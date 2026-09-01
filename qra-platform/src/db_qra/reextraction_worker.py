"""Background execution for FILE, PAGE and FIELD reextraction requests."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from qra_converter.contract_catalog import load_contract_catalog
from qra_converter.extraction.aliyun_bailian import configured_extraction_provider
from qra_converter.extraction.ports import ExtractionProvider
from qra_converter.ocr.ports import OcrProvider
from qra_converter.orchestration.workflow import Stage4Workflow
from qra_converter.parsing.contracts import ParsedDocument, parsed_document_from_dict
from qra_converter.parsing.pipeline import ParsingPipeline, configured_ocr_provider
from qra_converter.parsing.quality import build_quality_report

from .database import QraDatabase, canonical_json, utc_now
from .paths import DEFAULT_RUNTIME_ROOT


def _safe_error(value: object) -> str:
    text = " ".join(str(value).split())
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED_SECRET]", text)
    text = re.sub(r"https?://\S+", "[REDACTED_URL]", text)
    text = re.sub(
        r"(?:[A-Za-z]:[\\/]|/)(?:[^\s:]+[\\/])*[^\s:]*",
        "[REDACTED_PATH]",
        text,
    )
    return text[:300]


def _has_content(document: ParsedDocument) -> bool:
    return bool(
        document.text_blocks
        or document.tables
        or any(page.text_blocks or page.table_ids for page in document.pages)
    )


def _merge_page(
    current: ParsedDocument, replacement: ParsedDocument, page_number: int
) -> ParsedDocument:
    if not 1 <= page_number <= current.page_count or page_number > len(replacement.pages):
        raise ValueError("页面重提取页码超出PDF范围")
    new_page = replacement.pages[page_number - 1]
    if new_page.classification == "NOT_SELECTED":
        raise ValueError("页面重提取没有返回目标页")
    pages = list(current.pages)
    pages[page_number - 1] = new_page
    tables = tuple(table for table in current.tables if table.page_number != page_number) + tuple(
        table for table in replacement.tables if table.page_number == page_number
    )
    images = tuple(image for image in current.images if image.page_number != page_number) + tuple(
        image for image in replacement.images if image.page_number == page_number
    )
    issues = tuple(issue for issue in current.issues if issue.page_number != page_number) + tuple(
        issue for issue in replacement.issues if issue.page_number in {None, page_number}
    )
    return replace(
        current,
        parser_version=replacement.parser_version,
        pages=tuple(pages),
        tables=tables,
        images=images,
        issues=issues,
        metadata={
            **current.metadata,
            "reextraction": {
                "scope": "PAGE",
                "page_number": page_number,
                "replacement_parse_sha256": replacement.parse_sha256,
            },
        },
        parse_sha256="",
    ).finalized()


def _artifacts(execution: Any, document: ParsedDocument) -> list[dict[str, Any]]:
    if not execution.artifact_dir:
        return []
    root = Path(str(execution.artifact_dir)).resolve()
    result = []
    quality = build_quality_report(document, 0)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == "parsed_document.json":
            content = (
                json.dumps(
                    document.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
                )
                + "\n"
            ).encode("utf-8")
        elif relative == "quality_report.json":
            content = (
                json.dumps(quality, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
        elif relative == "preview_manifest.json":
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["parse_sha256"] = document.parse_sha256
            content = (
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
        else:
            content = path.read_bytes()
        suffix = path.suffix.casefold()
        content_type = {
            ".json": "application/json",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }.get(suffix, "application/octet-stream")
        kind = {
            "parsed_document.json": "PARSED_DOCUMENT",
            "quality_report.json": "QUALITY_REPORT",
            "preview_manifest.json": "PREVIEW_MANIFEST",
        }.get(relative, "PREVIEW_RESOURCE")
        result.append(
            {
                "path": relative,
                "artifact_kind": kind,
                "content_type": content_type,
                "content": content,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    return result


class ReextractionWorker:
    def __init__(
        self,
        database: QraDatabase,
        *,
        runtime_root: Path | str = DEFAULT_RUNTIME_ROOT,
        ocr_provider: OcrProvider | None = None,
        extraction_provider: ExtractionProvider | None = None,
    ) -> None:
        self.database = database
        self.runtime_root = Path(runtime_root).resolve()
        self.ocr_provider = ocr_provider
        self.extraction_provider = extraction_provider

    def _start(self, request_id: str) -> dict[str, Any]:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM reextraction_request WHERE id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"重新提取请求不存在：{request_id}")
            if str(row["status"]) != "QUEUED":
                raise ValueError("重新提取请求不在排队状态")
            connection.execute(
                "UPDATE reextraction_request SET status = 'RUNNING', started_at = ? WHERE id = ?",
                (utc_now(), request_id),
            )
            self.database._record_event_in_connection(
                connection,
                event_type="REVIEW_REEXTRACTION_STARTED",
                entity_type="reextraction_request",
                entity_id=request_id,
                actor="reextraction-worker",
                detail={"scope": row["scope"], "conversion_job_id": row["conversion_job_id"]},
            )
        return dict(row)

    def _fail(self, request_id: str, exc: Exception) -> dict[str, Any]:
        error = {
            "code": "REEXTRACTION_EXECUTION_FAILED",
            "type": type(exc).__name__,
            "message": _safe_error(exc),
        }
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE reextraction_request
                SET status = 'FAILED', finished_at = ?, error_json = ?
                WHERE id = ? AND status = 'RUNNING'
                """,
                (utc_now(), canonical_json(error), request_id),
            )
            self.database._record_event_in_connection(
                connection,
                event_type="REVIEW_REEXTRACTION_FAILED",
                entity_type="reextraction_request",
                entity_id=request_id,
                actor="reextraction-worker",
                detail={"error_code": error["code"], "error_type": error["type"]},
            )
        return {"id": request_id, "status": "FAILED", "error": error}

    def _documents(self, job_id: str) -> dict[str, ParsedDocument]:
        result = {}
        for source in self.database.list_conversion_sources(job_id):
            source_id = str(source["id"])
            stored = self.database.get_conversion_parse_artifact(
                job_id, source_id, "parsed_document.json"
            )
            if stored is None:
                continue
            _, content = stored
            value = json.loads(content.decode("utf-8"))
            result[source_id] = parsed_document_from_dict(value)
        return result

    @staticmethod
    def _target_candidate_count(result: dict[str, Any], request: dict[str, Any]) -> int:
        evidence = {
            str(item["evidence_id"]): item for item in result.get("evidence", [])
        }
        scope = str(request["scope"])
        count = 0
        for candidate in result.get("candidates", []):
            if not candidate.get("evidence_ids"):
                continue
            if scope == "FIELD":
                matches = str(candidate.get("field_id")) == str(request["field_id"])
            else:
                matches = False
                for evidence_id in candidate.get("evidence_ids", []):
                    location = (evidence.get(str(evidence_id)) or {}).get("location") or {}
                    if str(location.get("file_id") or "") != str(request["source_id"]):
                        continue
                    if scope != "PAGE" or int(location.get("page") or 0) == int(
                        request["page_number"] or 0
                    ):
                        matches = True
                        break
            count += int(matches)
        return count

    def run(self, request_id: str) -> dict[str, Any]:
        try:
            request = self._start(request_id)
            job_id = str(request["conversion_job_id"])
            job = self.database.get_conversion_job(job_id)
            documents = self._documents(job_id)
            target_document = None
            artifact_values: list[dict[str, Any]] = []
            scope = str(request["scope"])
            if scope in {"FILE", "PAGE"}:
                source_id = str(request["source_id"])
                current = documents.get(source_id)
                if current is None:
                    raise ValueError("目标源文件没有可复用的当前解析版本")
                sources = {
                    str(item["id"]): item
                    for item in self.database.conversion_source_contents(job_id)
                }
                source = sources.get(source_id)
                if source is None:
                    raise ValueError("目标受保护原文件不存在")
                self.runtime_root.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(
                    prefix=f"{request_id}-", dir=self.runtime_root
                ) as temporary:
                    root = Path(temporary)
                    suffix = Path(str(source["file_name"])).suffix.casefold()
                    source_path = root / f"source{suffix}"
                    source_path.write_bytes(bytes(source["content"]))
                    pipeline = ParsingPipeline(
                        output_root=root / "output",
                        cache_root=self.runtime_root / "reextraction-cache",
                        ocr_provider=self.ocr_provider
                        or configured_ocr_provider(model_version=job.get("ocr_model_version")),
                        audit_callback=self.database.record_model_call_audit,
                        job_id=job_id,
                    )
                    execution = pipeline.parse_path(
                        source_path,
                        detected_media_type=str(
                            source.get("detected_media_type") or source.get("media_type") or ""
                        )
                        or None,
                        source=current.source,
                        selected_pages=(
                            frozenset({int(request["page_number"])})
                            if scope == "PAGE"
                            else None
                        ),
                    )
                    target_document = (
                        _merge_page(current, execution.document, int(request["page_number"]))
                        if scope == "PAGE"
                        else execution.document
                    )
                    if not _has_content(target_document):
                        raise ValueError("目标范围重解析没有获得任何可复核内容")
                    artifact_values = _artifacts(execution, target_document)
                documents[source_id] = target_document

            catalog = load_contract_catalog(
                Path(str(job["contract_path"])),
                expected_contract_id=str(job["contract_id"]),
                expected_version=str(job["contract_version"]),
                expected_manifest_sha256=str(job["contract_sha256"]),
            )
            provider = self.extraction_provider or configured_extraction_provider(
                model_version=job.get("extraction_model_version")
            )
            workflow = Stage4Workflow(catalog=catalog, provider=provider)
            current_documents = tuple(
                documents[key] for key in sorted(documents) if _has_content(documents[key])
            )
            if scope == "FIELD":
                with self.database.session() as connection:
                    entity_rows = connection.execute(
                        """
                        SELECT payload_json FROM extracted_entity
                        WHERE job_id = ? ORDER BY entity_id
                        """,
                        (job_id,),
                    ).fetchall()
                entities = tuple(json.loads(str(row["payload_json"])) for row in entity_rows)
                requested_entity = str(request.get("entity_id") or "")
                if requested_entity:
                    matched_entities = tuple(
                        entity
                        for entity in entities
                        if requested_entity
                        in {
                            str(entity.get("entity_id") or ""),
                            str(entity.get("business_key") or ""),
                        }
                    )
                    if matched_entities:
                        entities = matched_entities
                    else:
                        with self.database.session() as connection:
                            candidate_row = connection.execute(
                                """
                                SELECT entity_type FROM candidate_field
                                WHERE job_id = ? AND entity_key = ? AND field_id = ?
                                ORDER BY candidate_id LIMIT 1
                                """,
                                (job_id, requested_entity, request["field_id"]),
                            ).fetchone()
                            evidence_rows = connection.execute(
                                """
                                SELECT DISTINCT link.evidence_id
                                FROM candidate_evidence_link AS link
                                JOIN candidate_field AS candidate
                                  ON candidate.job_id = link.job_id
                                 AND candidate.candidate_id = link.candidate_id
                                WHERE candidate.job_id = ? AND candidate.entity_key = ?
                                  AND candidate.field_id = ?
                                ORDER BY link.evidence_id
                                """,
                                (job_id, requested_entity, request["field_id"]),
                            ).fetchall()
                        if candidate_row is not None:
                            entities = (
                                {
                                    "entity_id": "ENT-REXT-"
                                    + hashlib.sha256(requested_entity.encode()).hexdigest()[:16],
                                    "entity_type": str(candidate_row["entity_type"]),
                                    "raw_name": requested_entity,
                                    "normalized_name": requested_entity.casefold(),
                                    "business_key": requested_entity,
                                    "time_range": None,
                                    "chainage_range": None,
                                    "coordinate_range": None,
                                    "evidence_ids": [
                                        str(row["evidence_id"]) for row in evidence_rows
                                    ],
                                    "confidence": 1.0,
                                    "source_id": str(request.get("source_id") or ""),
                                    "identity_status": "REEXTRACTION_TARGET",
                                },
                            )
                stage4 = workflow.reextract_field(
                    job_id=job_id,
                    documents=current_documents,
                    field_id=str(request["field_id"]),
                    entities=entities,
                    external_sharing_allowed=bool(job.get("external_sharing_allowed")),
                    audit_callback=self.database.record_model_call_audit,
                )
            else:
                stage4 = workflow.run(
                    job_id=job_id,
                    documents=current_documents,
                    mapping_version=f"{job['profile_id']}/{job['profile_version']}",
                    base_case=job.get("payload"),
                    external_sharing_allowed=bool(job.get("external_sharing_allowed")),
                    audit_callback=self.database.record_model_call_audit,
                )
            stage4_result = stage4.to_dict()
            stage4_result["result_sha256"] = stage4.sha256
            target_candidate_count = self._target_candidate_count(stage4_result, request)
            partial = bool(
                (target_document is not None and target_document.has_errors)
                or any(
                    str(issue.get("code")) == "EXTRACT.PARTIAL"
                    for issue in stage4_result.get("issues", [])
                )
                or target_candidate_count == 0
            )
            return self.database.apply_reextraction_result(
                request_id,
                document=target_document.to_dict() if target_document is not None else None,
                artifacts=artifact_values,
                stage4_result=stage4_result,
                actor="reextraction-worker",
                partial=partial,
                replace_candidates=target_candidate_count > 0,
            )
        except Exception as exc:
            return self._fail(request_id, exc)


__all__ = ["ReextractionWorker"]
