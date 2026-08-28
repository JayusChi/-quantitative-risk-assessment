"""Reject model output outside the task contract, whitelist, or supplied evidence."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

ENTITY_TYPES = frozenset(
    {
        "PROJECT",
        "PIPELINE",
        "SEGMENT",
        "INSPECTION_RUN",
        "INSPECTION_POINT",
        "DEFECT",
        "REPAIR_ORDER",
        "VALVE",
        "LEAK_EVENT",
        "THIRD_PARTY_ACTIVITY",
        "GEOHAZARD",
        "POPULATION_CELL",
        "SENSITIVE_RECEPTOR",
        "WEATHER_CASE",
        "PARAMETER_LIBRARY",
        "APPROVAL_DOCUMENT",
    }
)
RELATION_TYPES = frozenset(
    {
        "BELONGS_TO",
        "LOCATED_IN",
        "MEASURED_AT",
        "APPLIES_TO_RANGE",
        "SUPERSEDES",
        "REPAIRS",
        "REFERENCES",
    }
)
DOCUMENT_CATEGORIES = frozenset(
    {
        "DESIGN_AS_BUILT",
        "PIPELINE_SEGMENT_REGISTER",
        "INTEGRITY_INSPECTION",
        "CONSTRUCTION_WELD_REPAIR",
        "THIRD_PARTY_PATROL",
        "GEOHAZARD",
        "OPERATING_EVENT",
        "VALVE_ISOLATION",
        "POPULATION_HCA_RECEPTOR",
        "WEATHER_TERRAIN_IGNITION_CONGESTION",
        "FREQUENCY_PARAMETER_APPROVAL",
        "OTHER_UNKNOWN",
    }
)

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INJECTION = re.compile(
    r"(?i)(ignore\s+(all\s+)?(previous|above)|system\s+prompt|developer\s+message|"
    r"tool\s*call|execute\s+(this\s+)?command|upload\s+.*https?://|"
    r"忽略.{0,12}(规则|指令|提示)|执行.{0,8}(命令|代码)|上传.{0,20}https?://|"
    r"你现在是|系统提示词|调用工具)"
)


def detect_untrusted_instructions(blocks: tuple[dict[str, Any], ...]) -> list[str]:
    return [str(block["evidence_id"]) for block in blocks if _INJECTION.search(str(block["text"]))]


def _depth(value: Any, level: int = 0) -> int:
    if isinstance(value, Mapping):
        return max([level, *(_depth(item, level + 1) for item in value.values())])
    if isinstance(value, list | tuple):
        return max([level, *(_depth(item, level + 1) for item in value)])
    return level


def _safe_text(value: Any) -> bool:
    if isinstance(value, str):
        return len(value) <= 100_000 and _CONTROL.search(value) is None
    if isinstance(value, Mapping):
        return all(_safe_text(key) and _safe_text(item) for key, item in value.items())
    if isinstance(value, list | tuple):
        return all(_safe_text(item) for item in value)
    return True


def _issue(code: str, message: str, *, item_index: int | None = None) -> dict[str, Any]:
    digest = (
        __import__("hashlib").sha256(f"{code}\0{message}\0{item_index}".encode()).hexdigest()[:20]
    )
    return {
        "issue_id": f"ISS-{digest}",
        "code": code,
        "quality_status": "INVALID",
        "target": f"model_output.items[{item_index}]" if item_index is not None else "model_output",
        "message": message,
        "candidate_ids": [],
        "evidence_ids": [],
        "blocking": True,
    }


_ALLOWED_KEYS = {
    "CLASSIFY": {
        "classification_id",
        "source_id",
        "primary_category",
        "secondary_categories",
        "confidence",
        "evidence_ids",
        "pipelines",
        "segments",
        "chainage_range",
        "document_date",
        "version_clues",
    },
    "EXTRACT_ENTITIES": {
        "entity_id",
        "entity_type",
        "raw_name",
        "normalized_name",
        "business_key",
        "time_range",
        "chainage_range",
        "coordinate_range",
        "evidence_ids",
        "confidence",
        "source_id",
    },
    "EXTRACT_FIELDS": {
        "candidate_id",
        "field_id",
        "entity_id",
        "raw_value",
        "source_unit",
        "normalized_value",
        "confidence",
        "evidence_ids",
        "not_found",
        "effective_from",
        "effective_to",
    },
    "EXTRACT_RELATIONSHIPS": {
        "relationship_id",
        "relation_type",
        "source_entity_id",
        "target_entity_id",
        "evidence_ids",
        "derived_rule",
        "confidence",
    },
}


def validate_structured_output(
    task_type: str,
    output: dict[str, Any],
    *,
    allowed_field_ids: set[str],
    allowed_evidence: dict[str, str],
    known_entity_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    if _depth(output) > 12 or not _safe_text(output):
        return [], [_issue("EXTRACT.OUTPUT_UNSAFE", "模型输出深度、长度或控制字符不符合合同")]
    try:
        encoded = json.dumps(output, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        return [], [_issue("EXTRACT.OUTPUT_INVALID", "模型输出不能编码为有限JSON")]
    if len(encoded) > 2_000_000:
        return [], [_issue("EXTRACT.RESPONSE_TOO_LARGE", "模型输出超过2MB上限")]
    raw_items = output.get("items")
    if not isinstance(raw_items, list):
        return [], [_issue("EXTRACT.OUTPUT_INVALID", "模型输出必须包含items数组")]
    allowed_keys = _ALLOWED_KEYS.get(task_type)
    if allowed_keys is None:
        return [], [_issue("EXTRACT.TASK_NOT_ALLOWED", f"不允许的提取任务：{task_type}")]
    valid: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            issues.append(_issue("EXTRACT.ITEM_INVALID", "提取项必须是对象", item_index=index))
            continue
        unknown = sorted(set(raw) - allowed_keys)
        if unknown:
            issues.append(
                _issue(
                    "EXTRACT.UNKNOWN_OUTPUT_FIELD",
                    "模型输出包含未授权字段：" + ", ".join(unknown),
                    item_index=index,
                )
            )
            continue
        evidence_ids = raw.get("evidence_ids", [])
        if not isinstance(evidence_ids, list) or any(
            not isinstance(item, str) or item not in allowed_evidence for item in evidence_ids
        ):
            issues.append(
                _issue("EXTRACT.EVIDENCE_INVALID", "证据引用不属于本次输入块", item_index=index)
            )
            continue
        confidence = raw.get("confidence", 0.0)
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, int | float)
            or not 0 <= float(confidence) <= 1
        ):
            issues.append(
                _issue("EXTRACT.CONFIDENCE_INVALID", "置信度必须在[0,1]", item_index=index)
            )
            continue
        if task_type == "CLASSIFY":
            if raw.get("primary_category") not in DOCUMENT_CATEGORIES:
                issues.append(
                    _issue("EXTRACT.CATEGORY_INVALID", "资料类别不在白名单", item_index=index)
                )
                continue
        elif task_type == "EXTRACT_ENTITIES":
            if raw.get("entity_type") not in ENTITY_TYPES:
                issues.append(
                    _issue("EXTRACT.ENTITY_TYPE_INVALID", "实体类型不在白名单", item_index=index)
                )
                continue
            if not str(raw.get("entity_id") or "").startswith("ENT-"):
                issues.append(
                    _issue("EXTRACT.ENTITY_ID_INVALID", "实体临时ID无效", item_index=index)
                )
                continue
        elif task_type == "EXTRACT_FIELDS":
            if raw.get("not_found") is True:
                continue
            if raw.get("field_id") not in allowed_field_ids:
                issues.append(
                    _issue("EXTRACT.FIELD_NOT_ALLOWED", "字段ID不在本次子集", item_index=index)
                )
                continue
            if raw.get("entity_id") not in known_entity_ids:
                issues.append(
                    _issue("EXTRACT.ENTITY_REFERENCE_INVALID", "字段引用未知实体", item_index=index)
                )
                continue
            if not evidence_ids:
                issues.append(
                    _issue("EXTRACT.EVIDENCE_REQUIRED", "文档候选必须绑定证据", item_index=index)
                )
                continue
            raw_text = str(raw.get("raw_value") or "").strip()
            evidence_text = "\n".join(allowed_evidence[item] for item in evidence_ids)
            if raw_text and raw_text not in evidence_text:
                issues.append(
                    _issue(
                        "EXTRACT.RAW_VALUE_NOT_IN_EVIDENCE",
                        "原值无法在证据文本中核对",
                        item_index=index,
                    )
                )
                continue
        else:
            if raw.get("relation_type") not in RELATION_TYPES:
                issues.append(
                    _issue("EXTRACT.RELATION_TYPE_INVALID", "关系类型不在白名单", item_index=index)
                )
                continue
            endpoints = {raw.get("source_entity_id"), raw.get("target_entity_id")}
            if None in endpoints or not endpoints.issubset(known_entity_ids):
                issues.append(
                    _issue(
                        "EXTRACT.RELATION_ENDPOINT_INVALID",
                        "关系端点必须引用现有实体",
                        item_index=index,
                    )
                )
                continue
            if not evidence_ids and not raw.get("derived_rule"):
                issues.append(
                    _issue(
                        "EXTRACT.RELATION_EVIDENCE_REQUIRED",
                        "关系必须有证据或确定性规则",
                        item_index=index,
                    )
                )
                continue
        valid.append(dict(raw))
    return valid, issues


__all__ = [
    "DOCUMENT_CATEGORIES",
    "ENTITY_TYPES",
    "RELATION_TYPES",
    "detect_untrusted_instructions",
    "validate_structured_output",
]
