"""Stable relationship IDs and endpoint-preserving deduplication."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def finalize_relationships(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        row = dict(item)
        payload = {
            "type": row.get("relation_type"),
            "source": row.get("source_entity_id"),
            "target": row.get("target_entity_id"),
            "evidence": sorted(row.get("evidence_ids") or []),
            "rule": row.get("derived_rule"),
        }
        relationship_id = str(row.get("relationship_id") or "")
        if not relationship_id.startswith("REL-"):
            encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
            relationship_id = "REL-" + hashlib.sha256(encoded).hexdigest()[:20]
        row["relationship_id"] = relationship_id
        result.setdefault(relationship_id, row)
    return [result[key] for key in sorted(result)]


__all__ = ["finalize_relationships"]
