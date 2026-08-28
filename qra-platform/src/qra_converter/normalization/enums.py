"""Approved alias-only enum mapping."""

from __future__ import annotations

from typing import Any


def normalize_enum(
    value: Any,
    allowed: list[Any],
    aliases: dict[str, str] | None = None,
) -> str:
    text = str(value).strip()
    canonical = {str(item): str(item) for item in allowed}
    folded = {key.casefold(): result for key, result in canonical.items()}
    if text in canonical:
        return canonical[text]
    if text.casefold() in folded:
        return folded[text.casefold()]
    alias_value = (aliases or {}).get(text) or (aliases or {}).get(text.casefold())
    if alias_value in canonical:
        return str(alias_value)
    raise ValueError(f"枚举值无法唯一映射：{text}")


__all__ = ["normalize_enum"]
