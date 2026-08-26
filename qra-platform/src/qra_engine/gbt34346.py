from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .aqt3046 import (
    gas_orifice_mass_flow_rate,
    horizontal_jet_fire_threshold_distance_m,
)
from .gas_properties import gas_properties_from_case


@dataclass(frozen=True, slots=True)
class GasLeakScenario:
    scenario_id: str
    hole_diameter_mm: float
    probability_fraction: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite_nonnegative(name: str, value: float) -> float:
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{name}必须为非负有限数")
    return result


def gas_pipeline_average_failure_frequency_per_km_year(wall_thickness_mm: float) -> float:
    """GB/T 34346—2017 表C.1输气管道平均失效概率。"""
    thickness = _finite_nonnegative("wall_thickness_mm", wall_thickness_mm)
    if thickness <= 0.0:
        raise ValueError("wall_thickness_mm必须大于0")
    if thickness <= 5.0:
        return 4.0e-4
    if thickness <= 10.0:
        return 1.7e-4
    if thickness <= 15.0:
        return 8.1e-5
    return 4.1e-5


def management_correction_factor(scores: Iterable[float]) -> float:
    """GB/T 34346—2017 式(C.3)，顺序为表C.3～C.8六项管理得分。"""
    values = [float(value) for value in scores]
    if len(values) != 6:
        raise ValueError("管理措施修正因子必须提供6项得分")
    if any(not math.isfinite(value) or value < 0.0 or value > 100.0 for value in values):
        raise ValueError("管理措施各项得分必须位于[0,100]")
    return 10.0 ** (1.0 - sum(values) / 300.0)


def damage_correction_factor(
    component_factors: dict[str, float],
    weights: dict[str, float],
) -> float:
    """GB/T 34346—2017 式(C.2)的损伤修正因子FD。"""
    required = ("corrosion", "body_defect", "third_party", "manufacturing", "fatigue")
    if set(component_factors) != set(required) or set(weights) != set(required):
        raise ValueError(f"损伤因子和权重必须且只能包含：{', '.join(required)}")
    factors = {key: _finite_nonnegative(key, component_factors[key]) for key in required}
    weight_values = {key: _finite_nonnegative(f"weight.{key}", weights[key]) for key in required}
    if not math.isclose(sum(weight_values.values()), 1.0, rel_tol=0.0, abs_tol=1.0e-12):
        raise ValueError("损伤修正因子权重之和必须为1")
    return sum(factors[key] * weight_values[key] for key in required)


def pipeline_failure_probability_per_km_year(
    average_failure_frequency_per_km_year: float,
    management_factor: float,
    damage_factor: float,
) -> float:
    """GB/T 34346—2017 式(C.1)。"""
    average = _finite_nonnegative(
        "average_failure_frequency_per_km_year",
        average_failure_frequency_per_km_year,
    )
    management = _finite_nonnegative("management_factor", management_factor)
    damage = _finite_nonnegative("damage_factor", damage_factor)
    return average * management * damage


def gas_leak_scenarios(pipe_outer_diameter_mm: float) -> tuple[GasLeakScenario, ...]:
    """GB/T 34346—2017 表C.24的输气管道孔径及概率。"""
    diameter = _finite_nonnegative("pipe_outer_diameter_mm", pipe_outer_diameter_mm)
    if diameter <= 0.0:
        raise ValueError("pipe_outer_diameter_mm必须大于0")
    return (
        GasLeakScenario("small_5mm", 5.0, 0.50),
        GasLeakScenario("medium_25mm", 25.0, 0.18),
        GasLeakScenario("large_150mm", 150.0, 0.18),
        GasLeakScenario("rupture", min(diameter, 400.0), 0.14),
    )


def maximum_release_mass_kg(inventory_between_valves_kg: float, isolation_makeup_kg: float) -> float:
    """GB/T 34346—2017 式(C.16)。"""
    return _finite_nonnegative("inventory_between_valves_kg", inventory_between_valves_kg) + _finite_nonnegative(
        "isolation_makeup_kg", isolation_makeup_kg
    )


def depletion_time_s(maximum_release_mass_kg_value: float, mass_flow_rate_kg_s: float) -> float:
    """GB/T 34346—2017 式(C.17)。"""
    mass = _finite_nonnegative("maximum_release_mass_kg", maximum_release_mass_kg_value)
    flow = _finite_nonnegative("mass_flow_rate_kg_s", mass_flow_rate_kg_s)
    if flow <= 0.0:
        raise ValueError("mass_flow_rate_kg_s必须大于0")
    return mass / flow


def consequence_area_m2(threshold_distance_m: float) -> float:
    """GB/T 34346—2017 式(C.40)。"""
    radius = _finite_nonnegative("threshold_distance_m", threshold_distance_m)
    return 4.0 * math.pi * radius * radius


def weighted_average_consequence_area_m2(
    scenario_failure_frequencies_per_km_year: Iterable[float],
    scenario_consequence_areas_m2: Iterable[float],
) -> float:
    """GB/T 34346—2017 式(C.41)。"""
    frequencies = [
        _finite_nonnegative("scenario_failure_frequency", value)
        for value in scenario_failure_frequencies_per_km_year
    ]
    areas = [_finite_nonnegative("scenario_consequence_area", value) for value in scenario_consequence_areas_m2]
    if len(frequencies) != len(areas) or not frequencies:
        raise ValueError("场景频率和后果面积必须为长度相同的非空序列")
    total = sum(frequencies)
    if total <= 0.0:
        return 0.0
    return sum(frequency * area for frequency, area in zip(frequencies, areas, strict=True)) / total


def weighted_average_leak_rate_kg_s(
    scenario_failure_frequencies_per_km_year: Iterable[float],
    scenario_leak_rates_kg_s: Iterable[float],
) -> float:
    """GB/T 34346—2017 式(C.43)。"""
    frequencies = [
        _finite_nonnegative("scenario_failure_frequency", value)
        for value in scenario_failure_frequencies_per_km_year
    ]
    rates = [_finite_nonnegative("scenario_leak_rate", value) for value in scenario_leak_rates_kg_s]
    if len(frequencies) != len(rates) or not frequencies:
        raise ValueError("场景频率和泄漏速率必须为长度相同的非空序列")
    total = sum(frequencies)
    if total <= 0.0:
        return 0.0
    return sum(frequency * rate for frequency, rate in zip(frequencies, rates, strict=True)) / total


def pipeline_human_risk_fatalities_per_km_year(
    failure_probability_per_km_year: float,
    ignition_probability: float,
    human_consequence_area_m2_value: float,
    population_density_per_m2: float,
) -> float:
    """GB/T 34346—2017 式(C.42)。"""
    probability = _finite_nonnegative("failure_probability_per_km_year", failure_probability_per_km_year)
    ignition = _finite_nonnegative("ignition_probability", ignition_probability)
    if ignition > 1.0:
        raise ValueError("ignition_probability不得大于1")
    area = _finite_nonnegative("human_consequence_area_m2", human_consequence_area_m2_value)
    density = _finite_nonnegative("population_density_per_m2", population_density_per_m2)
    return probability * ignition * area * density


def calculate_annex_c_secondary_assessment(case: dict[str, Any]) -> dict[str, Any]:
    """用合成参数运行GB/T 34346附录C规范性二级评价公式。"""
    parameters = case["standard_formula_test_parameters"]["gbt34346_annex_c"]
    pipeline = case["pipeline"]
    gas_properties = (
        gas_properties_from_case(case)
        if pipeline.get("gas_composition_mole_fraction") is not None
        else None
    )
    molar_mass = (
        gas_properties.molar_mass_kg_mol
        if gas_properties is not None
        else float(parameters["molar_mass_kg_mol"])
    )
    gamma = (
        gas_properties.heat_capacity_ratio
        if gas_properties is not None
        else float(parameters["gamma"])
    )
    heat_of_combustion = (
        gas_properties.lower_heating_value_j_kg
        if gas_properties is not None
        else float(parameters["heat_of_combustion_j_kg"])
    )
    ambient = float(parameters["ambient_pressure_pa_abs"])
    operating_pressure = float(pipeline["operating_pressure_mpa"]) * 1.0e6
    if parameters["operating_pressure_basis"] == "gauge":
        operating_pressure += ambient
    elif parameters["operating_pressure_basis"] != "absolute":
        raise ValueError("operating_pressure_basis必须为gauge或absolute")

    segment_results: list[dict[str, Any]] = []
    for segment in case["segments"]:
        segment_id = segment["segment_id"]
        segment_parameters = parameters["segments"][segment_id]
        average_frequency = gas_pipeline_average_failure_frequency_per_km_year(
            float(segment["wall_thickness_mm"])
        )
        management_factor = management_correction_factor(
            segment_parameters["management_scores_c3_to_c8"]
        )
        damage_factor = damage_correction_factor(
            segment_parameters["damage_component_factors"],
            segment_parameters["damage_component_weights"],
        )
        failure_probability = pipeline_failure_probability_per_km_year(
            average_frequency,
            management_factor,
            damage_factor,
        )

        scenario_rows: list[dict[str, Any]] = []
        scenario_frequencies: list[float] = []
        scenario_areas: list[float] = []
        scenario_rates: list[float] = []
        for scenario in gas_leak_scenarios(float(segment["outside_diameter_mm"])):
            release = gas_orifice_mass_flow_rate(
                upstream_pressure_pa_abs=operating_pressure,
                downstream_pressure_pa_abs=ambient,
                temperature_k=float(pipeline["operating_temperature_k"]),
                molar_mass_kg_mol=molar_mass,
                gamma=gamma,
                discharge_coefficient=float(parameters["gas_discharge_coefficient_c13"]),
                orifice_diameter_m=scenario.hole_diameter_mm / 1000.0,
            )
            threshold_distance = horizontal_jet_fire_threshold_distance_m(
                heat_of_combustion_j_kg=heat_of_combustion,
                mass_flow_rate_kg_s=release.mass_flow_rate_kg_s,
                radiative_fraction=float(parameters["radiative_fraction"]),
                threshold_heat_flux_kw_m2=float(parameters["fatal_heat_flux_threshold_kw_m2"]),
            )
            area = consequence_area_m2(threshold_distance)
            scenario_frequency = failure_probability * scenario.probability_fraction
            scenario_frequencies.append(scenario_frequency)
            scenario_areas.append(area)
            scenario_rates.append(release.mass_flow_rate_kg_s)
            scenario_rows.append(
                {
                    **scenario.to_dict(),
                    "scenario_failure_frequency_per_km_year": scenario_frequency,
                    "mass_flow_rate_kg_s": release.mass_flow_rate_kg_s,
                    "flow_regime": release.flow_regime,
                    "fatal_heat_flux_threshold_kw_m2": float(
                        parameters["fatal_heat_flux_threshold_kw_m2"]
                    ),
                    "fatal_heat_flux_distance_m": threshold_distance,
                    "human_consequence_area_m2": area,
                }
            )

        average_area = weighted_average_consequence_area_m2(
            scenario_frequencies,
            scenario_areas,
        )
        weighted_rate = weighted_average_leak_rate_kg_s(
            scenario_frequencies,
            scenario_rates,
        )
        risk_density = pipeline_human_risk_fatalities_per_km_year(
            failure_probability,
            float(segment_parameters["point_ignition_probability"]),
            average_area,
            float(segment_parameters["population_density_per_m2"]),
        )
        segment_results.append(
            {
                "segment_id": segment_id,
                "length_km": float(segment["length_km"]),
                "average_failure_frequency_per_km_year": average_frequency,
                "management_correction_factor": management_factor,
                "damage_correction_factor": damage_factor,
                "failure_probability_per_km_year": failure_probability,
                "point_ignition_probability": float(
                    segment_parameters["point_ignition_probability"]
                ),
                "population_density_per_m2": float(
                    segment_parameters["population_density_per_m2"]
                ),
                "weighted_leak_rate_kg_s": weighted_rate,
                "weighted_human_consequence_area_m2": average_area,
                "risk_density_fatalities_per_km_year": risk_density,
                "segment_risk_fatalities_per_year": risk_density
                * float(segment["length_km"]),
                "leak_scenarios": scenario_rows,
            }
        )

    segment_results.sort(
        key=lambda row: (-row["segment_risk_fatalities_per_year"], row["segment_id"])
    )
    for index, row in enumerate(segment_results, start=1):
        row["risk_rank"] = index
    return {
        "model_id": "pipeline.gbt34346.annex_c.secondary.v1",
        "model_status": "STANDARD_FORMULAS_SYNTHETIC_PARAMETERS",
        "method_type": "QUANTITATIVE_SECONDARY_ASSESSMENT_NOT_FULL_SPATIAL_QRA",
        "source": "GB/T 34346—2017 normative Annex C",
        "formula_trace": ["C.1", "C.2", "C.3", "C.12-C.15", "C.40-C.43"],
        "parameter_data_classification": parameters["data_classification"],
        "gas_mixture_properties": (
            gas_properties.to_dict()
            if gas_properties is not None
            else {
                "property_model_id": "provided_parameter_set_fallback.v1",
                "source": "standard_formula_test_parameters.gbt34346_annex_c",
                "molar_mass_kg_mol": molar_mass,
                "heat_capacity_ratio": gamma,
                "lower_heating_value_j_kg": heat_of_combustion,
                "reason": "pipeline.gas_composition_mole_fraction未提供",
            }
        ),
        "formal_report_allowed": False,
        "formal_report_blockers": [
            "测试物性、修正因子、点火概率和人口密度不是项目实测或批准参数",
            "该配置是附录C二级评价，不替代SY/T 6891.2完整空间QRA的IR与F-N计算",
        ],
        "pipeline_risk_fatalities_per_year": sum(
            row["segment_risk_fatalities_per_year"] for row in segment_results
        ),
        "segment_ranking": segment_results,
    }


__all__ = [
    "GasLeakScenario",
    "calculate_annex_c_secondary_assessment",
    "consequence_area_m2",
    "damage_correction_factor",
    "depletion_time_s",
    "gas_leak_scenarios",
    "gas_pipeline_average_failure_frequency_per_km_year",
    "management_correction_factor",
    "maximum_release_mass_kg",
    "pipeline_failure_probability_per_km_year",
    "pipeline_human_risk_fatalities_per_km_year",
    "weighted_average_consequence_area_m2",
    "weighted_average_leak_rate_kg_s",
]
