"""Coordinate normalization that refuses spatial mapping without a CRS."""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def normalize_coordinate(value: Any, coordinate_system: str | None) -> dict[str, Any]:
    if not coordinate_system or not str(coordinate_system).strip():
        raise ValueError("坐标缺少坐标系，不能执行空间映射")
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise ValueError("坐标必须包含x和y")
    x, y = Decimal(str(value[0])), Decimal(str(value[1]))
    if not x.is_finite() or not y.is_finite():
        raise ValueError("坐标必须为有限数")
    return {"x": float(x), "y": float(y), "coordinate_system": str(coordinate_system)}


__all__ = ["normalize_coordinate"]
