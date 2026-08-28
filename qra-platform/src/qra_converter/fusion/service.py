"""Stage-four fusion facade."""

from __future__ import annotations

from typing import Any

from .conflicts import build_fusion_groups
from .identities import rebind_candidate_entities


def fuse_candidates(
    candidates: list[dict[str, Any]],
    *,
    fields: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    rebound = rebind_candidate_entities(candidates)
    groups, issues = build_fusion_groups(rebound, fields=fields)
    return rebound, groups, issues


__all__ = ["fuse_candidates"]
