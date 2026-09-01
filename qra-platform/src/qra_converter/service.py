from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .assembly import assemble_case
from .contract_catalog import ContractCatalog, load_contract_catalog
from .contracts import (
    ConversionIssue,
    ConversionResult,
    IssueSeverity,
    RawTable,
    SourceReference,
)
from .extraction.ports import ExtractionProvider
from .mapping import ProfileMapper, load_profile
from .model_audit import ModelAuditCallback, sanitized_error_message
from .ocr.ports import OcrProvider
from .orchestration.state import WorkflowStore
from .orchestration.workflow import Stage4Workflow
from .parsing.compatibility import document_to_raw_tables
from .parsing.contracts import ParseExecution
from .parsing.pipeline import ParsingPipeline
from .parsing.registry import ParsingRegistry, file_sha256
from .reporting import write_conversion_outputs
from .review import auxiliary_review_items, load_review_bundle, merge_mapped_tables
from .schema_validation import validate_qra_input
from .validation import validate_conversion_quality

CONVERTER_VERSION = "0.6.0"
ContractValidator = Callable[[Any], Any]
CapabilityPlanner = Callable[[dict[str, Any]], dict[str, Any]]
ParseResultCallback = Callable[[Path, ParseExecution], None]
ParseProgressCallback = Callable[[int, int, int, int, str], None]
Stage4ResultCallback = Callable[[dict[str, Any]], None]


def discover_sources(source_dir: Path, registry: ParsingRegistry) -> tuple[Path, ...]:
    root = source_dir.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"源目录不存在：{source_dir}")
    return tuple(
        sorted(
            (
                path
                for path in root.rglob("*")
                if path.is_file() and path.suffix.lower() in registry.supported_suffixes
            ),
            key=lambda path: path.relative_to(root).as_posix().casefold(),
        )
    )


def _contract_issues(report: Any) -> tuple[list[ConversionIssue], str | None]:
    if report is None:
        return [], None
    status = str(getattr(report, "status", "UNKNOWN"))
    converted = []
    for issue in getattr(report, "issues", ()):
        severity_text = str(getattr(issue, "severity", "ERROR"))
        try:
            severity = IssueSeverity(severity_text)
        except ValueError:
            severity = IssueSeverity.ERROR
        converted.append(
            ConversionIssue(
                severity,
                f"IMPORT_CONTRACT.{getattr(issue, 'code', 'INVALID')}",
                str(getattr(issue, "message", "输入合同预检失败")),
                target_path=str(getattr(issue, "path", "$")),
            )
        )
    return converted, status


def _source_reference(
    path: Path,
    source_root: Path,
    metadata: dict[str, Any] | None,
) -> SourceReference:
    relative_path = path.resolve().relative_to(source_root.resolve()).as_posix()
    checksum = file_sha256(path)
    if metadata is not None:
        expected = str(metadata.get("sha256") or "")
        if expected and checksum != expected:
            raise ValueError(f"源文件{relative_path}哈希与安全登记不一致")
        source_id = str(metadata.get("id") or "").strip()
    else:
        source_id = ""
    if not source_id:
        source_id = hashlib.sha256(f"{checksum}\0{relative_path}".encode()).hexdigest()
    return SourceReference(
        source_id=source_id,
        source_path=relative_path,
        reader_id="pending",
        checksum_sha256=checksum,
        location=(
            f"archive:{metadata.get('archive_name')};member:{metadata.get('archive_member_path')}"
            if metadata and metadata.get("archive_name")
            else None
        ),
    )


def convert_sources(
    *,
    source_dir: Path,
    profile_path: Path,
    output_dir: Path,
    case_id: str | None = None,
    project_name: str | None = None,
    contract_validator: ContractValidator | None = None,
    capability_planner: CapabilityPlanner | None = None,
    review_decisions_path: Path | None = None,
    contract_dir: Path | None = None,
    legacy_contract_compatibility: bool = False,
    source_metadata: dict[str, dict[str, Any]] | None = None,
    parse_cache_dir: Path | None = None,
    ocr_provider: OcrProvider | None = None,
    cancel_check: Callable[[], None] | None = None,
    parse_result_callback: ParseResultCallback | None = None,
    parse_progress_callback: ParseProgressCallback | None = None,
    enable_stage4: bool = False,
    stage4_job_id: str | None = None,
    extraction_provider: ExtractionProvider | None = None,
    stage4_external_sharing_allowed: bool = False,
    stage4_store: WorkflowStore | None = None,
    stage4_result_callback: Stage4ResultCallback | None = None,
    model_audit_callback: ModelAuditCallback | None = None,
) -> dict[str, Any]:
    registry = ParsingRegistry()
    profile = load_profile(profile_path)
    contract_catalog: ContractCatalog | None = None
    if contract_dir is not None:
        if profile.contract_id is None or profile.contract_version is None:
            if not legacy_contract_compatibility:
                raise ValueError(
                    "映射配置未声明contract_id/contract_version；仅测试兼容模式允许继续"
                )
        else:
            contract_catalog = load_contract_catalog(
                contract_dir,
                expected_contract_id=profile.contract_id,
                expected_version=profile.contract_version,
                expected_manifest_sha256=profile.contract_sha256,
            )
    review_bundle = load_review_bundle(review_decisions_path)
    paths = discover_sources(source_dir, registry)
    raw_tables: list[RawTable] = []
    issues: list[ConversionIssue] = []
    parse_results: list[ParseExecution] = []
    if not paths:
        issues.append(
            ConversionIssue(
                IssueSeverity.ERROR,
                "SOURCE_FILES_EMPTY",
                "源目录中没有CSV、XLS、XLSX、DOCX、PDF、PNG或JPEG文件",
            )
        )
    current_index = 0

    def page_progress(page: int, page_total: int, message: str) -> None:
        if parse_progress_callback is not None:
            parse_progress_callback(current_index, len(paths), page, page_total, message)

    pipeline = ParsingPipeline(
        output_root=output_dir,
        cache_root=parse_cache_dir,
        registry=registry,
        ocr_provider=ocr_provider,
        cancel_check=cancel_check,
        page_progress=page_progress,
        audit_callback=model_audit_callback,
        job_id=stage4_job_id,
    )
    for path_index, path in enumerate(paths, start=1):
        current_index = path_index
        if cancel_check is not None:
            cancel_check()
        relative_path = path.resolve().relative_to(source_dir.resolve()).as_posix()
        metadata = (source_metadata or {}).get(relative_path)
        try:
            source = _source_reference(path, source_dir, metadata)
            detected_media_type = str(metadata.get("detected_media_type")) if metadata else None
            execution = pipeline.parse_path(
                path,
                detected_media_type=detected_media_type,
                source=source,
            )
            parse_results.append(execution)
            if parse_result_callback is not None:
                parse_result_callback(path, execution)
            for issue in execution.document.issues:
                issues.append(
                    ConversionIssue(
                        issue.severity,
                        issue.code,
                        issue.message,
                        source_id=execution.document.source.source_id,
                        location=issue.location
                        or (
                            f"page:{issue.page_number}"
                            if issue.page_number is not None
                            else relative_path
                        ),
                    )
                )
            if (
                execution.succeeded
                or execution.document.text_blocks
                or execution.document.tables
                or any(page.text_blocks or page.table_ids for page in execution.document.pages)
            ):
                raw_tables.extend(document_to_raw_tables(execution.document))
        except (OSError, RuntimeError, ValueError) as exc:
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    "SOURCE_READ_FAILED",
                    f"读取{path.name}失败：{exc}",
                    location=path.name,
                )
            )
    outcome = ProfileMapper(profile).map(raw_tables)
    issues.extend(outcome.issues)
    merge_result = merge_mapped_tables(outcome, review_bundle)
    issues.extend(merge_result.issues)
    auxiliary_items, auxiliary_audit, auxiliary_used = auxiliary_review_items(
        raw_tables, outcome.matched_table_keys, review_bundle
    )
    review_items = (*merge_result.review_items, *auxiliary_items)
    review_audit = (*merge_result.audit, *auxiliary_audit)
    used_review_ids = set(merge_result.used_review_ids) | set(auxiliary_used)
    for item in review_items:
        issues.append(
            ConversionIssue(
                IssueSeverity.WARNING,
                "MANUAL_REVIEW_PENDING",
                item.reason,
                target_path=item.target_path,
            )
        )
    for review_id in sorted(set(review_bundle.decisions) - used_review_ids):
        issues.append(
            ConversionIssue(
                IssueSeverity.ERROR,
                "REVIEW_DECISION_STALE",
                f"复核决定未匹配当前转换结果：{review_id}",
                target_path="review_decisions",
            )
        )
    case, assembly_issues = assemble_case(
        merge_result.outcome,
        case_id=case_id,
        project_name=project_name,
        fallback_project_name=source_dir.resolve().name,
    )
    issues.extend(assembly_issues)
    issues.extend(validate_conversion_quality(case))
    if profile.contract_id is None or profile.contract_version is None:
        issues.append(
            ConversionIssue(
                IssueSeverity.WARNING,
                "CONTRACT.LEGACY_PROFILE",
                "映射配置未绑定版本化第一部分合同，仅允许测试兼容使用",
                target_path="$",
            )
        )
    if contract_catalog is not None:
        issues.extend(validate_qra_input(case, catalog=contract_catalog))
    capability_plan: dict[str, Any] | None = None
    if capability_planner is not None:
        try:
            capability_plan = capability_planner(case)
        except Exception as exc:  # planner boundary must not hide conversion output
            issues.append(
                ConversionIssue(
                    IssueSeverity.WARNING,
                    "CAPABILITY_PLAN_FAILED",
                    f"计算能力盘点失败：{exc}",
                    target_path="$",
                )
            )
    contract_status: str | None = None
    if contract_validator is not None:
        try:
            contract_report = contract_validator(case)
        except Exception as exc:  # validator boundary must become a report issue
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    "IMPORT_CONTRACT_VALIDATOR_FAILED",
                    f"现有输入合同校验器执行失败：{exc}",
                    target_path="$",
                )
            )
            contract_status = "BLOCK"
        else:
            converted, contract_status = _contract_issues(contract_report)
            issues.extend(converted)
    stage4_document: dict[str, Any] | None = None
    if enable_stage4:
        if contract_catalog is None:
            raise ValueError("第四阶段必须使用已校验的第一部分版本化合同")
        effective_job_id = stage4_job_id or (
            "STAGE4-"
            + hashlib.sha256(
                (profile.checksum_sha256 + "\0" + "\0".join(
                    result.document.parse_sha256 for result in parse_results
                )).encode("utf-8")
            ).hexdigest()[:24]
        )
        try:
            stage4 = Stage4Workflow(
                catalog=contract_catalog,
                provider=extraction_provider,
                store=stage4_store,
            ).run(
                job_id=effective_job_id,
                documents=tuple(
                    result.document
                    for result in parse_results
                    if result.succeeded
                    or result.document.text_blocks
                    or result.document.tables
                    or any(page.text_blocks or page.table_ids for page in result.document.pages)
                ),
                lineage=outcome.lineage,
                mapping_version=f"{profile.profile_id}/{profile.version}",
                base_case=case,
                engine_capability_plan={
                    "contract_status": contract_status,
                    "dynamic_plan": capability_plan or {},
                },
                external_sharing_allowed=stage4_external_sharing_allowed,
                cancel_check=cancel_check,
                audit_callback=model_audit_callback,
            )
        except Exception as exc:
            if cancel_check is not None:
                cancel_check()
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    "EXTRACT.PARTIAL",
                    "字段提取工作流异常；解析与OCR产物已保留，可人工复核或重新提取。"
                    + sanitized_error_message(exc, limit=120),
                    target_path="stage4",
                )
            )
        else:
            stage4_document = stage4.to_dict()
            stage4_document["result_sha256"] = stage4.sha256
            if stage4_result_callback is not None:
                stage4_result_callback(stage4_document)
    unique_sources: dict[str, SourceReference] = {}
    for table in raw_tables:
        unique_sources[table.source.source_id] = table.source
    result = ConversionResult(
        payload=case,
        mapping_version=f"{profile.profile_id}/{profile.version}",
        contract_id=(contract_catalog.contract_id if contract_catalog else profile.contract_id),
        contract_version=(
            contract_catalog.version if contract_catalog else profile.contract_version
        ),
        contract_sha256=(contract_catalog.manifest_sha256 if contract_catalog else None),
        sources=tuple(
            sorted(
                unique_sources.values(),
                key=lambda source: (
                    source.source_path.casefold(),
                    source.checksum_sha256,
                ),
            )
        ),
        issues=tuple(issues),
        lineage=outcome.lineage,
        contract_status=contract_status,
        review_items=tuple(review_items),
        review_audit=tuple(review_audit),
        review_decision_source=review_bundle.source,
        capability_plan=capability_plan,
        stage4_result=stage4_document,
    )
    summary = write_conversion_outputs(
        output_dir,
        result,
        tuple(raw_tables),
        profile,
        CONVERTER_VERSION,
        outcome,
    )
    summary["parsing"] = {
        "source_count": len(parse_results),
        "succeeded_count": sum(result.succeeded for result in parse_results),
        "failed_count": sum(not result.succeeded for result in parse_results),
        "cache_hit_count": sum(result.cache_hit for result in parse_results),
        "page_count": sum(result.document.page_count for result in parse_results),
        "artifact_paths": [
            str(Path(result.artifact_dir).relative_to(output_dir).as_posix())
            for result in parse_results
            if result.artifact_dir
        ],
    }
    return summary


__all__ = ["CONVERTER_VERSION", "convert_sources", "discover_sources"]
