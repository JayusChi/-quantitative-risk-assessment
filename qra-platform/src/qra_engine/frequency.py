from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from .frequency_correction import (
    CorrectionFactorResolution,
    resolve_segment_correction_factors,
)


@dataclass(frozen=True, slots=True)
class LocFrequency:
    segment_id: str
    loc_id: str
    annual_frequency: float
    mechanism_contribution: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LeakPoint:
    leak_point_id: str
    segment_id: str
    sequence: int
    x_m: float
    y_m: float
    chainage_km: float
    frequency_share: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_loc_frequencies(
    case: dict[str, Any],
    correction_resolution: CorrectionFactorResolution | None = None,
) -> list[LocFrequency]:
    library = case["frequency_library"]
    base_by_mechanism = library["base_frequency_by_mechanism"]
    fractions_by_mechanism = library["loc_fraction_by_mechanism"]
    correction_by_segment = (
        correction_resolution or resolve_segment_correction_factors(case)
    ).factors_by_segment
    results: list[LocFrequency] = []

    loc_ids = tuple(next(iter(fractions_by_mechanism.values())).keys())
    for segment in case["segments"]:
        segment_id = segment["segment_id"]
        length_km = float(segment["length_km"])
        factors = correction_by_segment[segment_id]
        for loc_id in loc_ids:
            contributions: dict[str, float] = {}
            for mechanism, base_frequency in base_by_mechanism.items():
                contributions[mechanism] = (
                    float(base_frequency)
                    * float(factors[mechanism])
                    * length_km
                    * float(fractions_by_mechanism[mechanism][loc_id])
                )
            results.append(
                LocFrequency(
                    segment_id=segment_id,
                    loc_id=loc_id,
                    annual_frequency=sum(contributions.values()),
                    mechanism_contribution=contributions,
                )
            )
    return results


def discretize_segment(segment: dict[str, Any], spacing_m: float) -> list[LeakPoint]:
    if spacing_m <= 0:
        raise ValueError("leak point spacing must be greater than zero")

    length_m = float(segment["length_km"]) * 1000.0
    point_count = max(1, math.ceil(length_m / spacing_m))
    start_x, start_y = (float(value) for value in segment["start_xy_m"])
    end_x, end_y = (float(value) for value in segment["end_xy_m"])
    start_km = float(segment["start_km"])
    segment_id = segment["segment_id"]

    points: list[LeakPoint] = []
    for index in range(point_count):
        fraction = (index + 0.5) / point_count
        points.append(
            LeakPoint(
                leak_point_id=f"{segment_id}-LP-{index + 1:04d}",
                segment_id=segment_id,
                sequence=index + 1,
                x_m=start_x + (end_x - start_x) * fraction,
                y_m=start_y + (end_y - start_y) * fraction,
                chainage_km=start_km + float(segment["length_km"]) * fraction,
                frequency_share=1.0 / point_count,
            )
        )
    return points
