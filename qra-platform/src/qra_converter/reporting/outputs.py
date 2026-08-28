from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..contracts import ConversionResult, RawTable
from ..mapping.mapper import MappingOutcome
from ..mapping.profile import MappingProfile

REPORT_SCHEMA_VERSION = "2.0.0"


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _source_manifest(
    raw_tables: tuple[RawTable, ...], profile: MappingProfile, converter_version: str
) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    for table in raw_tables:
        source = table.source
        entry = grouped.setdefault(
            source.source_id,
            {
                "source_id": source.source_id,
                "file_name": source.source_path,
                "file_sha256": source.checksum_sha256,
                "reader_id": source.reader_id,
                "sheets": [],
            },
        )
        entry["sheets"].append(
            {
                "sheet_name": table.sheet_name,
                "non_empty_row_count": len(table.rows),
                "extraction_method": table.extraction_method,
                "confidence": table.confidence,
                "requires_review": table.requires_review,
            }
        )
    sources = sorted(
        grouped.values(),
        key=lambda row: (row["file_name"].casefold(), row["file_sha256"]),
    )
    first_by_checksum: dict[str, str] = {}
    for source in sources:
        source["sheets"].sort(key=lambda row: row["sheet_name"].casefold())
        checksum = str(source["file_sha256"])
        source["duplicate_content_of"] = first_by_checksum.get(checksum)
        first_by_checksum.setdefault(checksum, str(source["source_id"]))
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "converter_version": converter_version,
        "mapping_profile": {
            "profile_id": profile.profile_id,
            "version": profile.version,
            "sha256": profile.checksum_sha256,
            "inherited_profiles": list(profile.inherited_profiles),
        },
        "source_count": len(sources),
        "sources": sources,
    }


def _capability_preview(plan: dict[str, Any] | None) -> dict[str, Any]:
    if plan is None:
        return {
            "available": False,
            "runnable_node_ids": [],
            "continue_data_requirements": [],
        }
    missing: dict[tuple[str, str], dict[str, str]] = {}
    for node in plan.get("plan", []):
        for item in node.get("missing_inputs", []):
            key = (str(item.get("path", "")), str(item.get("label_zh", "")))
            missing[key] = {"path": key[0], "label_zh": key[1]}
    return {
        "available": True,
        "runnable_node_ids": list(plan.get("runnable_node_ids", [])),
        "skipped_node_ids": list(plan.get("skipped_node_ids", [])),
        "continue_data_requirements": [missing[key] for key in sorted(missing)],
        "nodes": list(plan.get("plan", [])),
    }


def _segment_associations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    collections: list[tuple[str, list[Any]]] = [
        ("population_cells", list(payload.get("population_cells") or []))
    ]
    for category_id, category in sorted((payload.get("raw_data_categories") or {}).items()):
        if isinstance(category, dict):
            collections.append(
                (
                    f"raw_data_categories.{category_id}.records",
                    list(category.get("records") or []),
                )
            )
    result = []
    for target, rows in collections:
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or "segment_id" not in row:
                continue
            result.append(
                {
                    "target_path": f"{target}[{index}]",
                    "record_id": row.get("record_id") or row.get("target_id"),
                    "chainage_km": row.get("chainage_km"),
                    "segment_id": row.get("segment_id"),
                    "review_status": row.get("review_status"),
                    "source_ref": row.get("source_ref"),
                }
            )
    return result


def _conversion_preview(
    result: ConversionResult,
    raw_tables: tuple[RawTable, ...],
    outcome: MappingOutcome,
) -> dict[str, Any]:
    source_names = {table.source.source_id: table.source.source_path for table in raw_tables}
    recognized = [
        {
            "source_id": source_id,
            "file_name": source_names.get(source_id),
            "sheet_name": sheet_name,
            "mapping_table_id": table_id,
        }
        for source_id, sheet_name, table_id in outcome.matched_table_keys
    ]
    matched = {(row["source_id"], row["sheet_name"]) for row in recognized}
    unrecognized = [
        {
            "source_id": table.source.source_id,
            "file_name": table.source.source_path,
            "sheet_name": table.sheet_name,
            "extraction_method": table.extraction_method,
            "confidence": table.confidence,
            "row_count": len(table.rows),
        }
        for table in raw_tables
        if (table.source.source_id, table.sheet_name) not in matched and table.rows
    ]
    missing_codes = {
        "REQUIRED_COLUMN_MISSING",
        "REQUIRED_FIELD_MISSING",
        "REQUIRED_TABLE_MISSING",
        "SEGMENT_REFERENCE_MISSING",
    }
    capability = _capability_preview(result.capability_plan)
    capability["calculation_eligible"] = not result.is_blocked
    capability["blocked_by_review_ids"] = [
        item.review_id for item in result.review_items if item.blocking
    ]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "BLOCKED" if result.is_blocked else "READY_FOR_REVIEW",
        "recognized_tables": recognized,
        "unrecognized_tables": unrecognized,
        "recognized_fields": [row.to_dict() for row in result.lineage],
        "missing_fields": [
            {
                "code": issue.code,
                "message": issue.message,
                "location": issue.location,
                "target_path": issue.target_path,
            }
            for issue in result.issues
            if issue.code in missing_codes
        ],
        "conflicts": [
            item.to_dict() for item in result.review_items if item.kind == "SOURCE_VALUE_CONFLICT"
        ],
        "unit_conversions": [
            row.to_dict()
            for row in result.lineage
            if row.source_unit is not None
            and row.target_unit is not None
            and str(row.source_unit).casefold() != str(row.target_unit).casefold()
        ],
        "segment_associations": _segment_associations(result.payload),
        "manual_review": {
            "pending_count": len(result.review_items),
            "blocking_count": sum(item.blocking for item in result.review_items),
            "items": [item.to_dict() for item in result.review_items],
        },
        "capability": capability,
    }


def write_conversion_outputs(
    output_dir: Path,
    result: ConversionResult,
    raw_tables: tuple[RawTable, ...],
    profile: MappingProfile,
    converter_version: str,
    outcome: MappingOutcome,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    case_hash = canonical_json_sha256(result.payload)
    issue_counts = {severity: 0 for severity in ("ERROR", "WARNING", "INFO")}
    for issue in result.issues:
        issue_counts[issue.severity.value] += 1
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "converter_version": converter_version,
        "contract": {
            "contract_id": result.contract_id,
            "version": result.contract_version,
            "manifest_sha256": result.contract_sha256,
        },
        "mapping_profile": {
            "profile_id": profile.profile_id,
            "version": profile.version,
            "sha256": profile.checksum_sha256,
            "inherited_profiles": list(profile.inherited_profiles),
        },
        "status": "BLOCKED" if result.is_blocked else "READY_FOR_REVIEW",
        "contract_status": result.contract_status,
        "case_sha256": case_hash,
        "summary": {
            "segment_count": len(result.payload.get("segments", [])),
            "population_record_count": len(result.payload.get("population_cells", [])),
            "raw_record_count": sum(
                len(category.get("records", []))
                for category in result.payload.get("raw_data_categories", {}).values()
                if isinstance(category, dict)
            ),
            "lineage_count": len(result.lineage),
            "issue_counts": issue_counts,
            "pending_review_count": len(result.review_items),
            "blocking_review_count": sum(item.blocking for item in result.review_items),
            "review_audit_count": len(result.review_audit),
        },
        "issues": [
            {
                "severity": issue.severity.value,
                "code": issue.code,
                "message": issue.message,
                "source_id": issue.source_id,
                "location": issue.location,
                "target_path": issue.target_path,
            }
            for issue in result.issues
        ],
        "field_lineage": [row.to_dict() for row in result.lineage],
        "pending_review_items": [item.to_dict() for item in result.review_items],
        "review_audit": [entry.to_dict() for entry in result.review_audit],
        "review_decision_source": result.review_decision_source,
        "stage4": result.stage4_result,
    }
    manifest = _source_manifest(raw_tables, profile, converter_version)
    manifest["contract"] = {
        "contract_id": result.contract_id,
        "version": result.contract_version,
        "manifest_sha256": result.contract_sha256,
    }
    manifest["review_decision_source"] = result.review_decision_source
    preview = _conversion_preview(result, raw_tables, outcome)
    preview["stage4"] = (
        {
            "status": result.stage4_result.get("status"),
            "metrics": result.stage4_result.get("metrics", {}),
            "issue_count": len(result.stage4_result.get("issues", [])),
            "candidate_count": len(result.stage4_result.get("candidates", [])),
            "fusion_group_count": len(result.stage4_result.get("fusion_groups", [])),
        }
        if result.stage4_result is not None
        else {"status": "NOT_RUN"}
    )
    audit_document = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "PENDING" if result.review_items else "COMPLETE",
        "review_decision_source": result.review_decision_source,
        "pending_items": [item.to_dict() for item in result.review_items],
        "applied_decisions": [entry.to_dict() for entry in result.review_audit],
    }
    paths = {
        "case": output_dir / "case.json",
        "conversion_report": output_dir / "conversion_report.json",
        "source_manifest": output_dir / "source_manifest.json",
        "conversion_preview": output_dir / "conversion_preview.json",
        "review_audit": output_dir / "review_audit.json",
    }
    _write_json(paths["case"], result.payload)
    _write_json(paths["conversion_report"], report)
    _write_json(paths["source_manifest"], manifest)
    _write_json(paths["conversion_preview"], preview)
    _write_json(paths["review_audit"], audit_document)
    return {
        "status": report["status"],
        "case_sha256": case_hash,
        "output_directory": str(output_dir),
        "paths": {key: str(path) for key, path in paths.items()},
        "issue_counts": issue_counts,
        "pending_review_count": len(result.review_items),
    }


__all__ = [
    "REPORT_SCHEMA_VERSION",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "write_conversion_outputs",
]
