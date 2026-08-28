"""Document classification helpers."""

from __future__ import annotations

import hashlib
from typing import Any

from .output_validation import DOCUMENT_CATEGORIES


def fallback_classification(source_id: str, evidence_ids: list[str]) -> dict[str, Any]:
    digest = hashlib.sha256(f"{source_id}\0OTHER_UNKNOWN".encode()).hexdigest()[:20]
    return {
        "classification_id": f"CLS-{digest}",
        "source_id": source_id,
        "primary_category": "OTHER_UNKNOWN",
        "secondary_categories": [],
        "confidence": 0.0,
        "evidence_ids": evidence_ids[:1],
        "pipelines": [],
        "segments": [],
        "chainage_range": None,
        "document_date": None,
        "version_clues": [],
        "model_version": "provider-unavailable",
        "requires_review": True,
    }


def deterministic_classification(
    source_id: str,
    declared: str | None,
    evidence_ids: list[str],
) -> dict[str, Any] | None:
    category = str(declared or "").strip().upper()
    if category not in DOCUMENT_CATEGORIES:
        return None
    digest = hashlib.sha256(f"{source_id}\0{category}".encode()).hexdigest()[:20]
    return {
        "classification_id": f"CLS-{digest}",
        "source_id": source_id,
        "primary_category": category,
        "secondary_categories": [],
        "confidence": 1.0,
        "evidence_ids": evidence_ids[:1],
        "pipelines": [],
        "segments": [],
        "chainage_range": None,
        "document_date": None,
        "version_clues": ["deterministic-mapping-profile"],
        "model_version": "deterministic/1.0.0",
        "requires_review": False,
    }


def mark_ambiguity(items: list[dict[str, Any]], threshold: float = 0.65) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        secondary = row.get("secondary_categories") or []
        row["requires_review"] = (
            float(row.get("confidence", 0.0)) < threshold
            or (bool(secondary) and float(row.get("confidence", 0.0)) < threshold + 0.1)
            or float(row.get("classification_margin", 1.0)) < 0.1
        )
        result.append(row)
    return result


def consolidate_classifications(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(str(item.get("source_id") or ""), []).append(item)
    result = []
    for source_id, rows in sorted(grouped.items()):
        ordered = sorted(rows, key=lambda row: float(row.get("confidence", 0.0)), reverse=True)
        top = dict(ordered[0])
        categories = [
            str(row["primary_category"])
            for row in ordered[1:]
            if row.get("primary_category") != top.get("primary_category")
        ]
        top["secondary_categories"] = sorted(
            set(top.get("secondary_categories") or []) | set(categories)
        )
        top["evidence_ids"] = sorted(
            {evidence for row in ordered for evidence in row.get("evidence_ids", [])}
        )
        top["source_id"] = source_id
        if len(ordered) > 1:
            top["classification_margin"] = float(top.get("confidence", 0.0)) - float(
                ordered[1].get("confidence", 0.0)
            )
        result.append(top)
    return result


__all__ = [
    "consolidate_classifications",
    "deterministic_classification",
    "fallback_classification",
    "mark_ambiguity",
]
