"""Explicit Decimal unit conversions; pressure basis is never inferred."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

_FACTORS: dict[str, tuple[str, Decimal]] = {
    "Pa": ("pressure", Decimal("1")),
    "kPa": ("pressure", Decimal("1000")),
    "MPa": ("pressure", Decimal("1000000")),
    "bar": ("pressure", Decimal("100000")),
    "mm": ("length", Decimal("0.001")),
    "cm": ("length", Decimal("0.01")),
    "m": ("length", Decimal("1")),
    "km": ("length", Decimal("1000")),
    "V": ("electric_potential", Decimal("1")),
    "mV": ("electric_potential", Decimal("0.001")),
    "m/s": ("speed", Decimal("1")),
    "km/h": ("speed", Decimal("0.2777777777777777777777777778")),
    "fraction": ("ratio", Decimal("1")),
    "%": ("ratio", Decimal("0.01")),
    "per_km_year": ("frequency", Decimal("1")),
    "1/(km*year)": ("frequency", Decimal("1")),
    "ohm*m": ("resistivity", Decimal("1")),
    "Ω·m": ("resistivity", Decimal("1")),
}


def convert_unit(value: Decimal, source_unit: str | None, target_unit: str | None) -> Decimal:
    if target_unit is None or source_unit == target_unit:
        return value
    if source_unit is None:
        raise ValueError("原值没有单位证据，不能执行单位换算")
    if source_unit in {"Pa_abs", "kPa_abs", "MPa_abs"} or target_unit in {
        "Pa_abs",
        "kPa_abs",
        "MPa_abs",
    }:
        if source_unit != target_unit:
            raise ValueError("绝压与未声明压力基准的单位不得互换")
        return value
    if source_unit == "°C" and target_unit == "K":
        return value + Decimal("273.15")
    source = _FACTORS.get(source_unit)
    target = _FACTORS.get(target_unit)
    if source is None or target is None or source[0] != target[0]:
        raise ValueError(f"不支持的单位换算：{source_unit} -> {target_unit}")
    return value * source[1] / target[1]


def conversion_formula(source_unit: str | None, target_unit: str | None) -> str | None:
    if source_unit == target_unit or target_unit is None:
        return None
    if source_unit == "°C" and target_unit == "K":
        return "K = °C + 273.15"
    source = _FACTORS.get(str(source_unit))
    target = _FACTORS.get(str(target_unit))
    if source is None or target is None:
        return None
    return f"{target_unit} = {source_unit} × {source[1]} / {target[1]}"


def unit_allowed_for_field(
    source_unit: str | None,
    field_id: str,
    unit_registry: dict[str, Any],
) -> bool:
    if source_unit is None:
        return True
    for rule in unit_registry.get("units", []):
        if rule.get("symbol") != source_unit:
            continue
        allowed = rule.get("allowed_fields") or []
        return "*" in allowed or field_id in allowed
    return False


__all__ = ["conversion_formula", "convert_unit", "unit_allowed_for_field"]
