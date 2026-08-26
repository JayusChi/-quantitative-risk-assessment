from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from ..contracts import ConversionIssue, IssueSeverity

TOLERANCE = 1.0e-9


def _finite(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(float(value))
    )


def _by_chainage(chainage: float, segments: list[dict[str, Any]]) -> list[str]:
    matches: list[str] = []
    last_index = len(segments) - 1
    for index, segment in enumerate(segments):
        start = segment.get("start_km")
        end = segment.get("end_km")
        if not _finite(start) or not _finite(end):
            continue
        start_value = float(start)
        end_value = float(end)
        at_or_after_start = chainage > start_value or math.isclose(
            chainage, start_value, abs_tol=TOLERANCE, rel_tol=0.0
        )
        before_end = chainage < end_value and not math.isclose(
            chainage, end_value, abs_tol=TOLERANCE, rel_tol=0.0
        )
        at_last_end = index == last_index and math.isclose(
            chainage, end_value, abs_tol=TOLERANCE, rel_tol=0.0
        )
        if at_or_after_start and (before_end or at_last_end):
            matches.append(str(segment.get("segment_id", "")))
    return [segment_id for segment_id in matches if segment_id]


def attach_segments(
    records: Iterable[dict[str, Any]],
    segments: list[dict[str, Any]],
    *,
    target_path: str,
) -> tuple[list[dict[str, Any]], list[ConversionIssue]]:
    """Attach records by valid ID, otherwise by start-inclusive chainage."""

    segment_ids = {
        str(segment["segment_id"])
        for segment in segments
        if isinstance(segment.get("segment_id"), str) and segment["segment_id"]
    }
    attached: list[dict[str, Any]] = []
    issues: list[ConversionIssue] = []
    for index, source_record in enumerate(records):
        record = dict(source_record)
        source_ref = record.get("source_ref", {})
        source_id = source_ref.get("file_sha256")
        location = (
            f"{source_ref.get('file_name')}/{source_ref.get('sheet_name')}"
            f"!R{source_ref.get('row_number')}"
        )
        segment_id = record.get("segment_id")
        if segment_id in segment_ids:
            attached.append(record)
            continue
        chainage = record.get("chainage_km")
        if not _finite(chainage):
            code = "SEGMENT_ID_UNKNOWN" if segment_id else "SEGMENT_REFERENCE_MISSING"
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    code,
                    (
                        f"未知管段ID：{segment_id}，且没有有效里程可回退匹配"
                        if segment_id
                        else "记录既无有效segment_id，也无有效chainage_km"
                    ),
                    source_id,
                    location,
                    f"{target_path}[{index}].segment_id",
                )
            )
            attached.append(record)
            continue
        matches = _by_chainage(float(chainage), segments)
        if len(matches) == 1:
            if segment_id:
                issues.append(
                    ConversionIssue(
                        IssueSeverity.WARNING,
                        "SEGMENT_ID_INVALID_FALLBACK",
                        f"未知管段ID {segment_id}，已按里程匹配到{matches[0]}",
                        source_id,
                        location,
                        f"{target_path}[{index}].segment_id",
                    )
                )
            record["segment_id"] = matches[0]
        elif not matches:
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    "CHAINAGE_OUT_OF_RANGE",
                    f"里程{chainage} km不在任何管段范围内",
                    source_id,
                    location,
                    f"{target_path}[{index}].chainage_km",
                )
            )
        else:
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    "CHAINAGE_MATCH_AMBIGUOUS",
                    f"里程{chainage} km同时匹配多个管段：{', '.join(matches)}",
                    source_id,
                    location,
                    f"{target_path}[{index}].chainage_km",
                )
            )
        attached.append(record)
    return attached, issues


__all__ = ["attach_segments"]
