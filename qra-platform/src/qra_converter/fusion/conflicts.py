"""Conflict groups retain every member and hash the complete candidate set."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from .duplicates import duplicate_kind
from .source_policy import proposed_candidate


def _hash_members(candidates: list[dict[str, Any]]) -> str:
    payload = [
        {
            "candidate_id": item["candidate_id"],
            "normalized_value": item.get("normalized_value"),
            "evidence_ids": sorted(item.get("evidence_ids") or []),
        }
        for item in sorted(candidates, key=lambda row: str(row["candidate_id"]))
    ]
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_fusion_groups(
    candidates: list[dict[str, Any]],
    *,
    fields: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        entity = candidate.get("entity") or {}
        grouped[(str(entity.get("entity_key")), str(candidate["field_id"]))].append(candidate)
    groups: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for (entity_key, field_id), members in sorted(grouped.items()):
        definition = fields[field_id]
        policy = dict(definition.get("conflict_policy") or {})
        tolerance = float(policy.get("numeric_tolerance", 0.0))
        kind = "SINGLE" if len(members) == 1 else duplicate_kind(members, tolerance)
        proposed, reason = proposed_candidate(members)
        member_hash = _hash_members(members)
        group_id = (
            "FUS-"
            + hashlib.sha256(f"{entity_key}\0{field_id}\0{member_hash}".encode()).hexdigest()[:24]
        )
        groups.append(
            {
                "fusion_group_id": group_id,
                "entity_key": entity_key,
                "field_id": field_id,
                "group_type": kind,
                "candidate_ids": [
                    str(item["candidate_id"])
                    for item in sorted(members, key=lambda row: str(row["candidate_id"]))
                ],
                "candidate_set_sha256": member_hash,
                "proposed_candidate_id": proposed,
                "proposal_reason": reason,
                "confirmed_candidate_id": None,
            }
        )
        if kind == "CONFLICT":
            issues.append(
                {
                    "issue_id": f"ISS-{group_id}",
                    "code": "FUSION.VALUE_CONFLICT",
                    "quality_status": "CONFLICT",
                    "target": f"field:{field_id};entity:{entity_key}",
                    "message": "同一对象和字段的有效候选超出合同容差，全部来源已保留",
                    "candidate_ids": [item["candidate_id"] for item in members],
                    "evidence_ids": sorted(
                        {evidence for item in members for evidence in item.get("evidence_ids", [])}
                    ),
                    "blocking": bool(policy.get("blocking", True)),
                }
            )
    by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_entity[str((candidate.get("entity") or {}).get("entity_key"))].append(candidate)
    for entity_key, members in sorted(by_entity.items()):
        field_ids = {str(item["field_id"]) for item in members}
        if len(field_ids) < 2:
            continue
        member_hash = _hash_members(members)
        group_id = (
            "FUS-"
            + hashlib.sha256(f"{entity_key}\0COMPLEMENTARY\0{member_hash}".encode()).hexdigest()[
                :24
            ]
        )
        groups.append(
            {
                "fusion_group_id": group_id,
                "entity_key": entity_key,
                "field_id": None,
                "group_type": "COMPLEMENTARY",
                "candidate_ids": sorted(str(item["candidate_id"]) for item in members),
                "candidate_set_sha256": member_hash,
                "proposed_candidate_id": None,
                "proposal_reason": "同一对象的不同字段互补，不进行值覆盖",
                "confirmed_candidate_id": None,
            }
        )
    return groups, issues


def decision_is_stale(group: dict[str, Any], decision: dict[str, Any]) -> bool:
    return str(decision.get("candidate_set_sha256") or "") != str(
        group.get("candidate_set_sha256") or ""
    )


__all__ = ["build_fusion_groups", "decision_is_stale"]
