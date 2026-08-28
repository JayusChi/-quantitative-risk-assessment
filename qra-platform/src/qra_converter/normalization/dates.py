"""ISO-8601 dates with explicit source precision."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any


def normalize_date(value: Any) -> dict[str, str]:
    if isinstance(value, datetime):
        return {"value": value.isoformat(), "precision": "SECOND"}
    if isinstance(value, date):
        return {"value": value.isoformat(), "precision": "DAY"}
    text = str(value).strip()
    if re.fullmatch(r"\d{4}", text):
        return {"value": text, "precision": "YEAR"}
    month = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})月?", text)
    if month:
        year, month_number = int(month.group(1)), int(month.group(2))
        if not 1 <= month_number <= 12:
            raise ValueError("月份超出范围")
        return {"value": f"{year:04d}-{month_number:02d}", "precision": "MONTH"}
    day = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", text)
    if day:
        parsed = date(int(day.group(1)), int(day.group(2)), int(day.group(3)))
        return {"value": parsed.isoformat(), "precision": "DAY"}
    try:
        parsed_datetime = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"无法解析日期：{text}") from exc
    return {"value": parsed_datetime.isoformat(), "precision": "SECOND"}


__all__ = ["normalize_date"]
