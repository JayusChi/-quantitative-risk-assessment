"""Deterministic extraction metrics, optionally compared with a labelled golden set."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def _fact_key(item: dict[str, Any]) -> tuple[str, str, str]:
    entity = item.get("entity") or {}
    return (
        str(item.get("field_id")),
        str(entity.get("entity_key")),
        repr(item.get("normalized_value")),
    )


def extraction_metrics(
    candidates: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    *,
    golden_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    extracted = {_fact_key(item) for item in candidates if item.get("quality_status") != "INVALID"}
    expected = {_fact_key(item) for item in golden_candidates or []}
    true_positive = len(extracted & expected) if golden_candidates is not None else None
    precision = (
        true_positive / len(extracted) if golden_candidates is not None and extracted else None
    )
    recall = true_positive / len(expected) if golden_candidates is not None and expected else None
    evidence_bound = sum(bool(item.get("evidence_ids")) for item in candidates)
    conflicts = sum(issue.get("code") == "FUSION.VALUE_CONFLICT" for issue in issues)
    manual = sum(
        item.get("quality_status") in {"LOW_CONFIDENCE", "PENDING_REVIEW", "CONFLICT", "INVALID"}
        for item in candidates
    )
    by_method = Counter(str(item.get("extraction_method")) for item in candidates)
    by_field_group: dict[str, int] = defaultdict(int)
    for item in candidates:
        by_field_group[str(item.get("field_id", "")).split(".", 1)[0]] += 1
    return {
        "candidate_count": len(candidates),
        "precision": precision,
        "recall": recall,
        "evidence_binding_rate": evidence_bound / len(candidates) if candidates else 1.0,
        "conflict_count": conflicts,
        "manual_review_rate": manual / len(candidates) if candidates else 0.0,
        "by_extraction_method": dict(sorted(by_method.items())),
        "by_field_group": dict(sorted(by_field_group.items())),
    }


__all__ = ["extraction_metrics"]
