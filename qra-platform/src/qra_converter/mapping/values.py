from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from typing import Any


class ValueConversionError(ValueError):
    pass


_UNIT_ALIASES = {
    "c": "degc",
    "°c": "degc",
    "℃": "degc",
    "degc": "degc",
    "k": "k",
    "km": "km",
    "m": "m",
    "mm": "mm",
    "pa": "pa",
    "kpa": "kpa",
    "mpa": "mpa",
    "bar": "bar",
    "%": "percent",
    "percent": "percent",
    "fraction": "fraction",
}


def _unit(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", "", str(value)).casefold()
    if normalized not in _UNIT_ALIASES:
        raise ValueConversionError(f"不支持的单位：{value}")
    return _UNIT_ALIASES[normalized]


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueConversionError("布尔值不能作为数值")
    if isinstance(value, int | float):
        result = float(value)
    elif isinstance(value, str):
        stripped = value.strip().replace(",", "")
        if not stripped:
            raise ValueConversionError("空字符串不能作为数值")
        if stripped.endswith("%"):
            stripped = stripped[:-1].strip()
        try:
            result = float(stripped)
        except ValueError as exc:
            raise ValueConversionError(f"无法解析数值：{value}") from exc
    else:
        raise ValueConversionError(f"无法解析数值类型：{type(value).__name__}")
    if not math.isfinite(result):
        raise ValueConversionError("数值必须为有限数")
    return result


def _convert_unit(value: float, source: str | None, target: str | None) -> float:
    source_unit = _unit(source)
    target_unit = _unit(target)
    if source_unit == target_unit or (source_unit is None and target_unit is None):
        return value
    if source_unit is None or target_unit is None:
        raise ValueConversionError("源单位和目标单位必须同时明确")
    scale = {
        ("m", "km"): 0.001,
        ("km", "m"): 1000.0,
        ("m", "mm"): 1000.0,
        ("mm", "m"): 0.001,
        ("pa", "mpa"): 1.0e-6,
        ("kpa", "mpa"): 0.001,
        ("bar", "mpa"): 0.1,
        ("mpa", "pa"): 1.0e6,
        ("mpa", "kpa"): 1000.0,
        ("mpa", "bar"): 10.0,
        ("percent", "fraction"): 0.01,
        ("fraction", "percent"): 100.0,
    }
    if (source_unit, target_unit) in scale:
        return value * scale[(source_unit, target_unit)]
    if source_unit == "degc" and target_unit == "k":
        return value + 273.15
    if source_unit == "k" and target_unit == "degc":
        return value - 273.15
    raise ValueConversionError(f"不支持从{source}换算到{target}")


def _parse_date(value: Any, include_time: bool) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        text = value.strip()
        parsed = None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for pattern in (
                "%Y/%m/%d",
                "%Y.%m.%d",
                "%Y年%m月%d日",
                "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d %H:%M:%S",
            ):
                try:
                    parsed = datetime.strptime(text, pattern)
                    break
                except ValueError:
                    continue
        if parsed is None:
            raise ValueConversionError(f"无法解析日期：{value}")
    else:
        raise ValueConversionError(f"无法解析日期类型：{type(value).__name__}")
    if not include_time:
        return parsed.date().isoformat()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _parse_chainage_km(value: Any, field: dict[str, Any]) -> float:
    if isinstance(value, str):
        matches = re.findall(r"(?<!\d)(\d+)\s*\+\s*(\d+(?:\.\d+)?)", value)
        if matches:
            if len(matches) != 1:
                raise ValueConversionError(f"桩号表达包含多个里程：{value}")
            kilometre, metres = (float(item) for item in matches[0])
            if metres >= 1000.0:
                raise ValueConversionError(f"桩号米数部分必须小于1000：{value}")
            result = kilometre + metres / 1000.0
        else:
            result = _convert_unit(
                _number(value), field.get("source_unit"), field.get("target_unit")
            )
    else:
        result = _convert_unit(_number(value), field.get("source_unit"), field.get("target_unit"))
    minimum = float(field.get("minimum", 0.0))
    if result < minimum:
        raise ValueConversionError(f"里程{result}小于允许下限{minimum}")
    return result


def convert_value(value: Any, field: dict[str, Any]) -> Any:
    value_type = str(field.get("type", "string"))
    if value_type == "chainage":
        return _parse_chainage_km(value, field)
    if value_type in {"number", "integer"}:
        number = _convert_unit(_number(value), field.get("source_unit"), field.get("target_unit"))
        minimum = field.get("minimum")
        maximum = field.get("maximum")
        if minimum is not None and number < float(minimum):
            raise ValueConversionError(f"数值{number}小于允许下限{minimum}")
        if maximum is not None and number > float(maximum):
            raise ValueConversionError(f"数值{number}大于允许上限{maximum}")
        if value_type == "integer":
            rounded = round(number)
            if not math.isclose(number, rounded, rel_tol=0.0, abs_tol=1.0e-9):
                raise ValueConversionError(f"数值{number}不是整数")
            return int(rounded)
        return number
    if value_type == "string":
        text = str(value).strip()
        if not text:
            raise ValueConversionError("字符串不能为空")
        return text
    if value_type == "enum":
        text = str(value).strip()
        enum_map = {
            str(key).strip().casefold(): mapped
            for key, mapped in (field.get("enum_map") or {}).items()
        }
        if text.casefold() not in enum_map:
            raise ValueConversionError(f"无法识别枚举值：{value}")
        return enum_map[text.casefold()]
    if value_type == "date":
        return _parse_date(value, include_time=False)
    if value_type == "datetime":
        return _parse_date(value, include_time=True)
    if value_type == "boolean":
        if isinstance(value, bool):
            return value
        text = str(value).strip().casefold()
        truthy = {
            str(item).casefold() for item in field.get("true_values", ["true", "yes", "是", "1"])
        }
        falsy = {
            str(item).casefold() for item in field.get("false_values", ["false", "no", "否", "0"])
        }
        if text in truthy:
            return True
        if text in falsy:
            return False
        raise ValueConversionError(f"无法识别布尔值：{value}")
    raise ValueConversionError(f"不支持的字段类型：{value_type}")


__all__ = ["ValueConversionError", "convert_value"]
