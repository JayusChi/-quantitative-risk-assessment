"""Lossless numeric parsing with qualifiers and ranges."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

_NUMBER = r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?"
_RANGE = re.compile(rf"^\s*({_NUMBER})\s*(?:~|～|至|—|–)\s*({_NUMBER})\s*$")
_SINGLE = re.compile(rf"^\s*(约|≈|~|≤|>=|≥|<=|<|>)?\s*({_NUMBER})\s*$")


@dataclass(frozen=True, slots=True)
class ParsedNumber:
    value: Decimal | None
    lower: Decimal | None
    upper: Decimal | None
    qualifier: str | None

    def json_value(self) -> int | float | dict[str, Any]:
        if self.lower is not None or self.upper is not None:
            return {
                "lower": _json_decimal(self.lower),
                "upper": _json_decimal(self.upper),
                "qualifier": self.qualifier,
            }
        if self.value is None:
            raise ValueError("数值为空")
        return _json_decimal(self.value)


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"无法解析数值：{value}") from exc


def _json_decimal(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def parse_number(value: Any) -> ParsedNumber:
    if isinstance(value, bool):
        raise ValueError("布尔值不能作为数值")
    if isinstance(value, int | float | Decimal):
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("数值必须有限")
        return ParsedNumber(number, None, None, None)
    text = str(value).strip()
    range_match = _RANGE.fullmatch(text)
    if range_match:
        lower, upper = _decimal(range_match.group(1)), _decimal(range_match.group(2))
        if upper < lower:
            raise ValueError("数值区间上界小于下界")
        return ParsedNumber(None, lower, upper, "RANGE")
    match = _SINGLE.fullmatch(text)
    if not match:
        raise ValueError(f"无法解析数值：{text}")
    qualifier = {
        "约": "APPROXIMATE",
        "≈": "APPROXIMATE",
        "~": "APPROXIMATE",
        "<": "LESS_THAN",
        "≤": "LESS_THAN_OR_EQUAL",
        "<=": "LESS_THAN_OR_EQUAL",
        ">": "GREATER_THAN",
        "≥": "GREATER_THAN_OR_EQUAL",
        ">=": "GREATER_THAN_OR_EQUAL",
    }.get(match.group(1))
    return ParsedNumber(_decimal(match.group(2)), None, None, qualifier)


__all__ = ["ParsedNumber", "parse_number"]
