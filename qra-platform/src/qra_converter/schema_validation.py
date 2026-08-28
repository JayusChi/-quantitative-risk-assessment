"""JSON Schema validation adapted to the converter's stable issue contract."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .contract_catalog import ContractCatalog
from .contracts import ConversionIssue, IssueSeverity


def json_path(parts: Iterable[Any]) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            token = str(part)
            path += f".{token}" if token.isidentifier() else f"[{token!r}]"
    return path


def _keyword_code(keyword: str) -> str:
    return {
        "required": "CONTRACT.SCHEMA.REQUIRED",
        "additionalProperties": "CONTRACT.SCHEMA.UNKNOWN_FIELD",
        "type": "CONTRACT.SCHEMA.TYPE",
        "enum": "CONTRACT.SCHEMA.ENUM",
        "minimum": "CONTRACT.SCHEMA.RANGE",
        "maximum": "CONTRACT.SCHEMA.RANGE",
        "exclusiveMinimum": "CONTRACT.SCHEMA.RANGE",
        "exclusiveMaximum": "CONTRACT.SCHEMA.RANGE",
        "minItems": "CONTRACT.SCHEMA.MIN_ITEMS",
        "uniqueItems": "CONTRACT.SCHEMA.DUPLICATE",
        "format": "CONTRACT.SCHEMA.FORMAT",
        "pattern": "CONTRACT.SCHEMA.PATTERN",
        "oneOf": "CONTRACT.SCHEMA.ONE_OF",
        "anyOf": "CONTRACT.SCHEMA.ANY_OF",
    }.get(keyword, "CONTRACT.SCHEMA.INVALID")


def _non_finite_issues(value: Any, parts: tuple[Any, ...] = ()) -> list[ConversionIssue]:
    if isinstance(value, float) and not math.isfinite(value):
        return [
            ConversionIssue(
                IssueSeverity.ERROR,
                "CONTRACT.NON_FINITE_NUMBER",
                "数值不得为NaN或Infinity",
                target_path=json_path(parts),
            )
        ]
    issues: list[ConversionIssue] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            issues.extend(_non_finite_issues(item, (*parts, key)))
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            issues.extend(_non_finite_issues(item, (*parts, index)))
    return issues


def validate_schema_document(
    value: Any,
    *,
    catalog: ContractCatalog,
    schema_name: str,
) -> tuple[ConversionIssue, ...]:
    """Return deterministic converter issues instead of raising validation errors."""

    schema = catalog.read_schema(schema_name)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    issues = _non_finite_issues(value)
    errors = sorted(
        validator.iter_errors(value),
        key=lambda error: (tuple(str(item) for item in error.absolute_path), error.message),
    )
    for error in errors:
        issues.append(
            ConversionIssue(
                IssueSeverity.ERROR,
                _keyword_code(str(error.validator)),
                error.message,
                target_path=json_path(error.absolute_path),
            )
        )
    unique: dict[tuple[str, str, str], ConversionIssue] = {}
    for issue in issues:
        key = (issue.code, issue.target_path or "$", issue.message)
        unique[key] = issue
    return tuple(unique[key] for key in sorted(unique))


def _qra_semantic_issues(value: Any) -> tuple[ConversionIssue, ...]:
    if not isinstance(value, dict):
        return ()
    issues: list[ConversionIssue] = []
    seen_segments: set[str] = set()
    for index, segment in enumerate(value.get("segments") or []):
        if not isinstance(segment, dict):
            continue
        segment_id = segment.get("segment_id")
        if isinstance(segment_id, str) and segment_id in seen_segments:
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    "CONTRACT.SEGMENT_ID_DUPLICATE",
                    f"管段业务主键重复：{segment_id}",
                    target_path=f"$.segments[{index}].segment_id",
                )
            )
        elif isinstance(segment_id, str):
            seen_segments.add(segment_id)
        start = segment.get("start_km")
        end = segment.get("end_km")
        if (
            isinstance(start, int | float)
            and not isinstance(start, bool)
            and math.isfinite(float(start))
            and isinstance(end, int | float)
            and not isinstance(end, bool)
            and math.isfinite(float(end))
            and float(end) <= float(start)
        ):
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    "NORMALIZE.CHAINAGE_INVALID",
                    "管段终点里程必须大于起点里程",
                    target_path=f"$.segments[{index}]",
                )
            )

    frequency_library = value.get("frequency_library")
    if isinstance(frequency_library, dict) and frequency_library.get("unit") not in {
        None,
        "per_km_year",
    }:
        issues.append(
            ConversionIssue(
                IssueSeverity.ERROR,
                "NORMALIZE.UNIT_UNSUPPORTED",
                "失效频率计算合同只接受per_km_year",
                target_path="$.frequency_library.unit",
            )
        )

    weather = value.get("weather_joint_probability")
    if isinstance(weather, list) and weather:
        probabilities = [
            row.get("probability") if isinstance(row, dict) else None for row in weather
        ]
        if all(
            isinstance(item, int | float)
            and not isinstance(item, bool)
            and math.isfinite(float(item))
            for item in probabilities
        ) and not math.isclose(
            sum(float(item) for item in probabilities), 1.0, rel_tol=0.0, abs_tol=1.0e-9
        ):
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    "CONTRACT.PROBABILITY_NOT_NORMALIZED",
                    "气象联合概率之和必须为1",
                    target_path="$.weather_joint_probability",
                )
            )

    pipeline = value.get("pipeline")
    if isinstance(pipeline, dict):
        design = pipeline.get("design_pressure_mpa")
        operating = pipeline.get("operating_pressure_mpa")
        if (
            isinstance(design, int | float)
            and not isinstance(design, bool)
            and isinstance(operating, int | float)
            and not isinstance(operating, bool)
            and math.isfinite(float(design))
            and math.isfinite(float(operating))
            and float(operating) > float(design)
        ):
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    "CONTRACT.OPERATING_PRESSURE_EXCEEDS_DESIGN",
                    "运行压力不得高于设计压力",
                    target_path="$.pipeline.operating_pressure_mpa",
                )
            )
    return tuple(issues)


def validate_qra_input(value: Any, *, catalog: ContractCatalog) -> tuple[ConversionIssue, ...]:
    issues = [
        *validate_schema_document(value, catalog=catalog, schema_name="qra-input"),
        *_qra_semantic_issues(value),
    ]
    unique: dict[tuple[str, str, str], ConversionIssue] = {}
    for issue in issues:
        key = (issue.code, issue.target_path or "$", issue.message)
        unique[key] = issue
    return tuple(unique[key] for key in sorted(unique))


__all__ = ["json_path", "validate_qra_input", "validate_schema_document"]
