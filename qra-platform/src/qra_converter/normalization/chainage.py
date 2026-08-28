"""Canonical kilometre chainage parsing and half-open interval helpers."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

_K_PLUS = re.compile(r"^\s*[Kk]?\s*(\d+(?:\.\d+)?)\s*(?:km)?\s*\+\s*(\d+(?:\.\d+)?)\s*(?:m)?\s*$")
_METRES = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*m\s*$", re.IGNORECASE)
_KM = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*km\s*$", re.IGNORECASE)


def parse_chainage_km(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("桩号不能是布尔值")
    if isinstance(value, int | float | Decimal):
        result = Decimal(str(value))
    else:
        text = str(value).strip()
        match = _K_PLUS.fullmatch(text)
        if match:
            result = Decimal(match.group(1)) + Decimal(match.group(2)) / Decimal("1000")
        else:
            match = _METRES.fullmatch(text)
            if match:
                result = Decimal(match.group(1)) / Decimal("1000")
            else:
                match = _KM.fullmatch(text)
                if not match:
                    raise ValueError(f"桩号表达无法唯一解释：{text}")
                result = Decimal(match.group(1))
    if not result.is_finite() or result < 0:
        raise ValueError("桩号必须是非负有限数")
    return result


def in_half_open_range(
    chainage_km: Decimal,
    start_km: Decimal,
    end_km: Decimal,
    *,
    is_pipeline_terminal: bool = False,
) -> bool:
    return start_km <= chainage_km < end_km or (is_pipeline_terminal and chainage_km == end_km)


__all__ = ["in_half_open_range", "parse_chainage_km"]
