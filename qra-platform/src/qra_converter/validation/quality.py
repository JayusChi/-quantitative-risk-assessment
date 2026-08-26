from __future__ import annotations

import math
from typing import Any

from ..contracts import ConversionIssue, IssueSeverity

TOLERANCE = 1.0e-9


def _finite(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(float(value))
    )


def _duplicate_record_issues(case: dict[str, Any]) -> list[ConversionIssue]:
    issues: list[ConversionIssue] = []
    raw_categories = case.get("raw_data_categories", {})
    if not isinstance(raw_categories, dict):
        return issues
    for category_id, category in raw_categories.items():
        if not isinstance(category, dict):
            continue
        seen: set[str] = set()
        for index, record in enumerate(category.get("records", [])):
            if not isinstance(record, dict):
                continue
            record_id = record.get("record_id") or record.get("target_id")
            if not record_id:
                issues.append(
                    ConversionIssue(
                        IssueSeverity.ERROR,
                        "RECORD_ID_MISSING",
                        f"数据类别{category_id}的记录主键为空",
                        target_path=f"raw_data_categories.{category_id}.records[{index}]",
                    )
                )
            elif str(record_id) in seen:
                issues.append(
                    ConversionIssue(
                        IssueSeverity.ERROR,
                        "RECORD_ID_DUPLICATE",
                        f"数据类别{category_id}的记录主键重复：{record_id}",
                        target_path=f"raw_data_categories.{category_id}.records[{index}]",
                    )
                )
            else:
                seen.add(str(record_id))
    return issues


def validate_conversion_quality(case: dict[str, Any]) -> tuple[ConversionIssue, ...]:
    issues: list[ConversionIssue] = []
    segments = case.get("segments")
    if not isinstance(segments, list) or not segments:
        return (
            ConversionIssue(
                IssueSeverity.ERROR,
                "SEGMENTS_EMPTY",
                "至少需要一个有效管段",
                target_path="segments",
            ),
        )
    seen: set[str] = set()
    previous_end: float | None = None
    length_sum = 0.0
    for index, segment in enumerate(segments):
        path = f"segments[{index}]"
        segment_id = segment.get("segment_id")
        if not isinstance(segment_id, str) or not segment_id.strip():
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    "SEGMENT_ID_MISSING",
                    "管段ID不能为空",
                    target_path=f"{path}.segment_id",
                )
            )
        elif segment_id in seen:
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    "SEGMENT_ID_DUPLICATE",
                    f"管段ID重复：{segment_id}",
                    target_path=f"{path}.segment_id",
                )
            )
        else:
            seen.add(segment_id)
        start = segment.get("start_km")
        end = segment.get("end_km")
        length = segment.get("length_km")
        if not _finite(start) or not _finite(end) or float(end) <= float(start):
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    "SEGMENT_CHAINAGE_INVALID",
                    "管段起止里程无效",
                    target_path=path,
                )
            )
            continue
        if not _finite(length) or float(length) <= 0:
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    "SEGMENT_LENGTH_INVALID",
                    "管段长度必须大于0",
                    target_path=f"{path}.length_km",
                )
            )
            continue
        length_sum += float(length)
        if not math.isclose(
            float(end) - float(start), float(length), abs_tol=TOLERANCE, rel_tol=0.0
        ):
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    "SEGMENT_LENGTH_MISMATCH",
                    "管段长度与起止里程不一致",
                    target_path=path,
                )
            )
        if previous_end is not None:
            if float(start) < previous_end and not math.isclose(
                float(start), previous_end, abs_tol=TOLERANCE, rel_tol=0.0
            ):
                issues.append(
                    ConversionIssue(
                        IssueSeverity.ERROR,
                        "SEGMENT_OVERLAP",
                        "相邻管段里程重叠",
                        target_path=f"{path}.start_km",
                    )
                )
            elif float(start) > previous_end and not math.isclose(
                float(start), previous_end, abs_tol=TOLERANCE, rel_tol=0.0
            ):
                issues.append(
                    ConversionIssue(
                        IssueSeverity.ERROR,
                        "SEGMENT_GAP",
                        "相邻管段存在里程断点",
                        target_path=f"{path}.start_km",
                    )
                )
        previous_end = float(end)
    total_length = case.get("pipeline", {}).get("total_length_km")
    if total_length is not None and (
        not _finite(total_length)
        or not math.isclose(length_sum, float(total_length), abs_tol=TOLERANCE, rel_tol=0.0)
    ):
        issues.append(
            ConversionIssue(
                IssueSeverity.ERROR,
                "SEGMENT_LENGTH_SUM_MISMATCH",
                f"管段长度合计{length_sum} km与管线总长{total_length} km不一致",
                target_path="segments",
            )
        )
    issues.extend(_duplicate_record_issues(case))
    return tuple(issues)


__all__ = ["validate_conversion_quality"]
