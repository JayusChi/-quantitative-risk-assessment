from __future__ import annotations

import copy
from typing import Any

from ..contracts import ConversionIssue, IssueSeverity
from ..mapping.mapper import MappingOutcome
from ..matching import attach_segments


def _without_helper_coordinates(record: dict[str, Any]) -> dict[str, Any]:
    result = _public_record(record)
    x_value = result.get("x_m")
    y_value = result.get("y_m")
    if x_value is not None and y_value is not None:
        result.pop("x_m")
        result.pop("y_m")
        result["xy_m"] = [x_value, y_value]
    return result


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("_")}


def _merge_pipeline(
    pipeline: dict[str, Any], records: list[dict[str, Any]], issues: list[ConversionIssue]
) -> None:
    source_refs = list(pipeline.get("source_refs", []))
    for record in records:
        source_ref = record.get("source_ref")
        record_source_refs = record.get("source_refs") or ([source_ref] if source_ref else [])
        for row in record_source_refs:
            if row not in source_refs:
                source_refs.append(row)
        for key, value in record.items():
            if key in {"source_ref", "source_refs", "quality", "review_status"} or key.startswith(
                "_"
            ):
                continue
            if key in pipeline and pipeline[key] != value:
                issues.append(
                    ConversionIssue(
                        IssueSeverity.ERROR,
                        "PIPELINE_VALUE_CONFLICT",
                        f"管线字段{key}存在冲突值：{pipeline[key]} 与 {value}",
                        source_ref.get("file_sha256") if source_ref else None,
                        (
                            f"{source_ref.get('file_name')}/{source_ref.get('sheet_name')}"
                            f"!R{source_ref.get('row_number')}"
                            if source_ref
                            else None
                        ),
                        f"pipeline.{key}",
                    )
                )
            else:
                pipeline[key] = value
    if source_refs:
        pipeline["source_refs"] = source_refs


def assemble_case(
    outcome: MappingOutcome,
    *,
    case_id: str | None = None,
    project_name: str | None = None,
    fallback_project_name: str = "自动转换案例",
) -> tuple[dict[str, Any], tuple[ConversionIssue, ...]]:
    case = copy.deepcopy(outcome.defaults)
    case.setdefault("schema_version", "1.0.0")
    metadata = case.setdefault("metadata", {})
    pipeline = case.setdefault("pipeline", {})
    segments: list[dict[str, Any]] = list(case.get("segments") or [])
    issues: list[ConversionIssue] = []

    if case_id:
        metadata["case_id"] = case_id
    if project_name:
        metadata["project_name"] = project_name
    if not metadata.get("case_id") and not metadata.get("project_name"):
        metadata["project_name"] = fallback_project_name
        issues.append(
            ConversionIssue(
                IssueSeverity.INFO,
                "METADATA_DERIVED",
                f"未提供案例标识，使用源目录名称：{fallback_project_name}",
                target_path="metadata.project_name",
            )
        )

    deferred: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for mapped_table in outcome.tables:
        definition = mapped_table.definition
        target = str(definition["target"])
        records = [dict(record) for record in mapped_table.records]
        if target == "segments":
            segments.extend(_public_record(record) for record in records)
        elif target == "pipeline":
            _merge_pipeline(pipeline, records, issues)
        else:
            deferred.append((definition, records))

    segments.sort(
        key=lambda row: (
            float(row.get("start_km", float("inf")))
            if isinstance(row.get("start_km"), int | float)
            else float("inf"),
            str(row.get("segment_id", "")),
        )
    )
    case["segments"] = segments

    raw_categories: dict[str, Any] = case.setdefault("raw_data_categories", {})
    population_cells: list[dict[str, Any]] = list(case.get("population_cells") or [])
    for definition, records in deferred:
        target = str(definition["target"])
        attach = bool(definition.get("attach_to_segment", False))
        if attach:
            records, matching_issues = attach_segments(records, segments, target_path=target)
            issues.extend(matching_issues)
        if target == "population_cells":
            population_cells.extend(_without_helper_coordinates(row) for row in records)
        elif target.startswith("raw_data_categories."):
            category_id = target.split(".", 1)[1]
            category = raw_categories.setdefault(
                category_id,
                {
                    "name_zh": str(definition.get("name_zh") or category_id),
                    "records": [],
                },
            )
            category["records"].extend(_public_record(record) for record in records)
        else:
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    "MAPPING_TARGET_UNSUPPORTED",
                    f"不支持的映射目标：{target}",
                    target_path=target,
                )
            )
        raw_category_id = definition.get("raw_category_id")
        if raw_category_id:
            category = raw_categories.setdefault(
                str(raw_category_id),
                {
                    "name_zh": str(definition.get("name_zh") or raw_category_id),
                    "records": [],
                },
            )
            category["records"].extend(_public_record(record) for record in records)

    if population_cells:
        case["population_cells"] = population_cells
    if not raw_categories:
        case.pop("raw_data_categories", None)

    categories: list[dict[str, Any]] = []
    if segments:
        categories.append(
            {
                "category_id": "segment_chainage",
                "name_zh": "管段编号、起止里程和长度",
                "record_count": len(segments),
            }
        )
    if any(key not in {"source_refs"} for key in pipeline):
        categories.append(
            {
                "category_id": "operating_conditions",
                "name_zh": "管线基础信息与运行工况",
                "record_count": 1,
            }
        )
    for category_id, category in sorted(raw_categories.items()):
        categories.append(
            {
                "category_id": category_id,
                "name_zh": str(category.get("name_zh") or category_id),
                "record_count": len(category.get("records", [])),
            }
        )
    case["data_category_manifest"] = {
        "category_count": len(categories),
        "categories": categories,
    }
    return case, tuple(issues)


__all__ = ["assemble_case"]
