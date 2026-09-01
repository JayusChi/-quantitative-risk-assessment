"""Explicit, resumable stage-four workflow; model output never controls transitions."""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..contract_catalog import ContractCatalog
from ..extraction.classifier import (
    consolidate_classifications,
    deterministic_classification,
    fallback_classification,
    mark_ambiguity,
)
from ..extraction.entities import finalize_entities
from ..extraction.fields import (
    chunk_document_blocks,
    deterministic_candidates_from_lineage,
    evidence_from_documents,
    field_index,
    model_candidates,
)
from ..extraction.output_validation import (
    DOCUMENT_CATEGORIES,
    ENTITY_TYPES,
    RELATION_TYPES,
    detect_untrusted_instructions,
    validate_structured_output,
)
from ..extraction.ports import (
    ExtractionProvider,
    ExtractionRequest,
    ProviderCallError,
    ProviderExecutor,
)
from ..extraction.prompts import (
    SYSTEM_POLICY_VERSION,
    UNTRUSTED_CONTENT_NOTICE,
    load_prompt_bundle,
)
from ..extraction.relationships import finalize_relationships
from ..fusion import fuse_candidates
from ..normalization import normalize_candidates
from ..parsing.contracts import ParsedDocument
from ..quality import (
    candidate_capability_plan,
    completeness_issues,
    extraction_metrics,
    validate_candidate_quality,
)
from ..validation import validate_conversion_quality
from .contracts import Stage4Result, StepResult, WorkflowStatus
from .state import InMemoryWorkflowStore, WorkflowStore

PROMPT_ROOT = Path(__file__).resolve().parents[3] / "resources" / "extraction" / "part1" / "v1"


class Stage4Cancelled(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_json(item) for item in value]
    return value


def _model_issue(code: str, message: str, *, blocking: bool = False) -> dict[str, Any]:
    return {
        "issue_id": "ISS-" + hashlib.sha256(f"{code}\0{message}".encode()).hexdigest()[:24],
        "code": code,
        "quality_status": "INVALID" if blocking else "WARNING",
        "target": "extraction",
        "message": message,
        "candidate_ids": [],
        "evidence_ids": [],
        "blocking": blocking,
    }


def _task_schema(
    task_type: str,
    *,
    evidence_ids: tuple[str, ...],
    source_ids: tuple[str, ...],
    field_ids: tuple[str, ...],
    entity_ids: tuple[str, ...],
) -> dict[str, Any]:
    def enum_string(values: tuple[str, ...] | list[str]) -> dict[str, Any]:
        return (
            {"type": "string", "enum": list(values)}
            if values
            else {"type": "string"}
        )

    string_or_null = {"type": ["string", "null"]}
    object_or_null = {"type": ["object", "null"]}
    unconstrained_value = {
        "type": ["string", "number", "integer", "boolean", "object", "array", "null"]
    }
    evidence_array = {
        "type": "array",
        "items": enum_string(evidence_ids),
    }
    common = {
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_ids": evidence_array,
    }
    if task_type == "CLASSIFY":
        properties = {
            "classification_id": string_or_null,
            "source_id": enum_string(source_ids),
            "primary_category": {
                "type": "string",
                "enum": sorted(DOCUMENT_CATEGORIES),
            },
            "secondary_categories": {
                "type": "array",
                "items": {"type": "string", "enum": sorted(DOCUMENT_CATEGORIES)},
            },
            **common,
            "pipelines": {"type": "array", "items": {"type": "string"}},
            "segments": {"type": "array", "items": {"type": "string"}},
            "chainage_range": object_or_null,
            "document_date": string_or_null,
            "version_clues": {"type": "array", "items": {"type": "string"}},
        }
        required = [
            "source_id",
            "primary_category",
            "secondary_categories",
            "confidence",
            "evidence_ids",
        ]
    elif task_type == "EXTRACT_ENTITIES":
        properties = {
            "entity_id": {"type": "string", "pattern": "^ENT-[A-Za-z0-9._-]+$"},
            "entity_type": {"type": "string", "enum": sorted(ENTITY_TYPES)},
            "raw_name": {"type": "string"},
            "normalized_name": string_or_null,
            "business_key": string_or_null,
            "time_range": object_or_null,
            "chainage_range": object_or_null,
            "coordinate_range": object_or_null,
            **common,
            "source_id": enum_string(source_ids),
        }
        required = [
            "entity_id",
            "entity_type",
            "raw_name",
            "confidence",
            "evidence_ids",
            "source_id",
        ]
    elif task_type == "EXTRACT_FIELDS":
        properties = {
            "candidate_id": string_or_null,
            "field_id": enum_string(field_ids),
            "entity_id": enum_string(entity_ids),
            "raw_value": unconstrained_value,
            "source_unit": string_or_null,
            "normalized_value": unconstrained_value,
            **common,
            "not_found": {"type": "boolean"},
            "effective_from": string_or_null,
            "effective_to": string_or_null,
        }
        required = [
            "field_id",
            "entity_id",
            "raw_value",
            "confidence",
            "evidence_ids",
            "not_found",
        ]
    elif task_type == "EXTRACT_RELATIONSHIPS":
        properties = {
            "relationship_id": string_or_null,
            "relation_type": {"type": "string", "enum": sorted(RELATION_TYPES)},
            "source_entity_id": enum_string(entity_ids),
            "target_entity_id": enum_string(entity_ids),
            **common,
            "derived_rule": string_or_null,
        }
        required = [
            "relation_type",
            "source_entity_id",
            "target_entity_id",
            "confidence",
            "evidence_ids",
        ]
    else:
        raise ValueError(f"不支持的模型任务：{task_type}")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                **(
                    {"minItems": 1}
                    if task_type == "EXTRACT_FIELDS" and field_ids and entity_ids
                    else {}
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": properties,
                    "required": required,
                },
            }
        },
        "x-untrusted-content-policy": SYSTEM_POLICY_VERSION,
        "x-tools-allowed": [],
    }


class Stage4Workflow:
    def __init__(
        self,
        *,
        catalog: ContractCatalog,
        provider: ExtractionProvider | None = None,
        store: WorkflowStore | None = None,
        prompt_root: Path | str = PROMPT_ROOT,
        max_retries: int = 2,
    ) -> None:
        self.catalog = catalog
        self.provider = provider
        self.store = store or InMemoryWorkflowStore()
        self.prompts = load_prompt_bundle(prompt_root)
        self.executor = (
            ProviderExecutor(
                provider,
                max_retries=int(getattr(provider, "max_retries", max_retries)),
                max_concurrency=int(getattr(provider, "max_concurrency", 4)),
            )
            if provider is not None
            else None
        )
        self._provider_enabled_for_run = True
        self.fields = field_index(catalog.field_dictionary)
        unit_path = catalog.root / "unit_registry.json"
        self.unit_registry = json.loads(unit_path.read_text(encoding="utf-8"))
        self._active_evidence: dict[str, dict[str, Any]] = {}

    def _register_sub_evidence(self, blocks: tuple[dict[str, Any], ...]) -> None:
        for block in blocks:
            parent_id = str(block.get("parent_evidence_id") or "")
            evidence_id = str(block.get("evidence_id") or "")
            if not parent_id or not evidence_id or evidence_id in self._active_evidence:
                continue
            parent = self._active_evidence.get(parent_id)
            if parent is None:
                continue
            self._active_evidence[evidence_id] = {
                **parent,
                "evidence_id": evidence_id,
                "excerpt": str(block.get("text") or "")[:500],
                "checksum_sha256": _sha256(
                    [
                        parent.get("checksum_sha256"),
                        block.get("character_start"),
                        block.get("character_end"),
                    ]
                ),
                "parent_evidence_id": parent_id,
                "character_start": block.get("character_start"),
                "character_end": block.get("character_end"),
            }

    def _check_cancel(self, job_id: str, callback: Callable[[], None] | None) -> None:
        if callback is not None:
            callback()
        if self.store.is_cancel_requested(job_id):
            raise Stage4Cancelled("第四阶段任务已取消")

    def _step(
        self,
        job_id: str,
        step: str,
        input_value: Any,
        callback: Callable[[], dict[str, Any]],
        *,
        cancel_check: Callable[[], None] | None,
    ) -> tuple[dict[str, Any], StepResult]:
        self._check_cancel(job_id, cancel_check)
        input_hash = _sha256(input_value)
        existing = self.store.load_step(job_id, step, input_hash)
        if existing is not None:
            return existing.output, existing
        started = _now()
        output = callback()
        finished = _now()
        result = StepResult(
            step=step,
            status="COMPLETED",
            input_sha256=input_hash,
            output_sha256=_sha256(output),
            output=output,
            started_at=started,
            finished_at=finished,
            retry_count=sum(
                int(item.get("retry_count", 0)) for item in output.get("model_calls", [])
            ),
        )
        self.store.save_step(job_id, result)
        return output, result

    def _request_items(
        self,
        *,
        job_id: str,
        task_type: str,
        blocks: tuple[dict[str, Any], ...],
        field_subset: tuple[str, ...],
        entities: tuple[dict[str, Any], ...],
        issues: list[dict[str, Any]],
        cancel_check: Callable[[], None] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        if self.executor is None or not self._provider_enabled_for_run or not blocks:
            return [], None
        self._check_cancel(job_id, cancel_check)
        request = self._build_request(
            job_id=job_id,
            task_type=task_type,
            blocks=blocks,
            field_subset=field_subset,
            entities=entities,
        )
        evidence_text = {str(item["evidence_id"]): str(item["text"]) for item in blocks}
        known_entities = {str(item["entity_id"]) for item in entities}
        schema = request.schema
        max_request_bytes = self._max_request_bytes()
        input_byte_count = self.executor.request_byte_count(request)
        if input_byte_count > max_request_bytes:
            self.executor.record_local_skip(
                request,
                error_code="EXTRACT.REQUEST_TOO_LARGE",
                input_byte_count=input_byte_count,
            )
            return [], {
                "task_type": task_type,
                "status": "SKIPPED",
                "error_code": "EXTRACT.REQUEST_TOO_LARGE",
                "retry_count": 0,
                "input_sha256": _sha256(request.to_dict()),
                "input_byte_count": input_byte_count,
            }
        try:
            response, retry_count = self.executor.call(request)
        except ProviderCallError as exc:
            issues.append(_model_issue(exc.code, str(exc), blocking=False))
            return [], {
                "task_type": task_type,
                "status": "FAILED",
                "error_code": exc.code,
                "retry_count": self.executor.max_retries if exc.retryable else 0,
                "input_sha256": _sha256(request.to_dict()),
                "input_byte_count": input_byte_count,
            }
        valid, validation_issues = validate_structured_output(
            task_type,
            response.structured_output,
            allowed_field_ids=set(field_subset),
            allowed_evidence=evidence_text,
            known_entity_ids=known_entities,
        )
        repair_count = 0
        if validation_issues and not valid:
            repair = ExtractionRequest(
                task_type=task_type,
                request_id=request.request_id + ":repair",
                system_policy_version=request.system_policy_version,
                prompt_template_version=request.prompt_template_version,
                schema=request.schema,
                field_subset=request.field_subset,
                document_blocks=request.document_blocks,
                instructions=request.instructions,
                field_definitions=request.field_definitions,
                entity_context=request.entity_context,
                timeout_seconds=request.timeout_seconds,
                repair_of_request_id=request.request_id,
                job_id=job_id,
                parent_call_id=request.request_id,
            )
            try:
                repaired, repaired_retries = self.executor.call(repair)
            except ProviderCallError:
                issues.extend(validation_issues)
            else:
                repair_count = 1
                retry_count += repaired_retries
                repaired_valid, repaired_issues = validate_structured_output(
                    task_type,
                    repaired.structured_output,
                    allowed_field_ids=set(field_subset),
                    allowed_evidence=evidence_text,
                    known_entity_ids=known_entities,
                )
                if repaired_valid:
                    response = repaired
                    valid = repaired_valid
                else:
                    issues.extend(validation_issues)
                    issues.extend(repaired_issues)
        elif validation_issues:
            issues.extend(validation_issues)
        audit = {
            "task_type": task_type,
            "status": "COMPLETED" if valid or not validation_issues else "STRUCTURE_FAILED",
            "provider_id": response.provider_id,
            "model_id": response.model_id,
            "model_version": response.model_version,
            "provider_request_id": response.provider_request_id,
            "raw_response_sha256": response.raw_response_sha256,
            "prompt_template_version": self.prompts.version,
            "prompt_manifest_sha256": self.prompts.manifest_sha256,
            "schema_sha256": _sha256(schema),
            "input_sha256": _sha256(request.to_dict()),
            "input_byte_count": input_byte_count,
            "retry_count": retry_count,
            "repair_count": repair_count,
            "usage": response.usage,
            "finish_reason": response.finish_reason,
        }
        return valid, audit

    @staticmethod
    def _max_request_bytes() -> int:
        try:
            value = int(os.environ.get("QRA_EXTRACTION_MAX_REQUEST_BYTES", "7500000"))
        except ValueError as exc:
            raise ValueError("QRA_EXTRACTION_MAX_REQUEST_BYTES必须是整数") from exc
        if not 128 * 1024 <= value <= 32 * 1024 * 1024:
            raise ValueError("QRA_EXTRACTION_MAX_REQUEST_BYTES超出安全范围")
        return value

    def _build_request(
        self,
        *,
        job_id: str,
        task_type: str,
        blocks: tuple[dict[str, Any], ...],
        field_subset: tuple[str, ...],
        entities: tuple[dict[str, Any], ...],
        parent_call_id: str | None = None,
    ) -> ExtractionRequest:
        evidence_text = {str(item["evidence_id"]): str(item["text"]) for item in blocks}
        known_entities = {str(item["entity_id"]) for item in entities}
        schema = _task_schema(
            task_type,
            evidence_ids=tuple(sorted(evidence_text)),
            source_ids=tuple(sorted({str(item["source_id"]) for item in blocks})),
            field_ids=tuple(sorted(field_subset)),
            entity_ids=tuple(sorted(known_entities)),
        )
        return ExtractionRequest(
            task_type=task_type,
            request_id=f"{job_id}:{task_type}:{_sha256(blocks)[:16]}",
            system_policy_version=SYSTEM_POLICY_VERSION,
            prompt_template_version=self.prompts.version,
            schema=schema,
            field_subset=field_subset,
            document_blocks=blocks,
            instructions=(
                UNTRUSTED_CONTENT_NOTICE
                + "\n\n"
                + self.prompts.templates[task_type]
            ),
            field_definitions=tuple(
                _plain_json(self.fields[field_id])
                for field_id in field_subset
                if field_id in self.fields
            ),
            entity_context=entities,
            timeout_seconds=float(
                getattr(self.provider, "default_timeout_seconds", 30.0)
            ),
            job_id=job_id,
            parent_call_id=parent_call_id,
        )

    def _request_batched(
        self,
        *,
        job_id: str,
        task_type: str,
        blocks: tuple[dict[str, Any], ...],
        field_subset: tuple[str, ...],
        entities: tuple[dict[str, Any], ...],
        issues: list[dict[str, Any]],
        cancel_check: Callable[[], None] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        items: dict[str, dict[str, Any]] = {}
        calls: list[dict[str, Any]] = []
        if self.executor is None or not blocks:
            return [], []
        max_bytes = self._max_request_bytes()

        def request_size(batch: tuple[dict[str, Any], ...]) -> int:
            request = self._build_request(
                job_id=job_id,
                task_type=task_type,
                blocks=batch,
                field_subset=field_subset,
                entities=entities,
            )
            return self.executor.request_byte_count(request)

        batches = chunk_document_blocks(
            blocks,
            max_characters=60_000,
            overlap_blocks=1,
            max_request_bytes=max_bytes,
            request_size=request_size,
        ) or ((),)
        queue: list[tuple[tuple[dict[str, Any], ...], tuple[str, ...], int]] = [
            (batch, field_subset, 0) for batch in batches
        ]
        failed_leaf_count = 0
        while queue:
            batch, fields, depth = queue.pop(0)
            self._register_sub_evidence(batch)
            if depth > 8:
                failed_leaf_count += 1
                calls.append(
                    {
                        "task_type": task_type,
                        "status": "FAILED",
                        "error_code": "EXTRACT.REQUEST_TOO_LARGE",
                        "retry_count": 0,
                        "input_sha256": _sha256([batch, fields]),
                    }
                )
                continue
            extracted, audit = self._request_items(
                job_id=job_id,
                task_type=task_type,
                blocks=batch,
                field_subset=fields,
                entities=entities,
                issues=issues,
                cancel_check=cancel_check,
            )
            if audit is not None:
                calls.append(audit)
            if audit and audit.get("error_code") == "EXTRACT.REQUEST_TOO_LARGE":
                parent_id = str(audit.get("input_sha256") or _sha256([batch, fields]))
                if len(batch) > 1:
                    middle = len(batch) // 2
                    queue[0:0] = [
                        (batch[:middle], fields, depth + 1),
                        (batch[middle:], fields, depth + 1),
                    ]
                    continue
                if len(fields) > 1:
                    middle = len(fields) // 2
                    queue[0:0] = [
                        (batch, fields[:middle], depth + 1),
                        (batch, fields[middle:], depth + 1),
                    ]
                    continue
                if batch:
                    text_length = len(str(batch[0].get("text") or ""))
                    smaller = chunk_document_blocks(
                        batch,
                        max_characters=max(128, text_length // 2),
                        overlap_blocks=0,
                    )
                    if len(smaller) > 1:
                        queue[0:0] = [
                            (
                                tuple(
                                    {
                                        **block,
                                        "split_parent_request_id": parent_id,
                                    }
                                    for block in child_batch
                                ),
                                fields,
                                depth + 1,
                            )
                            for child_batch in smaller
                        ]
                        continue
                failed_leaf_count += 1
                continue
            for item in extracted:
                items.setdefault(_sha256(item), item)
        if failed_leaf_count:
            issues.append(
                _model_issue(
                    "EXTRACT.PARTIAL",
                    f"{task_type}有{failed_leaf_count}个最小批次失败，已保留其他成功结果",
                    blocking=False,
                )
            )
        return list(items.values()), calls

    def _relevant_field_subset(
        self,
        blocks: tuple[dict[str, Any], ...],
        entities: list[dict[str, Any]],
        explicit: tuple[str, ...] | None,
    ) -> tuple[str, ...]:
        if explicit is not None:
            unknown = sorted(set(explicit) - set(self.fields))
            if unknown:
                raise ValueError("字段子集包含未知字段：" + ", ".join(unknown))
            return tuple(sorted(set(explicit)))
        text = "\n".join(str(block["text"]) for block in blocks)
        entity_types = {str(entity["entity_type"]) for entity in entities}
        selected = []
        for field_id, definition in self.fields.items():
            terms = [definition.get("name_zh"), *(definition.get("aliases_zh") or [])]
            mentioned = any(term and str(term) in text for term in terms)
            if mentioned or (
                definition.get("entity_type") in entity_types
                and definition.get("required_level") == "REQUIRED"
            ):
                selected.append(field_id)
        return tuple(sorted(selected[:80]))

    def run(
        self,
        *,
        job_id: str,
        documents: tuple[ParsedDocument, ...],
        lineage: tuple[Any, ...] = (),
        mapping_version: str,
        base_case: dict[str, Any] | None = None,
        engine_capability_plan: dict[str, Any] | None = None,
        declared_categories: dict[str, str] | None = None,
        field_subset: tuple[str, ...] | None = None,
        golden_candidates: list[dict[str, Any]] | None = None,
        external_sharing_allowed: bool = False,
        cancel_check: Callable[[], None] | None = None,
        audit_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> Stage4Result:
        if self.executor is not None:
            self.executor.audit_callback = audit_callback
        steps: list[StepResult] = []
        evidence_bundle = evidence_from_documents(documents)
        deterministic, deterministic_evidence = deterministic_candidates_from_lineage(
            tuple(lineage),
            field_dictionary=self.catalog.field_dictionary,
            mapping_version=mapping_version,
        )
        evidence_by_id = {item["evidence_id"]: item for item in evidence_bundle.evidence}
        evidence_by_id.update({item["evidence_id"]: item for item in deterministic_evidence})
        self._active_evidence = evidence_by_id
        base_issues: list[dict[str, Any]] = []
        if base_case is not None:
            for issue in validate_conversion_quality(base_case):
                code = (
                    issue.code
                    if issue.code.startswith(
                        (
                            "INTAKE.",
                            "PARSE.",
                            "EXTRACT.",
                            "NORMALIZE.",
                            "FUSION.",
                            "CONTRACT.",
                            "GATE.",
                            "DELIVERY.",
                        )
                    )
                    else f"CONTRACT.{issue.code}"
                )
                base_issues.append(
                    {
                        "issue_id": "ISS-"
                        + hashlib.sha256(
                            f"{code}\0{issue.target_path}\0{issue.message}".encode()
                        ).hexdigest()[:24],
                        "code": code,
                        "quality_status": (
                            "INVALID" if issue.severity.value == "ERROR" else issue.severity.value
                        ),
                        "blocking": issue.severity.value == "ERROR",
                        "target": str(issue.target_path or issue.location or "$"),
                        "message": issue.message,
                        "candidate_ids": [],
                        "evidence_ids": [],
                    }
                )
        self._provider_enabled_for_run = True
        if (
            self.provider is not None
            and str(getattr(self.provider, "deployment_scope", "EXTERNAL")).upper() != "LOCAL"
        ):
            self._provider_enabled_for_run = bool(external_sharing_allowed)
            if not self._provider_enabled_for_run:
                base_issues.append(
                    _model_issue(
                        "EXTRACT.EXTERNAL_CALL_BLOCKED",
                        "资料未获外发许可，已跳过外部模型并保留确定性候选",
                        blocking=False,
                    )
                )
        elif self.provider is None and evidence_bundle.blocks:
            base_issues.append(
                _model_issue(
                    "EXTRACT.PROVIDER_NOT_CONFIGURED",
                    "未配置大模型信息提取提供方，已保留确定性候选并将未覆盖资料交由复核",
                    blocking=False,
                )
            )
        detected = detect_untrusted_instructions(evidence_bundle.blocks)
        if detected:
            issue = _model_issue(
                "EXTRACT.UNTRUSTED_INSTRUCTION_DETECTED",
                "资料包含指令性文字，已按普通不可信文本处理",
                blocking=False,
            )
            issue["quality_status"] = "INFO"
            issue["evidence_ids"] = detected
            base_issues.append(issue)

        blocks_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for block in evidence_bundle.blocks:
            blocks_by_source[str(block["source_id"])].append(block)

        def classify() -> dict[str, Any]:
            issues = list(base_issues)
            classifications: list[dict[str, Any]] = []
            calls = []
            for document in documents:
                source_blocks = tuple(blocks_by_source.get(document.source.source_id, []))
                declared = (declared_categories or {}).get(document.source.source_id)
                fixed = deterministic_classification(
                    document.source.source_id,
                    declared,
                    [item["evidence_id"] for item in source_blocks],
                )
                if fixed is not None:
                    classifications.append(fixed)
                    continue
                items, audits = self._request_batched(
                    job_id=job_id,
                    task_type="CLASSIFY",
                    blocks=source_blocks,
                    field_subset=(),
                    entities=(),
                    issues=issues,
                    cancel_check=cancel_check,
                )
                calls.extend(audits)
                if items:
                    for item in items:
                        item.setdefault("source_id", document.source.source_id)
                        item["model_version"] = audits[-1].get("model_version") if audits else None
                    classifications.extend(items)
                else:
                    classifications.append(
                        fallback_classification(
                            document.source.source_id,
                            [item["evidence_id"] for item in source_blocks],
                        )
                    )
                    issues.append(
                        _model_issue(
                            "EXTRACT.CLASSIFICATION_AMBIGUOUS",
                            f"资料{document.source.source_id}无法可靠分类",
                            blocking=False,
                        )
                    )
            marked = mark_ambiguity(consolidate_classifications(classifications))
            for item in marked:
                if item.get("requires_review") and not any(
                    issue.get("code") == "EXTRACT.CLASSIFICATION_AMBIGUOUS"
                    and str(item.get("source_id")) in str(issue.get("message"))
                    for issue in issues
                ):
                    issues.append(
                        _model_issue(
                            "EXTRACT.CLASSIFICATION_AMBIGUOUS",
                            f"资料{item.get('source_id')}主类别置信度不足或类别接近",
                            blocking=False,
                        )
                    )
            return {
                "classifications": marked,
                "issues": issues,
                "model_calls": calls,
            }

        classified, step = self._step(
            job_id,
            "CLASSIFYING",
            {
                "documents": [document.parse_sha256 for document in documents],
                "prompt": self.prompts.manifest_sha256,
                "declared": declared_categories or {},
            },
            classify,
            cancel_check=cancel_check,
        )
        steps.append(step)

        def extract_entities() -> dict[str, Any]:
            issues = list(classified["issues"])
            items, audits = self._request_batched(
                job_id=job_id,
                task_type="EXTRACT_ENTITIES",
                blocks=evidence_bundle.blocks,
                field_subset=(),
                entities=(),
                issues=issues,
                cancel_check=cancel_check,
            )
            entities = finalize_entities(items)
            known_keys = {str(entity.get("business_key")) for entity in entities}
            for candidate in deterministic:
                entity = candidate["entity"]
                entity_key = str(entity["entity_key"])
                if entity_key in known_keys:
                    continue
                entity_id = "ENT-" + hashlib.sha256(entity_key.encode()).hexdigest()[:20]
                entities.append(
                    {
                        "entity_id": entity_id,
                        "entity_type": entity["entity_type"],
                        "raw_name": entity_key,
                        "normalized_name": entity_key.casefold(),
                        "business_key": entity_key,
                        "time_range": None,
                        "chainage_range": None,
                        "coordinate_range": None,
                        "evidence_ids": list(candidate.get("evidence_ids") or []),
                        "confidence": 1.0,
                        "source_id": evidence_by_id[candidate["evidence_ids"][0]]["location"][
                            "file_id"
                        ],
                        "identity_status": "DETERMINISTIC_TEMPORARY",
                    }
                )
                known_keys.add(entity_key)
            return {
                "entities": finalize_entities(entities),
                "issues": issues,
                "model_calls": audits,
            }

        entity_output, step = self._step(
            job_id,
            "EXTRACTING_ENTITIES",
            {"classified": classified, "blocks": evidence_bundle.blocks},
            extract_entities,
            cancel_check=cancel_check,
        )
        steps.append(step)
        entities = list(entity_output["entities"])
        selected_fields = self._relevant_field_subset(
            evidence_bundle.blocks, entities, field_subset
        )

        def extract_fields() -> dict[str, Any]:
            issues = list(entity_output["issues"])
            items, audits = self._request_batched(
                job_id=job_id,
                task_type="EXTRACT_FIELDS",
                blocks=evidence_bundle.blocks,
                field_subset=selected_fields,
                entities=tuple(entities),
                issues=issues,
                cancel_check=cancel_check,
            )
            entity_lookup = {str(item["entity_id"]): item for item in entities}
            versions = {
                "provider": str(audits[-1].get("provider_id")) if audits else "none",
                "model": str(audits[-1].get("model_version")) if audits else "none",
                "prompt": self.prompts.version,
                "schema": self.catalog.version,
            }
            model = model_candidates(items, entities=entity_lookup, model_versions=versions)
            return {
                "candidates": [*deterministic, *model],
                "issues": issues,
                "model_calls": audits,
                "field_subset": list(selected_fields),
            }

        candidate_output, step = self._step(
            job_id,
            "EXTRACTING_FIELDS",
            {
                "entities": entities,
                "field_subset": selected_fields,
                "deterministic": deterministic,
                "blocks": evidence_bundle.blocks,
            },
            extract_fields,
            cancel_check=cancel_check,
        )
        steps.append(step)

        def normalize() -> dict[str, Any]:
            candidates, normalize_issues = normalize_candidates(
                list(candidate_output["candidates"]),
                fields=self.fields,
                unit_registry=self.unit_registry,
            )
            return {
                "candidates": candidates,
                "issues": [*candidate_output["issues"], *normalize_issues],
                "model_calls": [],
            }

        normalized, step = self._step(
            job_id,
            "NORMALIZING",
            candidate_output,
            normalize,
            cancel_check=cancel_check,
        )
        steps.append(step)

        def relationships() -> dict[str, Any]:
            issues = list(normalized["issues"])
            items, audits = self._request_batched(
                job_id=job_id,
                task_type="EXTRACT_RELATIONSHIPS",
                blocks=evidence_bundle.blocks,
                field_subset=(),
                entities=tuple(entities),
                issues=issues,
                cancel_check=cancel_check,
            )
            return {
                "relationships": finalize_relationships(items),
                "issues": issues,
                "model_calls": audits,
            }

        relation_output, step = self._step(
            job_id,
            "EXTRACTING_RELATIONSHIPS",
            {"entities": entities, "blocks": evidence_bundle.blocks},
            relationships,
            cancel_check=cancel_check,
        )
        steps.append(step)

        def fuse() -> dict[str, Any]:
            rebound, groups, fusion_issues = fuse_candidates(
                list(normalized["candidates"]), fields=self.fields
            )
            return {
                "candidates": rebound,
                "fusion_groups": groups,
                "issues": [*relation_output["issues"], *fusion_issues],
                "model_calls": [],
            }

        fused, step = self._step(
            job_id,
            "FUSING",
            {
                "candidates": normalized["candidates"],
                "relationships": relation_output["relationships"],
            },
            fuse,
            cancel_check=cancel_check,
        )
        steps.append(step)

        def quality() -> dict[str, Any]:
            issues = [
                *fused["issues"],
                *validate_candidate_quality(
                    list(fused["candidates"]),
                    list(relation_output["relationships"]),
                    entities,
                    fields=self.fields,
                ),
                *completeness_issues(list(fused["candidates"]), fields=self.fields),
            ]
            capability = candidate_capability_plan(
                list(fused["candidates"]),
                list(fused["fusion_groups"]),
                fields=self.fields,
                engine_plan=engine_capability_plan,
            )
            metrics = extraction_metrics(
                list(fused["candidates"]), issues, golden_candidates=golden_candidates
            )
            return {
                "issues": issues,
                "capability_plan": capability,
                "metrics": metrics,
                "model_calls": [],
            }

        quality_output, step = self._step(
            job_id,
            "QUALITY_CHECKING",
            {
                "candidates": fused["candidates"],
                "relationships": relation_output["relationships"],
                "fusion_groups": fused["fusion_groups"],
                "engine_plan": engine_capability_plan or {},
            },
            quality,
            cancel_check=cancel_check,
        )
        steps.append(step)
        blocked = any(bool(issue.get("blocking")) for issue in quality_output["issues"])
        final_status = WorkflowStatus.BLOCKED if blocked else WorkflowStatus.READY_FOR_REVIEW
        return Stage4Result(
            job_id=job_id,
            status=final_status,
            classifications=tuple(classified["classifications"]),
            entities=tuple(entities),
            candidates=tuple(fused["candidates"]),
            evidence=tuple(evidence_by_id[key] for key in sorted(evidence_by_id)),
            relationships=tuple(relation_output["relationships"]),
            fusion_groups=tuple(fused["fusion_groups"]),
            issues=tuple(quality_output["issues"]),
            metrics=dict(quality_output["metrics"]),
            capability_plan=dict(quality_output["capability_plan"]),
            steps=tuple(steps),
            state_history=(
                WorkflowStatus.PARSED.value,
                WorkflowStatus.CLASSIFYING.value,
                WorkflowStatus.CLASSIFIED.value,
                WorkflowStatus.EXTRACTING_ENTITIES.value,
                WorkflowStatus.ENTITIES_READY.value,
                WorkflowStatus.EXTRACTING_FIELDS.value,
                WorkflowStatus.CANDIDATES_READY.value,
                WorkflowStatus.NORMALIZING.value,
                WorkflowStatus.NORMALIZED.value,
                WorkflowStatus.FUSING.value,
                WorkflowStatus.FUSION_READY.value,
                WorkflowStatus.QUALITY_CHECKING.value,
                final_status.value,
            ),
        )

    def reextract_field(
        self,
        *,
        job_id: str,
        documents: tuple[ParsedDocument, ...],
        field_id: str,
        entities: tuple[dict[str, Any], ...],
        external_sharing_allowed: bool,
        audit_callback: Callable[[dict[str, Any]], None] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> Stage4Result:
        """Run only field extraction/normalization/fusion against current evidence."""

        if field_id not in self.fields:
            raise ValueError(f"未知重提取字段：{field_id}")
        if not entities:
            raise ValueError("字段重提取缺少现有实体，必须改用完整范围重提取")
        if self.executor is not None:
            self.executor.audit_callback = audit_callback
        self._provider_enabled_for_run = self.provider is not None and (
            str(getattr(self.provider, "deployment_scope", "EXTERNAL")).upper() == "LOCAL"
            or bool(external_sharing_allowed)
        )
        evidence_bundle = evidence_from_documents(documents)
        self._active_evidence = {
            str(item["evidence_id"]): item for item in evidence_bundle.evidence
        }
        model_blocks = list(evidence_bundle.blocks)
        represented = {str(item["evidence_id"]) for item in model_blocks}
        for item in evidence_bundle.evidence:
            if item["evidence_id"] in represented or item.get("source_type") != "TABLE":
                continue
            location = item.get("location") or {}
            model_blocks.append(
                {
                    "evidence_id": item["evidence_id"],
                    "source_id": str(location.get("file_id") or ""),
                    "content_type": "TABLE_CELL",
                    "text": str(item.get("excerpt") or ""),
                }
            )
        field_blocks = tuple(model_blocks)
        issues: list[dict[str, Any]] = []
        detected = detect_untrusted_instructions(field_blocks)
        if detected:
            issue = _model_issue(
                "EXTRACT.UNTRUSTED_INSTRUCTION_DETECTED",
                "资料包含指令性文字，字段重提取仍按普通不可信文本处理",
                blocking=False,
            )
            issue["quality_status"] = "INFO"
            issue["evidence_ids"] = detected
            issues.append(issue)
        if not self._provider_enabled_for_run:
            issues.append(
                _model_issue(
                    "EXTRACT.PROVIDER_NOT_CONFIGURED",
                    "字段重提取未调用模型，当前证据和旧候选保持可读",
                    blocking=False,
                )
            )
            items: list[dict[str, Any]] = []
            audits: list[dict[str, Any]] = []
        else:
            items, audits = self._request_batched(
                job_id=job_id,
                task_type="EXTRACT_FIELDS",
                blocks=field_blocks,
                field_subset=(field_id,),
                entities=entities,
                issues=issues,
                cancel_check=cancel_check,
            )
        entity_lookup = {str(item["entity_id"]): item for item in entities}
        versions = {
            "provider": str(audits[-1].get("provider_id")) if audits else "none",
            "model": str(audits[-1].get("model_version")) if audits else "none",
            "prompt": self.prompts.version,
            "schema": self.catalog.version,
        }
        candidates = model_candidates(items, entities=entity_lookup, model_versions=versions)
        normalized, normalize_issues = normalize_candidates(
            candidates, fields=self.fields, unit_registry=self.unit_registry
        )
        fused, groups, fusion_issues = fuse_candidates(normalized, fields=self.fields)
        issues.extend(normalize_issues)
        issues.extend(fusion_issues)
        issues.extend(
            validate_candidate_quality(
                fused,
                [],
                list(entities),
                fields=self.fields,
            )
        )
        status = (
            WorkflowStatus.BLOCKED
            if any(bool(issue.get("blocking")) for issue in issues)
            else WorkflowStatus.READY_FOR_REVIEW
        )
        return Stage4Result(
            job_id=job_id,
            status=status,
            entities=entities,
            candidates=tuple(fused),
            evidence=tuple(self._active_evidence[key] for key in sorted(self._active_evidence)),
            fusion_groups=tuple(groups),
            issues=tuple(issues),
            metrics=extraction_metrics(fused, issues),
            capability_plan={},
            state_history=(
                WorkflowStatus.PARSED.value,
                WorkflowStatus.EXTRACTING_FIELDS.value,
                WorkflowStatus.CANDIDATES_READY.value,
                WorkflowStatus.NORMALIZING.value,
                WorkflowStatus.NORMALIZED.value,
                WorkflowStatus.FUSING.value,
                WorkflowStatus.FUSION_READY.value,
                status.value,
            ),
        )


__all__ = ["PROMPT_ROOT", "Stage4Cancelled", "Stage4Workflow"]
