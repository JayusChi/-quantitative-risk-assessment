"""Apply deterministic normalizers to model suggestions and retain every raw value."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from .chainage import parse_chainage_km
from .dates import normalize_date
from .enums import normalize_enum
from .numbers import parse_number
from .units import conversion_formula, convert_unit, unit_allowed_for_field


def _issue(candidate_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "issue_id": f"ISS-{candidate_id}-{code.replace('.', '-')}",
        "code": code,
        "quality_status": "INVALID",
        "target": f"candidate:{candidate_id}",
        "message": message,
        "candidate_ids": [candidate_id],
        "evidence_ids": [],
        "blocking": True,
    }


def _normalize_one(
    candidate: dict[str, Any],
    definition: dict[str, Any],
    unit_registry: dict[str, Any],
) -> dict[str, Any]:
    row = dict(candidate)
    value_type = str(definition.get("value_type") or "string")
    raw = row.get("raw_value")
    source_unit = row.get("source_unit")
    canonical_unit = definition.get("canonical_unit") or row.get("canonical_unit")
    if canonical_unit is not None:
        row["canonical_unit"] = canonical_unit
    if row.get("extraction_method") == "STRUCTURED_TABLE":
        versions = dict(row.get("model_or_rule_versions") or {})
        versions["normalization"] = "mapping.values/1.0.0+qra.normalization/1.0.0"
        row["model_or_rule_versions"] = versions
        row["quality_status"] = "PASS"
        return row
    if value_type in {"number", "integer"}:
        if row["field_id"].endswith(("chainage_km", "start_km", "end_km")):
            value = parse_chainage_km(raw)
            row["parsed_value"] = float(value)
            row["normalized_value"] = float(value)
        else:
            numeric_input = raw
            if isinstance(raw, str) and source_unit:
                numeric_input = re.sub(rf"\s*{re.escape(str(source_unit))}\s*$", "", raw.strip())
            parsed = parse_number(numeric_input)
            parsed_value = parsed.json_value()
            row["parsed_value"] = parsed_value
            if isinstance(parsed_value, dict):
                row["normalized_value"] = parsed_value
            else:
                numeric = Decimal(str(parsed_value))
                if canonical_unit == "fraction" and source_unit is None:
                    raw_text = str(raw)
                    if "%" not in raw_text:
                        raise ValueError("百分数缺少%或列单位证据，不能推断是否除以100")
                    source_unit = "%"
                    row["source_unit"] = "%"
                if not unit_allowed_for_field(source_unit, row["field_id"], unit_registry):
                    raise ValueError(f"字段不允许单位：{source_unit}")
                converted = convert_unit(numeric, source_unit, canonical_unit)
                if value_type == "integer" and converted != converted.to_integral_value():
                    raise ValueError("整数型字段不能产生小数")
                row["normalized_value"] = (
                    int(converted)
                    if converted == converted.to_integral_value()
                    else float(converted)
                )
                formula = conversion_formula(source_unit, canonical_unit)
                if formula:
                    versions = dict(row.get("model_or_rule_versions") or {})
                    versions["normalization_formula"] = formula
                    row["model_or_rule_versions"] = versions
    elif value_type == "date":
        normalized = normalize_date(raw)
        row["parsed_value"] = normalized["value"]
        row["normalized_value"] = normalized["value"]
        versions = dict(row.get("model_or_rule_versions") or {})
        versions["date_precision"] = normalized["precision"]
        row["model_or_rule_versions"] = versions
    elif value_type == "boolean":
        if isinstance(raw, bool):
            normalized_bool = raw
        else:
            text = str(raw).strip().casefold()
            if text in {"true", "yes", "y", "是", "有", "1"}:
                normalized_bool = True
            elif text in {"false", "no", "n", "否", "无", "0"}:
                normalized_bool = False
            else:
                raise ValueError(f"无法解析布尔值：{raw}")
        row["parsed_value"] = normalized_bool
        row["normalized_value"] = normalized_bool
    elif value_type == "enum":
        allowed = list((definition.get("constraints") or {}).get("enum") or [])
        if not allowed:
            raise ValueError("枚举字段合同未声明允许值")
        normalized_enum = normalize_enum(raw, allowed)
        row["parsed_value"] = normalized_enum
        row["normalized_value"] = normalized_enum
    elif value_type == "string":
        normalized_text = " ".join(str(raw).split())
        row["parsed_value"] = normalized_text
        row["normalized_value"] = normalized_text
    else:
        row["parsed_value"] = raw
        row["normalized_value"] = raw
    threshold = 0.8 if definition.get("required_level") in {"REQUIRED", "CONDITIONAL"} else 0.65
    if float(row.get("confidence", 0.0)) < threshold:
        row["quality_status"] = "LOW_CONFIDENCE"
    else:
        row["quality_status"] = "PASS"
    versions = dict(row.get("model_or_rule_versions") or {})
    versions["normalization"] = "qra.normalization/1.0.0"
    row["model_or_rule_versions"] = versions
    return row


def normalize_candidates(
    candidates: list[dict[str, Any]],
    *,
    fields: dict[str, dict[str, Any]],
    unit_registry: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        definition = fields.get(str(candidate.get("field_id")))
        if definition is None:
            issues.append(_issue(candidate_id, "EXTRACT.FIELD_NOT_ALLOWED", "字段ID不在合同中"))
            continue
        try:
            normalized.append(_normalize_one(candidate, definition, unit_registry))
        except (TypeError, ValueError) as exc:
            row = dict(candidate)
            row["quality_status"] = "INVALID"
            row["normalized_value"] = None
            normalized.append(row)
            code = (
                "NORMALIZE.CHAINAGE_AMBIGUOUS"
                if candidate["field_id"].endswith(("chainage_km", "start_km", "end_km"))
                else "NORMALIZE.VALUE_INVALID"
            )
            issues.append(_issue(candidate_id, code, str(exc)))
    return normalized, issues


__all__ = ["normalize_candidates"]
