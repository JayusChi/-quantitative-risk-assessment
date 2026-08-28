"""Entity post-processing that never assigns formal segment identifiers."""

from __future__ import annotations

import hashlib
import re
from typing import Any


def normalize_entity_name(value: Any) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text.casefold() or None


def finalize_entities(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        row = dict(item)
        row["normalized_name"] = normalize_entity_name(
            row.get("normalized_name") or row.get("raw_name")
        )
        if row.get("entity_type") == "SEGMENT":
            row.pop("segment_id", None)
        entity_id = str(row.get("entity_id") or "")
        if not entity_id.startswith("ENT-"):
            seed = {
                key: row.get(key)
                for key in ("entity_type", "raw_name", "business_key", "source_id")
            }
            entity_id = (
                "ENT-" + hashlib.sha256(repr(sorted(seed.items())).encode()).hexdigest()[:20]
            )
            row["entity_id"] = entity_id
        result.setdefault(entity_id, row)
    return [result[key] for key in sorted(result)]


__all__ = ["finalize_entities", "normalize_entity_name"]
