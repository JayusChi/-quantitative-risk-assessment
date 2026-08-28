"""Field completeness and node availability without treating defaults as facts."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any


def _issue_id(field_id: str) -> str:
    return "ISS-MISSING-" + hashlib.sha256(field_id.encode()).hexdigest()[:20]


def _usable(candidate: dict[str, Any], conflict_ids: set[str]) -> bool:
    return (
        candidate.get("quality_status") == "PASS"
        and candidate.get("candidate_id") not in conflict_ids
        and candidate.get("normalized_value") is not None
    )


def completeness_issues(
    candidates: list[dict[str, Any]],
    *,
    fields: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    present = {
        str(item["field_id"])
        for item in candidates
        if item.get("quality_status") not in {"INVALID", "MISSING"}
    }
    issues = []
    for field_id, definition in sorted(fields.items()):
        if definition.get("required_level") != "REQUIRED" or field_id in present:
            continue
        nodes = list(definition.get("required_by_nodes") or [])
        issues.append(
            {
                "issue_id": _issue_id(field_id),
                "code": "EXTRACT.FIELD_MISSING",
                "quality_status": "MISSING",
                "target": f"field:{field_id}",
                "message": f"必填候选缺失：{field_id}",
                "candidate_ids": [],
                "evidence_ids": [],
                "blocking": bool(nodes),
            }
        )
    return issues


def candidate_capability_plan(
    candidates: list[dict[str, Any]],
    fusion_groups: list[dict[str, Any]],
    *,
    fields: dict[str, dict[str, Any]],
    engine_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conflict_ids = {
        candidate_id
        for group in fusion_groups
        if group.get("group_type") == "CONFLICT"
        for candidate_id in group.get("candidate_ids", [])
    }
    usable_fields = {
        str(candidate["field_id"]) for candidate in candidates if _usable(candidate, conflict_ids)
    }
    missing_by_node: dict[str, set[str]] = defaultdict(set)
    for field_id, definition in fields.items():
        if field_id in usable_fields:
            continue
        for node_id in definition.get("required_by_nodes") or []:
            missing_by_node[str(node_id)].add(field_id)
    candidate_nodes = [
        {
            "node_id": node_id,
            "status": "RUNNABLE" if not missing else "MISSING_INPUTS",
            "missing_inputs": [
                {"field_id": field_id, "path": fields[field_id].get("target_path")}
                for field_id in sorted(missing)
            ],
        }
        for node_id, missing in sorted(missing_by_node.items())
    ]
    return {
        "basis": "VALIDATED_CANDIDATES_ONLY",
        "default_values_counted_as_project_facts": False,
        "usable_field_ids": sorted(usable_fields),
        "conflicted_candidate_ids": sorted(conflict_ids),
        "candidate_nodes": candidate_nodes,
        "engine_import_preflight": engine_plan or {},
    }


__all__ = ["candidate_capability_plan", "completeness_issues"]
