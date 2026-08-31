"""Deterministic application of review decisions to a Part 1 QRA draft."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from qra_converter.normalization.service import normalize_candidates

from .database import json_sha256

ASSEMBLY_RULE_VERSION = "qra.review-assembly/1.0.0"


class ReviewAssemblyError(ValueError):
    """A review decision cannot be mapped to the immutable input contract."""


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def field_definitions(catalog: Any) -> dict[str, dict[str, Any]]:
    return {str(row["field_id"]): _thaw(row) for row in catalog.field_dictionary.get("fields", ())}


def normalize_manual_value(
    *,
    field_id: str,
    value: Any,
    unit: str | None,
    definition: dict[str, Any],
    unit_registry: dict[str, Any],
) -> Any:
    """Validate and normalize a typed manual value using the extraction normalizers."""

    if value is None:
        raise ReviewAssemblyError("手工修改值不能为空；缺失与不适用必须使用对应操作")
    candidate = {
        "candidate_id": "MANUAL-VALIDATION",
        "field_id": field_id,
        "entity_id": "MANUAL",
        "entity": {"entity_type": definition.get("entity_type", "UNKNOWN"), "entity_key": "MANUAL"},
        "raw_value": value,
        "source_unit": unit,
        "canonical_unit": definition.get("canonical_unit"),
        "normalized_value": None,
        "confidence": 1.0,
        "evidence_ids": [],
        "extraction_method": "MANUAL_REVIEW",
        "quality_status": "PENDING_REVIEW",
        "review_status": "PENDING",
        "model_or_rule_versions": {"manual_review": ASSEMBLY_RULE_VERSION},
    }
    normalized, issues = normalize_candidates(
        [candidate], fields={field_id: definition}, unit_registry=unit_registry
    )
    if issues or not normalized or normalized[0].get("quality_status") == "INVALID":
        message = issues[0].get("message") if issues else "手工值不符合字段合同"
        raise ReviewAssemblyError(str(message))
    result = normalized[0].get("normalized_value")
    constraints = dict(definition.get("constraints") or {})
    if isinstance(result, int | float) and not isinstance(result, bool):
        for key, comparator, label in (
            ("minimum", lambda a, b: a >= b, "最小值"),
            ("maximum", lambda a, b: a <= b, "最大值"),
            ("exclusiveMinimum", lambda a, b: a > b, "开区间最小值"),
            ("exclusiveMaximum", lambda a, b: a < b, "开区间最大值"),
        ):
            if key in constraints and not comparator(result, constraints[key]):
                raise ReviewAssemblyError(f"手工值不满足{label} {constraints[key]}")
    if isinstance(result, str):
        pattern = constraints.get("pattern")
        if pattern and re.fullmatch(str(pattern), result) is None:
            raise ReviewAssemblyError("手工值格式不符合字段合同")
        if constraints.get("minLength") is not None and len(result) < int(constraints["minLength"]):
            raise ReviewAssemblyError("手工值长度不足")
    return result


def _entity_hint(entity_key: str) -> str:
    parts = str(entity_key).split(":")
    if len(parts) >= 3 and parts[0] == "ID":
        return ":".join(parts[2:])
    return parts[-1]


def _identity_values(row: dict[str, Any]) -> set[str]:
    keys = (
        "segment_id",
        "pipeline_id",
        "target_id",
        "receptor_id",
        "record_id",
        "assessment_id",
        "id",
    )
    return {str(row[key]) for key in keys if row.get(key) is not None}


def _select_star(container: Any, entity_key: str) -> Any:
    hint = _entity_hint(entity_key)
    if isinstance(container, dict):
        for key in (hint, entity_key):
            if key in container:
                return container[key]
        if len(container) == 1:
            return next(iter(container.values()))
        raise ReviewAssemblyError(f"无法把实体 {entity_key} 映射到对象型通配路径")
    if isinstance(container, list):
        matches = [
            row for row in container if isinstance(row, dict) and hint in _identity_values(row)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(container) == 1 and isinstance(container[0], dict):
            return container[0]
        raise ReviewAssemblyError(f"无法把实体 {entity_key} 唯一映射到数组型通配路径")
    raise ReviewAssemblyError("字段目标路径的通配父节点不是对象或数组")


def _apply_target(
    payload: dict[str, Any],
    *,
    target_path: str,
    entity_key: str,
    value: Any,
    remove: bool = False,
) -> None:
    parts = [part for part in str(target_path).split(".") if part]
    if not parts:
        raise ReviewAssemblyError("字段合同缺少目标路径")
    current: Any = payload
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == "*":
            current = _select_star(current, entity_key)
            continue
        if not isinstance(current, dict):
            raise ReviewAssemblyError(f"目标路径 {target_path} 经过了非对象节点")
        if last:
            if remove:
                current.pop(part, None)
            else:
                current[part] = copy.deepcopy(value)
            return
        if part not in current:
            if remove:
                return
            current[part] = {}
        current = current[part]


def assemble_review_payload(
    *,
    base_payload: dict[str, Any],
    decisions: list[dict[str, Any]],
    candidates: dict[str, dict[str, Any]],
    definitions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Apply only current effective decisions in stable review-item order."""

    payload = copy.deepcopy(base_payload)
    for decision in sorted(decisions, key=lambda row: str(row["review_item_key"])):
        action = str(decision["action"])
        field_id = str(decision["field_id"])
        definition = definitions.get(field_id)
        if definition is None:
            raise ReviewAssemblyError(f"复核字段不在当前合同中：{field_id}")
        target_path = str(definition.get("target_path") or "")
        entity_key = str(decision.get("entity_id") or "GLOBAL")
        if action == "ACCEPT_CANDIDATE":
            candidate_id = str(decision.get("selected_candidate_id") or "")
            candidate = candidates.get(candidate_id)
            if candidate is None:
                raise ReviewAssemblyError(f"采用的候选已不存在：{candidate_id}")
            _apply_target(
                payload,
                target_path=target_path,
                entity_key=entity_key,
                value=candidate.get("normalized_value"),
            )
        elif action == "OVERRIDE_VALUE":
            _apply_target(
                payload,
                target_path=target_path,
                entity_key=entity_key,
                value=decision.get("override_normalized_value"),
            )
        elif action == "MARK_NOT_APPLICABLE":
            _apply_target(
                payload,
                target_path=target_path,
                entity_key=entity_key,
                value=None,
                remove=True,
            )
        elif action in {"REJECT_ALL", "REQUEST_REEXTRACTION"}:
            continue
        else:
            raise ReviewAssemblyError(f"不支持的复核操作：{action}")
    # canonical serialization is deliberately attempted here to reject NaN/Infinity.
    json_sha256(payload)
    return payload


__all__ = [
    "ASSEMBLY_RULE_VERSION",
    "ReviewAssemblyError",
    "assemble_review_payload",
    "field_definitions",
    "normalize_manual_value",
]
