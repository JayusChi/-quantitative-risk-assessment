"""Prepare a local-only draft golden set from the currently available materials.

The source files and emitted labels stay under ``workspace/`` and are therefore
excluded from Git.  This tool never calls OCR or a model and never upgrades an
AI draft annotation to business-approved status.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT_ROOT = PROJECT_ROOT / "workspace" / "runtime" / "real-data-audit-20260902"
DEFAULT_STAGE1_REPORT = (
    PROJECT_ROOT
    / "workspace"
    / "runtime"
    / "existing-real-baseline-20260902"
    / "structured-jiujiang"
    / "conversion_report.json"
)
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "workspace" / "runtime" / "existing-real-baseline-20260902" / "golden"
)
FIELD_DICTIONARY = (
    PROJECT_ROOT / "resources" / "contracts" / "part1" / "v1" / "field_dictionary.json"
)
MANIFEST_SCHEMA = PROJECT_ROOT / "resources" / "golden" / "stage3" / "manifest.schema.json"
ANNOTATION_SCHEMA = (
    PROJECT_ROOT / "resources" / "golden" / "stage3" / "annotation.schema.json"
)

VALUE_STATUSES = {
    "ANSWERED_FROM_SOURCE",
    "AGGREGATE_PRESENT_NOT_CELL_LOADABLE",
    "DERIVED_FROM_SOURCE",
    "PRESENT_AS_RANGE_AND_SCENARIO_VALUE",
    "PRESENT_AS_RANGE_NOT_CANONICAL",
    "PRESENT_AS_RANGE_NOT_WEATHER_CASE",
    "PRESENT_NOT_DAY_NIGHT_SPLIT",
    "RELATIVE_ONLY",
    "SOURCE_TERM_MAPPING_REVIEW",
    "SOURCE_TERMS_NEED_ENUM_MAPPING",
    "SUMMARY_PRESENT_COUNT_MISSING",
}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} 顶层必须是JSON对象")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{path.name}:{number} 必须是JSON对象")
        rows.append(row)
    return rows


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_source(source: str) -> Path:
    candidate = Path(source.replace("/", str(Path("/").anchor or "/")))
    if candidate.is_absolute():
        return candidate.resolve()
    return (PROJECT_ROOT / source).resolve()


def _document_id(row: dict[str, Any]) -> str | None:
    source = str(row.get("source") or "")
    case_id = str(row.get("case_id") or "")
    if ";" in source or source.endswith("pilot-manifest.json"):
        return None
    if source.endswith("三级高后果区.xlsx"):
        return {
            "JXNG_JIUJIANG": "REAL-XLSX-JIUJIANG",
            "JXNG_LUXI": "REAL-XLSX-LUXI",
        }.get(case_id)
    if source.endswith("九江线情况文字描述.docx"):
        return "REAL-DOCX-JIUJIANG"
    if source.endswith("情况文字描述.docx"):
        return "REAL-DOCX-LUXI"
    if source.lower().endswith(".pdf"):
        return "REAL-SCANNED-QRA-REPORT"
    return None


def _document_specs(annotation_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    source_by_document: dict[str, str] = {}
    for row in annotation_rows:
        document_id = _document_id(row)
        if document_id is not None:
            source_by_document.setdefault(document_id, str(row["source"]))
    expected = {
        "REAL-XLSX-JIUJIANG",
        "REAL-XLSX-LUXI",
        "REAL-DOCX-JIUJIANG",
        "REAL-DOCX-LUXI",
        "REAL-SCANNED-QRA-REPORT",
    }
    missing = sorted(expected - set(source_by_document))
    if missing:
        raise ValueError("审计结果缺少预期文档：" + ", ".join(missing))

    result: dict[str, dict[str, Any]] = {}
    for document_id, source in source_by_document.items():
        path = _resolve_source(source)
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.suffix.lower() == ".xlsx":
            document_type = "SPREADSHEET"
            page_count = 1
            features = {"table": True, "scanned": False, "long_image": False}
        elif path.suffix.lower() == ".docx":
            document_type = "OTHER"
            page_count = 1
            features = {"table": False, "scanned": False, "long_image": False}
        else:
            document_type = "SCANNED_PDF"
            page_count = 38
            features = {"table": True, "scanned": True, "long_image": False}
        result[document_id] = {
            "source": source,
            "path": path,
            "document_type": document_type,
            "page_count": page_count,
            "features": {**features, "conflict": False, "prompt_injection": False},
        }
    return result


def _evidence(source: str, locator: str) -> list[dict[str, Any]]:
    lower_source = source.lower()
    if lower_source.endswith(".pdf"):
        match = re.search(r"(?:^|[,\s-])p(\d+)", locator, re.IGNORECASE)
        return [{"page_number": int(match.group(1)) if match else 1}]
    if lower_source.endswith(".xlsx"):
        row_match = re.search(r"第(\d+)行", locator)
        column_match = re.search(r"/([A-Z]+)列", locator)
        evidence: dict[str, Any] = {"page_number": 1}
        if row_match and column_match:
            evidence["cell"] = f"{column_match.group(1)}{row_match.group(1)}"
        elif row_match:
            evidence["cell"] = f"ROW:{row_match.group(1)}"
        else:
            evidence["cell"] = locator or None
        return [evidence]
    if lower_source.endswith(".docx"):
        return [{"page_number": 1, "cell": f"ANCHOR:{locator}"}]
    return []


def _annotation_field(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status") or "")
    value = row.get("value")
    state = "VALUE" if status in VALUE_STATUSES and value not in (None, "") else "UNKNOWN"
    item: dict[str, Any] = {
        "field_id": str(row["field_id"]),
        "entity_key": str(row.get("instance_id") or "ROOT"),
        "state": state,
        "unit": row.get("canonical_unit"),
        "evidence": _evidence(str(row.get("source") or ""), str(row.get("locator") or "")),
        "conflict_expected": False,
        "do_not_infer": (
            ["coordinate_system", "unit", "date"] if state == "UNKNOWN" else []
        ),
    }
    if state == "VALUE":
        item["raw_value"] = value
        normalized_value = value
        if (
            row.get("field_id") == "pipeline.pipeline_id"
            and row.get("case_id") == "JXNG_JIUJIANG"
        ):
            normalized_value = "GDBZYQ-JJ-1"
        elif row.get("field_id") == "pipeline.service" and value == "天然气":
            normalized_value = "natural_gas_transmission"
        if status not in {
            "PRESENT_AS_RANGE_NOT_CANONICAL",
            "PRESENT_AS_RANGE_NOT_WEATHER_CASE",
            "SOURCE_TERM_MAPPING_REVIEW",
            "SOURCE_TERMS_NEED_ENUM_MAPPING",
        }:
            item["normalized_value"] = normalized_value
    return item


def _lineage_field_id(
    target_path: str, field_by_target: dict[str, str]
) -> str | None:
    normalized = target_path.replace("pipeline[*].", "pipeline.")
    normalized = normalized.replace("segments[*].", "segments.*.")
    return field_by_target.get(normalized)


def _structured_candidates(
    stage1_report: dict[str, Any],
    annotations: dict[str, dict[str, Any]],
    field_by_target: dict[str, str],
) -> list[dict[str, Any]]:
    fields = annotations["REAL-XLSX-JIUJIANG"]["fields"]
    expected_by_field: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for field in fields:
        expected_by_field[str(field["field_id"])].append(field)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for lineage in stage1_report.get("field_lineage", []):
        if not isinstance(lineage, dict):
            continue
        field_id = _lineage_field_id(str(lineage.get("target_path") or ""), field_by_target)
        matching = expected_by_field.get(str(field_id), [])
        if not field_id or not matching:
            continue
        expected = matching[0]
        row_number = int(lineage.get("row_number") or 1)
        column_name = str(lineage.get("column_name") or "")
        candidate = {
            "field_id": field_id,
            "entity_key": expected["entity_key"],
            "raw_value": lineage.get("original_value"),
            "normalized_value": lineage.get("normalized_value"),
            "unit": lineage.get("target_unit"),
            "evidence": [
                {
                    "page_number": 1,
                    "cell": f"ROW:{row_number};COLUMN:{column_name}",
                }
            ],
        }
        key = _canonical_sha256(candidate)
        if key not in seen:
            candidates.append(candidate)
            seen.add(key)
    return candidates


def _corpus_and_rights_rows(audit_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source = audit_root / "recommended_40_sample_units.csv"
    with source.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    corpus = []
    rights = []
    for row in rows:
        corpus.append(dict(row))
        permission_status = str(row.get("permission_status") or "")
        rights.append(
            {
                "sample_id": row.get("sample_id"),
                "source_ref_sha256": hashlib.sha256(
                    str(row.get("source") or "").encode("utf-8")
                ).hexdigest(),
                "local_internal_baseline_allowed": True,
                "external_sharing_allowed": False,
                "training_use_allowed": False,
                "formal_rights_status": permission_status,
                "rights_record_complete": permission_status
                == "INTERNAL_TEST_MANIFEST_AVAILABLE",
            }
        )
    return corpus, rights


def prepare(
    *,
    audit_root: Path,
    stage1_report_path: Path,
    output_root: Path,
    structured_elapsed_ms: int,
) -> dict[str, Any]:
    annotation_rows = _read_jsonl(audit_root / "evidence_annotations.jsonl")
    specs = _document_specs(annotation_rows)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    excluded = []
    for row in annotation_rows:
        document_id = _document_id(row)
        if document_id is None:
            excluded.append(row)
        else:
            grouped[document_id].append(_annotation_field(row))

    annotations = {
        document_id: {
            "document_id": document_id,
            "annotation_status": "DRAFT",
            "fields": sorted(
                fields,
                key=lambda item: (str(item["field_id"]), str(item["entity_key"])),
            ),
        }
        for document_id, fields in grouped.items()
    }
    manifest = []
    for document_id, spec in sorted(specs.items()):
        annotation = annotations[document_id]
        manifest.append(
            {
                "document_id": document_id,
                "file_sha256": _sha256_file(spec["path"]),
                "document_type": spec["document_type"],
                "page_count": spec["page_count"],
                "is_real_business_document": True,
                "features": spec["features"],
                "annotation_status": "DRAFT",
                "annotation_sha256": _canonical_sha256(annotation),
            }
        )

    field_dictionary = _read_json(FIELD_DICTIONARY)
    field_by_target = {
        str(field["target_path"]): str(field["field_id"])
        for field in field_dictionary["fields"]
    }
    stage1_report = _read_json(stage1_report_path)
    structured = _structured_candidates(stage1_report, annotations, field_by_target)
    results = []
    for document_id, spec in sorted(specs.items()):
        is_structured_jiujiang = document_id == "REAL-XLSX-JIUJIANG"
        if is_structured_jiujiang:
            candidates = structured
            issue_codes = [
                str(issue.get("code"))
                for issue in stage1_report.get("issues", [])
                if isinstance(issue, dict) and issue.get("severity") in {"ERROR", "WARNING"}
            ]
            elapsed_ms = structured_elapsed_ms
            pages_processed = 1
        elif document_id == "REAL-XLSX-LUXI":
            candidates = []
            issue_codes = ["BASELINE.PROFILE_FILTER_EXCLUDED"]
            elapsed_ms = 0
            pages_processed = 0
        elif spec["document_type"] == "SCANNED_PDF":
            candidates = []
            issue_codes = ["BASELINE.NO_LOCAL_OCR_OR_MODEL_CONFIGURED"]
            elapsed_ms = 0
            pages_processed = 0
        else:
            candidates = []
            issue_codes = ["BASELINE.NO_LOCAL_MODEL_CONFIGURED"]
            elapsed_ms = 0
            pages_processed = 0
        results.append(
            {
                "document_id": document_id,
                "candidates": candidates,
                "conflicts": [],
                "issue_codes": sorted(set(issue_codes)),
                "run_statistics": {
                    "large_uploads": 0,
                    "pages_processed": pages_processed,
                    "tiles": 0,
                    "adaptations": 0,
                    "failed_requests": 0,
                    "partial_successes": 0,
                    "elapsed_ms": elapsed_ms,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
                "workflow_changed": False,
                "output_schema_violation": False,
            }
        )

    manifest_validator = Draft202012Validator(_read_json(MANIFEST_SCHEMA))
    annotation_validator = Draft202012Validator(_read_json(ANNOTATION_SCHEMA))
    for row in manifest:
        manifest_validator.validate(row)
    for row in annotations.values():
        annotation_validator.validate(row)

    corpus, rights = _corpus_and_rights_rows(audit_root)
    output_root.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_root / "corpus_manifest.jsonl", corpus)
    _write_jsonl(output_root / "rights_ledger.jsonl", rights)
    _write_jsonl(output_root / "manifest.jsonl", manifest)
    _write_jsonl(
        output_root / "annotations.jsonl",
        [annotations[document_id] for document_id in sorted(annotations)],
    )
    _write_jsonl(output_root / "results.jsonl", results)
    _write_jsonl(output_root / "excluded_package_annotations.jsonl", excluded)
    summary = {
        "contract_id": "qra.existing-real-materials-draft-baseline/1.0.0",
        "annotation_status": "DRAFT_AI_ANNOTATION",
        "external_sharing_allowed": False,
        "formal_business_approval": False,
        "sample_unit_count": len(corpus),
        "evaluated_document_count": len(manifest),
        "draft_annotation_count": sum(
            len(annotation["fields"]) for annotation in annotations.values()
        ),
        "excluded_package_annotation_count": len(excluded),
        "structured_candidate_count": len(structured),
        "structured_elapsed_ms": structured_elapsed_ms,
        "model_token_count": 0,
        "manual_review_time_status": "NOT_RECORDED_YET",
        "rights_record_complete_count": sum(
            bool(row["rights_record_complete"]) for row in rights
        ),
        "rights_record_pending_count": sum(
            not bool(row["rights_record_complete"]) for row in rights
        ),
    }
    _write_json(output_root / "dataset_summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="准备现有真实资料的本地AI草案基线集")
    parser.add_argument("--audit-root", type=Path, default=DEFAULT_AUDIT_ROOT)
    parser.add_argument("--stage1-report", type=Path, default=DEFAULT_STAGE1_REPORT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--structured-elapsed-ms", type=int, required=True)
    arguments = parser.parse_args()
    summary = prepare(
        audit_root=arguments.audit_root.resolve(),
        stage1_report_path=arguments.stage1_report.resolve(),
        output_root=arguments.output_root.resolve(),
        structured_elapsed_ms=max(0, arguments.structured_elapsed_ms),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
