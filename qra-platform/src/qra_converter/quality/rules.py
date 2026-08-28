"""Deterministic candidate and relationship quality rules."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any


def _issue(
    code: str,
    message: str,
    *,
    candidate_ids: list[str],
    evidence_ids: list[str] | None = None,
    quality_status: str = "INVALID",
    blocking: bool = True,
) -> dict[str, Any]:
    seed = "\0".join([code, *sorted(candidate_ids), message])
    return {
        "issue_id": "ISS-" + hashlib.sha256(seed.encode()).hexdigest()[:24],
        "code": code,
        "quality_status": quality_status,
        "target": "candidates:" + ",".join(candidate_ids) if candidate_ids else "relationships",
        "message": message,
        "candidate_ids": candidate_ids,
        "evidence_ids": evidence_ids or [],
        "blocking": blocking,
    }


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def validate_candidate_quality(
    candidates: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    *,
    fields: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    evidence_bound = set()
    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        evidence_ids = list(candidate.get("evidence_ids") or [])
        if not evidence_ids and candidate.get("non_document_source") is None:
            issues.append(
                _issue(
                    "EXTRACT.EVIDENCE_REQUIRED",
                    "文档候选没有证据",
                    candidate_ids=[candidate_id],
                )
            )
        else:
            evidence_bound.add(candidate_id)
        definition = fields[str(candidate["field_id"])]
        critical = definition.get("required_level") in {"REQUIRED", "CONDITIONAL"}
        if candidate.get("quality_status") == "LOW_CONFIDENCE":
            issues.append(
                _issue(
                    "EXTRACT.LOW_CONFIDENCE",
                    "关键候选置信度低，不能进入可确认草稿",
                    candidate_ids=[candidate_id],
                    evidence_ids=evidence_ids,
                    quality_status="LOW_CONFIDENCE",
                    blocking=critical,
                )
            )

    values: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        if candidate.get("quality_status") != "PASS":
            continue
        entity_key = str((candidate.get("entity") or {}).get("entity_key"))
        values[(entity_key, str(candidate["field_id"]))].append(candidate)
    by_entity: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for (entity_key, field_id), rows in values.items():
        by_entity[entity_key][field_id] = rows[0]
    for entity_values in by_entity.values():
        design = entity_values.get("pipeline.design_pressure_mpa")
        operating = entity_values.get("pipeline.operating_pressure_mpa")
        if design and operating:
            design_value = _decimal(design.get("normalized_value"))
            operating_value = _decimal(operating.get("normalized_value"))
            if (
                design_value is not None
                and operating_value is not None
                and operating_value > design_value
            ):
                issues.append(
                    _issue(
                        "CONTRACT.OPERATING_PRESSURE_EXCEEDS_DESIGN",
                        "运行压力高于设计压力",
                        candidate_ids=[design["candidate_id"], operating["candidate_id"]],
                    )
                )
        diameter = entity_values.get("segment.outside_diameter_mm")
        thickness = entity_values.get("segment.wall_thickness_mm")
        if diameter and thickness:
            diameter_value = _decimal(diameter.get("normalized_value"))
            thickness_value = _decimal(thickness.get("normalized_value"))
            if (
                diameter_value is not None
                and thickness_value is not None
                and diameter_value <= thickness_value
            ):
                issues.append(
                    _issue(
                        "CONTRACT.DIAMETER_NOT_GREATER_THAN_THICKNESS",
                        "外径必须大于壁厚",
                        candidate_ids=[diameter["candidate_id"], thickness["candidate_id"]],
                    )
                )

    entity_ids = {str(entity["entity_id"]) for entity in entities}
    for relationship in relationships:
        endpoints = {
            str(relationship.get("source_entity_id")),
            str(relationship.get("target_entity_id")),
        }
        if not endpoints.issubset(entity_ids):
            issues.append(
                _issue(
                    "EXTRACT.RELATION_ENDPOINT_INVALID",
                    "关系引用了不存在的实体",
                    candidate_ids=[],
                )
            )
    return issues


__all__ = ["validate_candidate_quality"]
