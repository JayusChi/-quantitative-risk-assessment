"""Deterministic identity keys in the required precedence order."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any


def identity_key(
    entity: dict[str, Any],
    *,
    approved_aliases: dict[str, str] | None = None,
) -> tuple[str, bool]:
    entity_type = str(entity.get("entity_type") or "UNKNOWN")
    business_key = str(entity.get("business_key") or "").strip()
    if business_key:
        return f"ID:{entity_type}:{business_key}", False
    name = " ".join(str(entity.get("normalized_name") or entity.get("raw_name") or "").split())
    alias = (approved_aliases or {}).get(name) or (approved_aliases or {}).get(name.casefold())
    if alias:
        return f"ALIAS:{entity_type}:{alias}", False
    pipeline_id = str(entity.get("pipeline_id") or "").strip()
    chainage = entity.get("chainage_range")
    if pipeline_id and isinstance(chainage, dict):
        start, end = chainage.get("start_km"), chainage.get("end_km")
        if start is not None and end is not None:
            return (
                f"CHAINAGE:{entity_type}:{pipeline_id}:{Decimal(str(start))}:{Decimal(str(end))}",
                False,
            )
    coordinates = entity.get("coordinate_range")
    time_range = entity.get("time_range")
    if coordinates and time_range:
        encoded = json.dumps([entity_type, coordinates, time_range], sort_keys=True).encode()
        return f"SPATIOTEMPORAL:{hashlib.sha256(encoded).hexdigest()[:20]}", False
    if name:
        return f"FUZZY_NAME:{entity_type}:{name.casefold()}", True
    return f"TEMP:{entity_type}:{entity.get('entity_id') or entity.get('entity_key')}", True


def bind_entity_identities(
    entities: list[dict[str, Any]],
    *,
    approved_aliases: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    mapping: dict[str, str] = {}
    issues: list[dict[str, Any]] = []
    for entity in entities:
        key, ambiguous = identity_key(entity, approved_aliases=approved_aliases)
        entity_id = str(entity["entity_id"])
        mapping[entity_id] = key
        if ambiguous:
            issues.append(
                {
                    "issue_id": f"ISS-{entity_id}-IDENTITY",
                    "code": "FUSION.IDENTITY_AMBIGUOUS",
                    "quality_status": "WARNING",
                    "target": f"entity:{entity_id}",
                    "message": "实体仅能形成待复核身份候选，未绑定正式管段",
                    "candidate_ids": [],
                    "evidence_ids": list(entity.get("evidence_ids") or []),
                    "blocking": entity.get("entity_type") == "SEGMENT",
                }
            )
    return mapping, issues


def rebind_candidate_entities(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Promote explicit business-ID candidates before any value comparison."""

    by_temporary: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        key = str((candidate.get("entity") or {}).get("entity_key"))
        by_temporary.setdefault(key, []).append(candidate)
    replacements: dict[str, str] = {}
    for temporary, rows in by_temporary.items():
        explicit = next(
            (
                row
                for row in rows
                if str(row.get("field_id")).endswith((".segment_id", ".pipeline_id"))
                and row.get("normalized_value") not in {None, ""}
            ),
            None,
        )
        if explicit is not None:
            entity_type = str((explicit.get("entity") or {}).get("entity_type"))
            replacements[temporary] = f"ID:{entity_type}:{explicit['normalized_value']}"
    rebound = []
    for candidate in candidates:
        row = dict(candidate)
        entity = dict(row.get("entity") or {})
        entity["entity_key"] = replacements.get(
            str(entity.get("entity_key")), entity.get("entity_key")
        )
        row["entity"] = entity
        rebound.append(row)
    return rebound


__all__ = ["bind_entity_identities", "identity_key", "rebind_candidate_entities"]
