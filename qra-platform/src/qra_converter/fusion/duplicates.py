"""Exact and near-duplicate comparison in canonical value space."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any


def canonical_value(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def within_tolerance(left: Any, right: Any, tolerance: float) -> bool:
    try:
        return abs(Decimal(str(left)) - Decimal(str(right))) <= Decimal(str(tolerance))
    except (InvalidOperation, TypeError, ValueError):
        return canonical_value(left) == canonical_value(right)


def duplicate_kind(candidates: list[dict[str, Any]], tolerance: float) -> str:
    normalized = {canonical_value(item.get("normalized_value")) for item in candidates}
    if len(normalized) == 1:
        raw = {canonical_value(item.get("raw_value")) for item in candidates}
        return "EXACT_DUPLICATE" if len(raw) == 1 else "NEAR_DUPLICATE"
    anchor = candidates[0].get("normalized_value")
    if all(
        within_tolerance(anchor, item.get("normalized_value"), tolerance) for item in candidates[1:]
    ):
        return "NEAR_DUPLICATE"
    return "CONFLICT"


__all__ = ["canonical_value", "duplicate_kind", "within_tolerance"]
