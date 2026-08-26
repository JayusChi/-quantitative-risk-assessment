from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from .aqt3046 import (
    ExposureContext,
    R_UNIVERSAL_J_MOL_K,
    adiabatic_pipe_rupture_mass_flow_rate,
    corrected_thermal_fatality_probability,
    fanning_friction_factor_fully_rough,
    flash_fire_fatality_probability,
    gas_orifice_mass_flow_rate,
    gaussian_plume_concentration_kg_m3,
    horizontal_jet_fire_heat_flux_kw_m2,
    horizontal_jet_fire_threshold_distance_m,
    horizontal_jet_flame_length_m,
    tno_explosion_energy_j,
    tno_overpressure_kpa,
    tno_sachs_scaled_distance,
    tno_scaled_overpressure_from_curve,
    vce_fatality_probability,
)
from .consequence import HumanEffect, ScenarioContext
from .gas_properties import gas_properties_from_case


DEFAULT_SPEC_PATH = Path(__file__).resolve().parent / "model_specs" / "human_aqt3046_v1.json"


class AQT3046PipelineConsequenceAdapter:
    """SY/T 6891.2调用AQ/T 3046公式的天然气管道人员后果适配器。"""

    def __init__(self, case: dict[str, Any], spec_path: Path = DEFAULT_SPEC_PATH):
        self.case = case
        self.spec_path = spec_path
        self.spec = json.loads(spec_path.read_text(encoding="utf-8"))
        self.model_id = self.spec["model_id"]
        self.model_version = self.spec["version"]
        self.status = self.spec["status"]
        try:
            self.parameters = case["standard_formula_test_parameters"][
                "aqt3046_physical_chain"
            ]
        except KeyError as exc:
            raise ValueError(
                "缺少standard_formula_test_parameters.aqt3046_physical_chain"
            ) from exc

        self.pipeline = case["pipeline"]
        self.segments = {row["segment_id"]: row for row in case["segments"]}
        self.segment_parameters = self.parameters["segments"]
        missing_segments = set(self.segments) - set(self.segment_parameters)
        if missing_segments:
            raise ValueError(f"人员后果参数缺少管段：{sorted(missing_segments)}")

        self.ambient_pressure_pa = float(self.parameters["ambient_pressure_pa_abs"])
        self.ambient_temperature_k = float(
            self.parameters.get(
                "ambient_temperature_k", self.pipeline["operating_temperature_k"]
            )
        )
        self.gas_properties = gas_properties_from_case(case)
        self.molar_mass_kg_mol = self.gas_properties.molar_mass_kg_mol
        self.gamma = self.gas_properties.heat_capacity_ratio
        self.discharge_coefficient = float(self.parameters["gas_discharge_coefficient"])
        self.heat_of_combustion_j_kg = (
            self.gas_properties.lower_heating_value_j_kg
        )
        self.radiative_fraction = float(self.parameters["radiative_fraction"])
        self.lfl_volume_fraction = self.gas_properties.lfl_volume_fraction
        self.effective_release_height_m = float(
            self.parameters["effective_release_height_m"]
        )
        self.thermal_exposure_time_s = float(
            self.parameters["thermal_exposure_time_s"]
        )
        self.receptor_height_m = float(case["assessment"]["reference_height_m"])
        self.minimum_effect_distance_m = float(
            self.parameters.get("minimum_effect_distance_m", 1.0)
        )
        self.pipe_absolute_roughness_m = (
            float(self.parameters["pipe_absolute_roughness_mm"]) / 1000.0
        )
        self.minimum_rupture_flow_length_m = float(
            self.parameters.get("minimum_rupture_flow_length_m", 1.0)
        )
        self.loc_orifice_diameter_m = {
            key: float(value)
            for key, value in self.spec["loc_orifice_diameter_m"].items()
        }
        self.tno_curves = {
            int(strength): [(float(x), float(y)) for x, y in points]
            for strength, points in self.spec["tno_sachs_curves"].items()
        }
        self._validate_parameters()

        ignition_parameters = self.parameters["ignition_model"]
        self.ignition_model = {
            "model_status": ignition_parameters["model_status"],
            "material_reactivity_class": ignition_parameters[
                "material_reactivity_class"
            ],
            "immediate_ignition_probability": case["ignition_model"][
                "immediate_ignition_probability"
            ],
            "delayed_ignition_sources_by_activity": ignition_parameters[
                "delayed_ignition_sources_by_activity"
            ],
            "vce_given_delayed_probability": ignition_parameters[
                "vce_given_delayed_probability"
            ],
        }
        self._release_cache: dict[tuple[str, str, float], float] = {}
        self._release_values_by_loc: defaultdict[str, list[float]] = defaultdict(list)

    def _validate_parameters(self) -> None:
        positive_values = {
            "ambient_pressure_pa_abs": self.ambient_pressure_pa,
            "ambient_temperature_k": self.ambient_temperature_k,
            "molar_mass_kg_mol": self.molar_mass_kg_mol,
            "gamma": self.gamma,
            "gas_discharge_coefficient": self.discharge_coefficient,
            "heat_of_combustion_j_kg": self.heat_of_combustion_j_kg,
            "radiative_fraction": self.radiative_fraction,
            "lfl_volume_fraction": self.lfl_volume_fraction,
            "thermal_exposure_time_s": self.thermal_exposure_time_s,
            "pipe_absolute_roughness_m": self.pipe_absolute_roughness_m,
        }
        for name, value in positive_values.items():
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name}必须为大于0的有限数")
        if self.gamma <= 1.0:
            raise ValueError("gamma必须大于1")
        for name, value in (
            ("gas_discharge_coefficient", self.discharge_coefficient),
            ("radiative_fraction", self.radiative_fraction),
            ("lfl_volume_fraction", self.lfl_volume_fraction),
        ):
            if value > 1.0:
                raise ValueError(f"{name}不得大于1")
        if self.parameters.get("jet_direction_mode") != "radial_worst_case_envelope":
            raise ValueError("首版真实链只支持radial_worst_case_envelope喷射火方向包络")
        for segment_id, parameters in self.segment_parameters.items():
            strength = int(parameters["tno_source_strength"])
            if strength not in self.tno_curves:
                raise ValueError(f"{segment_id}的TNO强度{strength}没有数据化曲线")
            if float(parameters["explosion_source_volume_m3"]) <= 0.0:
                raise ValueError(f"{segment_id}的explosion_source_volume_m3必须大于0")
            if parameters["terrain"] not in ("rural", "urban"):
                raise ValueError(f"{segment_id}的terrain必须为rural或urban")

    @property
    def parameter_data_classification(self) -> str:
        return str(self.parameters["data_classification"])

    def _upstream_pressure_pa_abs(self) -> float:
        pressure = float(self.pipeline["operating_pressure_mpa"]) * 1.0e6
        if self.parameters.get("operating_pressure_basis", "gauge") == "gauge":
            pressure += self.ambient_pressure_pa
        return pressure

    def _inner_diameter_m(self, segment: dict[str, Any]) -> float:
        return (
            float(segment["outside_diameter_mm"])
            - 2.0 * float(segment["wall_thickness_mm"])
        ) / 1000.0

    def _orifice_release_rate(self, loc_id: str) -> float:
        return gas_orifice_mass_flow_rate(
            upstream_pressure_pa_abs=self._upstream_pressure_pa_abs(),
            downstream_pressure_pa_abs=self.ambient_pressure_pa,
            temperature_k=float(self.pipeline["operating_temperature_k"]),
            molar_mass_kg_mol=self.molar_mass_kg_mol,
            gamma=self.gamma,
            discharge_coefficient=self.discharge_coefficient,
            orifice_diameter_m=self.loc_orifice_diameter_m[loc_id],
        ).mass_flow_rate_kg_s

    def _rupture_release_rate(self, segment_id: str, chainage_km: float) -> float:
        segment = self.segments[segment_id]
        inner_diameter = self._inner_diameter_m(segment)
        friction = fanning_friction_factor_fully_rough(
            inner_diameter_m=inner_diameter,
            absolute_roughness_m=self.pipe_absolute_roughness_m,
        )
        lengths = (
            max(
                (chainage_km - float(segment["upstream_valve_km"])) * 1000.0,
                self.minimum_rupture_flow_length_m,
            ),
            max(
                (float(segment["downstream_valve_km"]) - chainage_km) * 1000.0,
                self.minimum_rupture_flow_length_m,
            ),
        )
        return sum(
            adiabatic_pipe_rupture_mass_flow_rate(
                upstream_pressure_pa_abs=self._upstream_pressure_pa_abs(),
                upstream_temperature_k=float(self.pipeline["operating_temperature_k"]),
                molar_mass_kg_mol=self.molar_mass_kg_mol,
                gamma=self.gamma,
                inner_diameter_m=inner_diameter,
                effective_length_m=length,
                fanning_friction_factor=friction,
            ).mass_flow_rate_kg_s
            for length in lengths
        )

    def release_rate_kg_s(
        self,
        loc_id: str,
        *,
        segment_id: str,
        leak_chainage_km: float,
    ) -> float:
        key = (loc_id, segment_id, round(float(leak_chainage_km), 12))
        if key in self._release_cache:
            return self._release_cache[key]
        if loc_id == "rupture":
            value = self._rupture_release_rate(segment_id, leak_chainage_km)
        else:
            try:
                value = self._orifice_release_rate(loc_id)
            except KeyError as exc:
                raise ValueError(f"未知泄漏孔径场景：{loc_id}") from exc
        self._release_cache[key] = value
        self._release_values_by_loc[loc_id].append(value)
        return value

    @staticmethod
    def _wind_to_vector(direction_from: str) -> tuple[float, float]:
        root_half = math.sqrt(0.5)
        vectors = {
            "N": (0.0, -1.0),
            "NE": (-root_half, -root_half),
            "E": (-1.0, 0.0),
            "SE": (-root_half, root_half),
            "S": (0.0, 1.0),
            "SW": (root_half, root_half),
            "W": (1.0, 0.0),
            "NW": (root_half, -root_half),
        }
        try:
            return vectors[direction_from.upper()]
        except KeyError as exc:
            raise ValueError(f"不支持的风向：{direction_from}") from exc

    def _coordinates(
        self, scenario: ScenarioContext, receptor_xy_m: list[float]
    ) -> tuple[float, float, float]:
        receptor_x, receptor_y = (float(value) for value in receptor_xy_m)
        dx = receptor_x - scenario.leak_x_m
        dy = receptor_y - scenario.leak_y_m
        wind_x, wind_y = self._wind_to_vector(scenario.wind_direction_from)
        downwind = dx * wind_x + dy * wind_y
        crosswind = -dx * wind_y + dy * wind_x
        return downwind, crosswind, math.hypot(dx, dy)

    def _lfl_mass_concentration_kg_m3(self) -> float:
        return (
            self.lfl_volume_fraction
            * self.ambient_pressure_pa
            * self.molar_mass_kg_mol
            / (R_UNIVERSAL_J_MOL_K * self.ambient_temperature_k)
        )

    def _jet_fire_effect(
        self,
        scenario: ScenarioContext,
        radial_distance_m: float,
        exposure_context: ExposureContext,
    ) -> HumanEffect:
        flame_length = horizontal_jet_flame_length_m(
            self.heat_of_combustion_j_kg,
            scenario.release_rate_kg_s,
        )
        point_source_offset = 0.8 * flame_length
        distance_from_point_source = max(
            abs(radial_distance_m - point_source_offset),
            self.minimum_effect_distance_m,
        )
        heat_flux = horizontal_jet_fire_heat_flux_kw_m2(
            heat_of_combustion_j_kg=self.heat_of_combustion_j_kg,
            mass_flow_rate_kg_s=scenario.release_rate_kg_s,
            radiative_fraction=self.radiative_fraction,
            distance_from_point_source_m=distance_from_point_source,
        )
        inside_fire_zone = radial_distance_m <= flame_length
        probability = corrected_thermal_fatality_probability(
            heat_flux,
            self.thermal_exposure_time_s,
            exposure_context,
            inside_fire_zone=inside_fire_zone,
        )
        return HumanEffect(
            probability,
            "thermal_radiation",
            heat_flux,
            "kW/m2",
            {
                "model": "AQ/T 3046 E.57-E.59 and clauses 10.3.1-10.3.2",
                "flame_length_m": flame_length,
                "point_source_offset_m": point_source_offset,
                "distance_from_point_source_m": distance_from_point_source,
                "inside_fire_zone": inside_fire_zone,
                "jet_direction_mode": self.parameters["jet_direction_mode"],
            },
        )

    def _flash_fire_effect(
        self,
        scenario: ScenarioContext,
        downwind_distance_m: float,
        crosswind_distance_m: float,
    ) -> HumanEffect:
        segment_parameters = self.segment_parameters[scenario.segment_id]
        concentration = gaussian_plume_concentration_kg_m3(
            source_mass_rate_kg_s=scenario.release_rate_kg_s,
            wind_speed_m_s=scenario.wind_speed_m_s,
            downwind_distance_m=downwind_distance_m,
            crosswind_distance_m=crosswind_distance_m,
            receptor_height_m=self.receptor_height_m,
            effective_release_height_m=self.effective_release_height_m,
            stability_class=scenario.stability_class,
            terrain=segment_parameters["terrain"],
        )
        lfl = self._lfl_mass_concentration_kg_m3()
        inside = concentration >= lfl
        return HumanEffect(
            flash_fire_fatality_probability(inside),
            "flammable_gas_concentration",
            concentration,
            "kg/m3",
            {
                "model": "AQ/T 3046 E.33 and clause 10.4.1",
                "lfl_mass_concentration_kg_m3": lfl,
                "inside_lfl_flame_envelope": inside,
                "downwind_distance_m": downwind_distance_m,
                "crosswind_distance_m": crosswind_distance_m,
                "terrain": segment_parameters["terrain"],
            },
        )

    def _vce_effect(
        self,
        scenario: ScenarioContext,
        radial_distance_m: float,
        exposure_context: ExposureContext,
    ) -> HumanEffect:
        segment_parameters = self.segment_parameters[scenario.segment_id]
        volume = float(segment_parameters["explosion_source_volume_m3"])
        strength = int(segment_parameters["tno_source_strength"])
        curve = self.tno_curves[strength]
        distance = max(radial_distance_m, self.minimum_effect_distance_m)
        energy = tno_explosion_energy_j(volume)
        scaled_distance = tno_sachs_scaled_distance(
            distance_m=distance,
            explosion_energy_j=energy,
            ambient_pressure_pa=self.ambient_pressure_pa,
        )
        scaled_overpressure = tno_scaled_overpressure_from_curve(
            scaled_distance,
            curve,
        )
        overpressure = tno_overpressure_kpa(
            distance_m=distance,
            explosion_source_volume_m3=volume,
            ambient_pressure_pa=self.ambient_pressure_pa,
            curve_points=curve,
        )
        return HumanEffect(
            vce_fatality_probability(overpressure, exposure_context),
            "peak_overpressure",
            overpressure,
            "kPa",
            {
                "model": "AQ/T 3046 E.60-E.62, Figure E.1 and clause 10.4.2",
                "explosion_source_volume_m3": volume,
                "explosion_energy_j": energy,
                "tno_source_strength": strength,
                "sachs_scaled_distance": scaled_distance,
                "sachs_scaled_overpressure": scaled_overpressure,
                "curve_status": self.spec["tno_curve_status"],
            },
        )

    def evaluate(
        self,
        scenario: ScenarioContext,
        receptor_xy_m: list[float],
        *,
        exposure_context: ExposureContext,
    ) -> HumanEffect:
        downwind, crosswind, radial = self._coordinates(scenario, receptor_xy_m)
        if scenario.branch_id == "safe_dispersion":
            return HumanEffect(0.0, "none", 0.0, "dimensionless")
        if scenario.branch_id == "jet_fire":
            return self._jet_fire_effect(scenario, radial, exposure_context)
        if scenario.branch_id == "flash_fire":
            return self._flash_fire_effect(scenario, downwind, crosswind)
        if scenario.branch_id == "vce":
            return self._vce_effect(scenario, radial, exposure_context)
        raise ValueError(f"不支持的后果分支：{scenario.branch_id}")

    def scenario_summary_metrics(self, scenario: ScenarioContext) -> dict[str, Any]:
        """Return reportable distances using the same parameters as evaluation."""
        if scenario.branch_id != "jet_fire":
            return {}
        flame_length = horizontal_jet_flame_length_m(
            self.heat_of_combustion_j_kg,
            scenario.release_rate_kg_s,
        )
        point_source_offset = 0.8 * flame_length
        threshold_from_point_source = horizontal_jet_fire_threshold_distance_m(
            heat_of_combustion_j_kg=self.heat_of_combustion_j_kg,
            mass_flow_rate_kg_s=scenario.release_rate_kg_s,
            radiative_fraction=self.radiative_fraction,
            threshold_heat_flux_kw_m2=37.5,
        )
        return {
            "fatal_heat_flux_threshold_kw_m2": 37.5,
            "fatal_heat_flux_distance_m": max(
                flame_length,
                point_source_offset + threshold_from_point_source,
            ),
            "fatal_heat_flux_distance_basis": (
                "radial worst-case envelope from leak: max(flame length, "
                "point-source offset + 37.5 kW/m2 distance)"
            ),
        }

    def model_diagnostics(self) -> dict[str, Any]:
        release_summary = {
            loc_id: {
                "minimum_kg_s": min(values),
                "maximum_kg_s": max(values),
                "evaluated_leak_point_count": len(values),
            }
            for loc_id, values in sorted(self._release_values_by_loc.items())
            if values
        }
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "model_status": self.status,
            "parameter_data_classification": self.parameter_data_classification,
            "gas_mixture_properties": self.gas_properties.to_dict(),
            "release_rate_summary": release_summary,
            "formula_trace": self.spec["formula_trace"],
            "assumptions_and_limitations": self.spec["assumptions_and_limitations"],
            "external_software_dependency": None,
        }


__all__ = ["AQT3046PipelineConsequenceAdapter"]
