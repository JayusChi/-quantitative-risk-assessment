"""Quality-gate evaluation for review sessions."""

from __future__ import annotations

from typing import Any

from qra_converter.schema_validation import validate_schema_document
from qra_engine.dynamic import plan_dynamic_flow
from qra_engine.validation import validate_import_contract

from .database import json_sha256

GATE_RULE_VERSION = "qra.review-gate/1.0.0"


def _issue_document(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if isinstance(value, dict):
        return dict(value)
    return {"code": "REVIEW.GATE_VALIDATION", "message": str(value), "severity": "ERROR"}


def evaluate_review_gate(
    *,
    session: dict[str, Any],
    items: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    payload: dict[str, Any] | None,
    assembly_error: str | None,
    catalog: Any,
    candidate_set_hash: str,
    decision_set_hash: str,
) -> dict[str, Any]:
    """Return a deterministic gate result; persistence and audit stay in the service."""

    unresolved = [item for item in items if item.get("requires_resolution")]
    warnings = [
        issue
        for item in items
        for issue in item.get("quality_issues", [])
        if not bool(issue.get("blocking"))
    ]
    gate_issues: list[dict[str, Any]] = []
    if assembly_error:
        gate_issues.append(
            {
                "code": "REVIEW.ASSEMBLY_FAILED",
                "message": assembly_error,
                "severity": "ERROR",
                "blocking": True,
            }
        )

    schema_issues: list[dict[str, Any]] = []
    contract_issues: list[dict[str, Any]] = []
    dynamic_plan: dict[str, Any] = {
        "runnable_node_ids": [],
        "skipped_node_ids": [],
        "plan": [],
        "requested_targets": list(session.get("target_node_ids") or []),
    }
    payload_hash: str | None = None
    if payload is not None and not assembly_error:
        payload_hash = json_sha256(payload)
        schema_issues = [
            _issue_document(issue)
            for issue in validate_schema_document(payload, catalog=catalog, schema_name="qra-input")
        ]
        if not schema_issues:
            validation = validate_import_contract(payload)
            contract_issues = [_issue_document(issue) for issue in validation.issues]
        if not schema_issues and not contract_issues:
            dynamic_plan = plan_dynamic_flow(payload, session.get("target_node_ids") or None)

    target_nodes = list(session.get("target_node_ids") or [])
    runnable_ids = list(dynamic_plan.get("runnable_node_ids") or [])
    blocked_target_ids = [node for node in target_nodes if node not in runnable_ids]
    plan_by_id = {
        str(row.get("node_id")): row
        for row in dynamic_plan.get("plan", [])
        if isinstance(row, dict)
    }
    missing_inputs = {
        node_id: list((plan_by_id.get(node_id) or {}).get("missing_inputs") or [])
        for node_id in blocked_target_ids
    }
    for item in unresolved:
        gate_issues.append(
            {
                "code": "REVIEW.FIELD_UNRESOLVED",
                "message": f"字段尚未解决：{item.get('field_name') or item['field_id']}",
                "review_item_key": item["review_item_key"],
                "field_id": item["field_id"],
                "severity": "ERROR",
                "blocking": True,
            }
        )
    gate_issues.extend(schema_issues)
    gate_issues.extend(contract_issues)
    for node_id in blocked_target_ids:
        gate_issues.append(
            {
                "code": "REVIEW.TARGET_NODE_BLOCKED",
                "message": f"目标计算节点不可运行：{node_id}",
                "node_id": node_id,
                "missing_inputs": missing_inputs[node_id],
                "severity": "ERROR",
                "blocking": True,
            }
        )

    action_counts = {
        action: sum(1 for row in decisions if row.get("action") == action)
        for action in (
            "ACCEPT_CANDIDATE",
            "OVERRIDE_VALUE",
            "REJECT_ALL",
            "MARK_NOT_APPLICABLE",
            "REQUEST_REEXTRACTION",
        )
    }
    blocking_count = sum(1 for issue in gate_issues if issue.get("blocking", True))
    status = "PASS" if blocking_count == 0 else "BLOCKED"
    assembled_count = sum(
        1
        for item in items
        if item.get("resolution_status")
        in {"AUTO_DETERMINISTIC", "ACCEPTED", "OVERRIDDEN", "NOT_APPLICABLE"}
    )
    formal_allowed = bool(
        payload
        and isinstance(payload.get("metadata"), dict)
        and payload["metadata"].get("formal_qra_allowed") is True
        and "human_qra" in runnable_ids
    )
    stable = {
        "rule_version": GATE_RULE_VERSION,
        "status": status,
        "candidate_set_hash": candidate_set_hash,
        "decision_set_hash": decision_set_hash,
        "target_node_ids": target_nodes,
        "blocking_issue_count": blocking_count,
        "warning_count": len(warnings),
        "unresolved_field_count": len(unresolved),
        "accepted_field_count": action_counts["ACCEPT_CANDIDATE"],
        "overridden_field_count": action_counts["OVERRIDE_VALUE"],
        "rejected_field_count": action_counts["REJECT_ALL"],
        "not_applicable_count": action_counts["MARK_NOT_APPLICABLE"],
        "reextraction_requested_count": action_counts["REQUEST_REEXTRACTION"],
        "assembled_field_count": assembled_count,
        "runnable_node_ids": runnable_ids,
        "blocked_node_ids": blocked_target_ids,
        "skipped_node_ids": list(dynamic_plan.get("skipped_node_ids") or []),
        "missing_inputs": missing_inputs,
        "payload_sha256": payload_hash,
        "formal_report_allowed": formal_allowed,
        "report_tier": "FORMAL_QRA" if formal_allowed else "TEST_SCREENING",
        "issues": gate_issues,
        "schema_issues": schema_issues,
        "contract_issues": contract_issues,
        "dynamic_plan": dynamic_plan,
    }
    stable["result_hash"] = json_sha256(stable)
    return stable


__all__ = ["GATE_RULE_VERSION", "evaluate_review_gate"]
