"""Source ordering proposes a candidate but never confirms or deletes another."""

from __future__ import annotations

from datetime import date
from typing import Any


def proposed_candidate(candidates: list[dict[str, Any]]) -> tuple[str | None, str]:
    if not candidates:
        return None, "候选集合为空"

    def key(item: dict[str, Any]) -> tuple[Any, ...]:
        effective = str(item.get("effective_from") or "")
        try:
            ordinal = date.fromisoformat(effective).toordinal()
        except ValueError:
            ordinal = 0
        return (
            int((item.get("model_or_rule_versions") or {}).get("source_rank", 0)),
            ordinal,
            float(item.get("confidence", 0.0)),
            str(item["candidate_id"]),
        )

    selected = max(candidates, key=key)
    return (
        str(selected["candidate_id"]),
        "按批准来源等级、有效日期和置信度生成建议；建议不等于确认",
    )


__all__ = ["proposed_candidate"]
