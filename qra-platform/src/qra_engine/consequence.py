from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .aqt3046 import ExposureContext


@dataclass(frozen=True, slots=True)
class ScenarioContext:
    segment_id: str
    loc_id: str
    branch_id: str
    leak_x_m: float
    leak_y_m: float
    leak_chainage_km: float
    release_rate_kg_s: float
    stability_class: str
    wind_speed_m_s: float
    wind_direction_from: str


@dataclass(frozen=True, slots=True)
class HumanEffect:
    fatality_probability: float
    effect_metric: str
    effect_value: float
    effect_unit: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HumanConsequenceAdapter(Protocol):
    model_id: str
    model_version: str
    status: str

    def release_rate_kg_s(
        self,
        loc_id: str,
        *,
        segment_id: str,
        leak_chainage_km: float,
    ) -> float: ...

    def evaluate(
        self,
        scenario: ScenarioContext,
        receptor_xy_m: list[float],
        *,
        exposure_context: ExposureContext,
    ) -> HumanEffect: ...


class SyntheticHumanConsequenceAdapter:
    """确定性的空间测试模型，只用于验证计算链和聚合不变量。"""

    def __init__(self, spec_path: Path):
        self.spec_path = spec_path
        self.spec = json.loads(spec_path.read_text(encoding="utf-8"))
        self.model_id = self.spec["model_id"]
        self.model_version = self.spec["version"]
        self.status = self.spec["status"]

    def release_rate_kg_s(
        self,
        loc_id: str,
        *,
        segment_id: str,
        leak_chainage_km: float,
    ) -> float:
        del segment_id, leak_chainage_km
        return float(self.spec["release_rate_kg_s_by_loc"][loc_id])

    @staticmethod
    def _wind_to_vector(direction_from: str) -> tuple[float, float]:
        vectors = {
            "N": (0.0, -1.0),
            "NE": (-math.sqrt(0.5), -math.sqrt(0.5)),
            "E": (-1.0, 0.0),
            "SE": (-math.sqrt(0.5), math.sqrt(0.5)),
            "S": (0.0, 1.0),
            "SW": (math.sqrt(0.5), math.sqrt(0.5)),
            "W": (1.0, 0.0),
            "NW": (math.sqrt(0.5), -math.sqrt(0.5)),
        }
        try:
            return vectors[direction_from.upper()]
        except KeyError as exc:
            raise ValueError(f"unsupported wind direction: {direction_from}") from exc

    def _scales(self, scenario: ScenarioContext) -> tuple[float, float]:
        loc_scale = float(self.spec["loc_scale"][scenario.loc_id])
        weather_scale = float(self.spec["weather_scale_by_stability"].get(scenario.stability_class, 1.0))
        return loc_scale, weather_scale

    def evaluate(
        self,
        scenario: ScenarioContext,
        receptor_xy_m: list[float],
        *,
        exposure_context: ExposureContext,
    ) -> HumanEffect:
        del exposure_context
        receptor_x, receptor_y = (float(value) for value in receptor_xy_m)
        dx = receptor_x - scenario.leak_x_m
        dy = receptor_y - scenario.leak_y_m
        distance = math.hypot(dx, dy)
        loc_scale, weather_scale = self._scales(scenario)

        if scenario.branch_id == "safe_dispersion":
            return HumanEffect(0.0, "none", 0.0, "dimensionless")

        if scenario.branch_id == "jet_fire":
            model = self.spec["jet_fire"]
            radius = float(model["base_distance_m"]) * loc_scale * math.sqrt(weather_scale)
            if distance <= radius * 0.5:
                probability = float(model["probability_near"])
            elif distance <= radius:
                probability = float(model["probability_mid"])
            else:
                probability = 0.0
            return HumanEffect(probability, "distance", distance, "m")

        if scenario.branch_id == "flash_fire":
            model = self.spec["flash_fire"]
            wind_factor = max(0.75, min(1.5, 3.0 / max(scenario.wind_speed_m_s, 0.1)))
            cloud_length = float(model["base_cloud_length_m"]) * loc_scale * weather_scale * wind_factor
            cloud_half_width = float(model["base_cloud_half_width_m"]) * math.sqrt(loc_scale * weather_scale)
            wind_x, wind_y = self._wind_to_vector(scenario.wind_direction_from)
            along = dx * wind_x + dy * wind_y
            cross = abs(-dx * wind_y + dy * wind_x)
            center = 0.35 * cloud_length
            radius_along = 0.65 * cloud_length
            inside = ((along - center) / radius_along) ** 2 + (cross / cloud_half_width) ** 2 <= 1.0
            probability = float(model["inside_probability"]) if inside else 0.0
            return HumanEffect(probability, "inside_lfl_cloud", 1.0 if inside else 0.0, "boolean")

        if scenario.branch_id == "vce":
            model = self.spec["vce"]
            decay_distance = float(model["decay_distance_m"]) * loc_scale * math.sqrt(weather_scale)
            overpressure = float(model["peak_overpressure_kpa"]) * loc_scale / (1.0 + (distance / decay_distance) ** 2)
            if overpressure >= float(model["high_threshold_kpa"]):
                probability = float(model["high_probability"])
            elif overpressure >= float(model["low_threshold_kpa"]):
                probability = float(model["low_probability"])
            else:
                probability = 0.0
            return HumanEffect(probability, "overpressure", overpressure, "kPa")

        raise ValueError(f"unsupported consequence branch: {scenario.branch_id}")
