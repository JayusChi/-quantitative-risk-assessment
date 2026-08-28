from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from .aqt3046 import (
    corrected_thermal_fatality_probability,
    gas_orifice_mass_flow_rate,
    horizontal_jet_fire_heat_flux_kw_m2,
    horizontal_jet_fire_threshold_distance_m,
)
from .gbt34346 import (
    consequence_area_m2,
    gas_leak_scenarios,
    gas_pipeline_average_failure_frequency_per_km_year,
)
from .gas_properties import calculate_gas_mixture_properties
from .indicators import load_indicator_catalog, resolve_indicator_value
from .model_registry import MODEL_SPEC_ROOT


MODEL_SPEC_PATH = MODEL_SPEC_ROOT / "adaptive_evidence_qra_v1.json"


def _load_spec() -> dict[str, Any]:
    return json.loads(MODEL_SPEC_PATH.read_text(encoding="utf-8"))


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _raw_segment_records(
    case: dict[str, Any], category_id: str, segment_id: str
) -> list[dict[str, Any]]:
    records = (
        case.get("raw_data_categories", {})
        .get(category_id, {})
        .get("records", [])
    )
    return [row for row in records if str(row.get("segment_id", "")) == segment_id]


def _raw_indicator_value(
    case: dict[str, Any], segment_id: str, indicator_id: str
) -> float | None:
    mapping: dict[str, tuple[str, tuple[str, ...], str]] = {
        "external_corrosion.cp_off_potential_v": (
            "synchronized_interruption_potential",
            ("instant_off_potential_v_cse",),
            "max",
        ),
        "external_corrosion.cp_out_of_range_fraction": (
            "cips_dense_interval_potential",
            ("out_of_range_fraction",),
            "max",
        ),
        "external_corrosion.dcvg_acvg_severity_max": (
            "dcvg_coating_inspection",
            ("maximum_ir_percent", "ir_percent"),
            "max",
        ),
        "external_corrosion.soil_resistivity_ohm_m": (
            "soil_environment",
            ("soil_resistivity_ohm_m",),
            "min",
        ),
        "external_corrosion.ac_interference_voltage_v": (
            "coupon_ac_dc_current_density",
            ("ac_voltage_v",),
            "max",
        ),
        "inspection_integrity.maximum_corrosion_depth_ratio": (
            "inline_inspection_defects",
            ("maximum_corrosion_depth_percent", "depth_percent_wall"),
            "percent_max",
        ),
        "inspection_integrity.unrepaired_critical_anomaly_count": (
            "inline_inspection_defects",
            ("unrepaired_critical_anomaly_count",),
            "max",
        ),
    }
    if indicator_id == "management_emergency.maintenance_completion_fraction":
        records = _raw_segment_records(
            case, "events_and_maintenance_work_orders", segment_id
        )
        completed = sum(
            float(row.get("completed_work_order_count", 0.0)) for row in records
        )
        total = sum(float(row.get("work_order_count", 0.0)) for row in records)
        if total > 0.0:
            return min(1.0, max(0.0, completed / total))
        return 1.0 if records else None
    definition = mapping.get(indicator_id)
    if definition is None:
        return None
    category_id, fields, aggregation = definition
    values: list[float] = []
    for row in _raw_segment_records(case, category_id, segment_id):
        for field in fields:
            value = _finite_number(row.get(field))
            if value is not None:
                values.append(value)
                break
    if not values:
        return None
    result = min(values) if aggregation == "min" else max(values)
    return result / 100.0 if aggregation == "percent_max" else result


def _indicator_value(
    case: dict[str, Any], segment_id: str, indicator_id: str
) -> tuple[float | None, str]:
    try:
        value = resolve_indicator_value(
            case,
            indicator_id,
            segment_id=segment_id,
            catalog=load_indicator_catalog(),
        )
    except (KeyError, ValueError):
        value = None
    standardized = _finite_number(value)
    if standardized is not None:
        return standardized, "engineering_indicators"
    raw = _raw_indicator_value(case, segment_id, indicator_id)
    return (raw, "raw_data_categories") if raw is not None else (None, "missing")


def _evidence_factor(
    case: dict[str, Any], segment_id: str, spec: dict[str, Any]
) -> dict[str, Any]:
    traces: list[dict[str, Any]] = []
    contributions: list[float] = []
    for term in spec["evidence_terms"]:
        value, value_source = _indicator_value(
            case, segment_id, str(term["indicator_id"])
        )
        if value is None:
            traces.append(
                {
                    "indicator_id": term["indicator_id"],
                    "name_zh": term["name_zh"],
                    "status": "MISSING_NEUTRAL_MARGINALIZED",
                    "log_factor_contribution": 0.0,
                }
            )
            continue
        reference = float(term["reference"])
        scale = float(term["scale"])
        if scale == 0.0:
            raise ValueError(f"{term['indicator_id']}的scale不能为0")
        normalized = (
            (reference - value) / scale
            if term.get("transform") == "inverse"
            else (value - reference) / scale
        )
        normalized = min(
            float(term["normalized_max"]),
            max(float(term["normalized_min"]), normalized),
        )
        contribution = float(term["coefficient"]) * normalized
        contributions.append(contribution)
        traces.append(
            {
                "indicator_id": term["indicator_id"],
                "name_zh": term["name_zh"],
                "status": "OBSERVED",
                "value_source": value_source,
                "value": value,
                "reference": reference,
                "scale": scale,
                "normalized_value": normalized,
                "coefficient": float(term["coefficient"]),
                "log_factor_contribution": contribution,
            }
        )
    total = len(spec["evidence_terms"])
    observed = len(contributions)
    coverage = observed / total if total else 0.0
    mean_log_factor = sum(contributions) / observed if contributions else 0.0
    defaults = spec["defaults"]
    raw_factor = math.exp(mean_log_factor)
    factor = min(
        float(defaults["maximum_evidence_factor"]),
        max(float(defaults["minimum_evidence_factor"]), raw_factor),
    )
    minimum_uncertainty = float(defaults["minimum_uncertainty_factor"])
    maximum_uncertainty = float(defaults["maximum_uncertainty_factor"])
    uncertainty_factor = minimum_uncertainty * (
        maximum_uncertainty / minimum_uncertainty
    ) ** (1.0 - coverage)
    return {
        "observed_term_count": observed,
        "registered_term_count": total,
        "coverage_fraction": coverage,
        "mean_log_factor": mean_log_factor,
        "raw_evidence_factor": raw_factor,
        "evidence_factor": factor,
        "uncertainty_factor": uncertainty_factor,
        "terms": traces,
    }


def _segment_point_distance_m(segment: dict[str, Any], xy: list[float]) -> float:
    x, y = float(xy[0]), float(xy[1])
    x1, y1 = map(float, segment.get("start_xy_m", [float(segment["start_km"]) * 1000.0, 0.0]))
    x2, y2 = map(float, segment.get("end_xy_m", [float(segment["end_km"]) * 1000.0, 0.0]))
    dx, dy = x2 - x1, y2 - y1
    denominator = dx * dx + dy * dy
    if denominator <= 0.0:
        return math.hypot(x - x1, y - y1)
    t = min(1.0, max(0.0, ((x - x1) * dx + (y - y1) * dy) / denominator))
    return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))


def _population_cells(case: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    cells = case.get("population_cells")
    if isinstance(cells, list) and cells:
        return cells, "population_cells"
    targets = (
        case.get("raw_data_categories", {})
        .get("high_consequence_targets", {})
        .get("records", [])
    )
    if targets:
        rows = []
        for target in targets:
            if "xy_m" not in target:
                continue
            rows.append(
                {
                    "cell_id": target.get("target_id", target.get("record_id")),
                    "segment_id": target.get("segment_id"),
                    "xy_m": target["xy_m"],
                    "population_day": target.get("population_day", 0.0),
                    "population_night": target.get("population_night", 0.0),
                    "outdoor_fraction_day": target.get("outdoor_fraction_day", 0.2),
                    "outdoor_fraction_night": target.get("outdoor_fraction_night", 0.05),
                    "receptor_type": target.get("target_type", "high_consequence_target"),
                }
            )
        if rows:
            return rows, "raw_data_categories.high_consequence_targets"
    return [], "model_population_density_prior"


def _scenario_release(
    case: dict[str, Any], segment: dict[str, Any], spec: dict[str, Any]
) -> list[dict[str, Any]]:
    defaults = spec["defaults"]
    pipeline = case.get("pipeline", {})
    composition = pipeline.get("gas_composition_mole_fraction")
    mixture = (
        calculate_gas_mixture_properties(composition)
        if composition is not None
        else None
    )
    molar_mass = (
        mixture.molar_mass_kg_mol
        if mixture is not None
        else float(defaults["molar_mass_kg_mol"])
    )
    gamma = (
        mixture.heat_capacity_ratio
        if mixture is not None
        else float(defaults["gamma"])
    )
    heat_of_combustion = (
        mixture.lower_heating_value_j_kg
        if mixture is not None
        else float(defaults["heat_of_combustion_j_kg"])
    )
    ambient = float(defaults["ambient_pressure_pa_abs"])
    pressure = float(pipeline.get("operating_pressure_mpa", defaults["operating_pressure_mpa"]))
    temperature = float(pipeline.get("operating_temperature_k", defaults["operating_temperature_k"]))
    diameter = float(segment.get("outside_diameter_mm", defaults["outside_diameter_mm"]))
    upstream = pressure * 1.0e6 + ambient
    rows = []
    for scenario in gas_leak_scenarios(diameter):
        release = gas_orifice_mass_flow_rate(
            upstream_pressure_pa_abs=upstream,
            downstream_pressure_pa_abs=ambient,
            temperature_k=temperature,
            molar_mass_kg_mol=molar_mass,
            gamma=gamma,
            discharge_coefficient=float(defaults["gas_discharge_coefficient"]),
            orifice_diameter_m=float(scenario.hole_diameter_mm) / 1000.0,
        )
        fatal_distance = horizontal_jet_fire_threshold_distance_m(
            heat_of_combustion_j_kg=heat_of_combustion,
            mass_flow_rate_kg_s=release.mass_flow_rate_kg_s,
            radiative_fraction=float(defaults["radiative_fraction"]),
            threshold_heat_flux_kw_m2=37.5,
        )
        rows.append(
            {
                **scenario.to_dict(),
                "mass_flow_rate_kg_s": release.mass_flow_rate_kg_s,
                "flow_regime": release.flow_regime,
                "heat_of_combustion_j_kg": heat_of_combustion,
                "fatal_heat_flux_distance_m": fatal_distance,
                "fatal_consequence_area_m2": consequence_area_m2(fatal_distance),
            }
        )
    return rows


def calculate_adaptive_evidence_qra(case: dict[str, Any]) -> dict[str, Any]:
    spec = _load_spec()
    defaults = spec["defaults"]
    cells, population_source = _population_cells(case)
    spatial_population_available = population_source != "model_population_density_prior"
    cells_by_segment: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    unassigned_cells: list[dict[str, Any]] = []
    segment_ids = {str(row["segment_id"]) for row in case.get("segments", [])}
    for cell in cells:
        segment_id = str(cell.get("segment_id", ""))
        if segment_id in segment_ids:
            cells_by_segment[segment_id].append(cell)
        else:
            unassigned_cells.append(cell)

    ranking: list[dict[str, Any]] = []
    scenario_events: list[dict[str, float]] = []
    default_usage: defaultdict[str, int] = defaultdict(int)
    pipeline = case.get("pipeline", {})
    if "operating_pressure_mpa" not in pipeline:
        default_usage["operating_pressure_mpa"] += 1
    if "operating_temperature_k" not in pipeline:
        default_usage["operating_temperature_k"] += 1

    for segment in case["segments"]:
        segment_id = str(segment["segment_id"])
        length = float(segment["length_km"])
        wall_thickness = float(segment.get("wall_thickness_mm", defaults["wall_thickness_mm"]))
        if "wall_thickness_mm" not in segment:
            default_usage["wall_thickness_mm"] += 1
        if "outside_diameter_mm" not in segment:
            default_usage["outside_diameter_mm"] += 1
        base_frequency_density = gas_pipeline_average_failure_frequency_per_km_year(
            wall_thickness
        )
        evidence = _evidence_factor(case, segment_id, spec)
        frequency_density = base_frequency_density * evidence["evidence_factor"]
        annual_frequency = frequency_density * length
        ignition_probability = float(defaults["point_ignition_probability"])
        scenario_rows = _scenario_release(case, segment, spec)
        applicable_cells = cells_by_segment.get(segment_id, [])
        if not applicable_cells and unassigned_cells:
            applicable_cells = unassigned_cells

        segment_pll = 0.0
        receptor_ir: defaultdict[str, float] = defaultdict(float)
        maximum_consequence: dict[str, Any] | None = None
        dominant_scenario: dict[str, Any] | None = None
        for scenario in scenario_rows:
            scenario_frequency = annual_frequency * float(scenario["probability_fraction"])
            conditional_fatalities = 0.0
            maximum_cell_probability = 0.0
            cell_rows = []
            if applicable_cells:
                for cell in applicable_cells:
                    distance = max(
                        float(defaults["minimum_distance_m"]),
                        _segment_point_distance_m(segment, cell["xy_m"]),
                    )
                    heat_flux = horizontal_jet_fire_heat_flux_kw_m2(
                        heat_of_combustion_j_kg=float(
                            scenario["heat_of_combustion_j_kg"]
                        ),
                        mass_flow_rate_kg_s=float(scenario["mass_flow_rate_kg_s"]),
                        radiative_fraction=float(defaults["radiative_fraction"]),
                        distance_from_point_source_m=distance,
                    )
                    exposure = float(defaults["thermal_exposure_time_s"])
                    outdoor_probability = corrected_thermal_fatality_probability(
                        heat_flux, exposure, "societal_outdoor"
                    )
                    indoor_probability = corrected_thermal_fatality_probability(
                        heat_flux, exposure, "societal_indoor"
                    )
                    day_population = float(cell.get("population_day", 0.0))
                    night_population = float(cell.get("population_night", day_population))
                    day_outdoor = float(cell.get("outdoor_fraction_day", 0.2))
                    night_outdoor = float(cell.get("outdoor_fraction_night", 0.05))
                    day_fatalities = day_population * (
                        day_outdoor * outdoor_probability
                        + (1.0 - day_outdoor) * indoor_probability
                    )
                    night_fatalities = night_population * (
                        night_outdoor * outdoor_probability
                        + (1.0 - night_outdoor) * indoor_probability
                    )
                    fatalities = (
                        float(defaults["day_time_weight"]) * day_fatalities
                        + (1.0 - float(defaults["day_time_weight"])) * night_fatalities
                    )
                    conditional_fatalities += fatalities
                    individual_probability = max(outdoor_probability, indoor_probability)
                    maximum_cell_probability = max(maximum_cell_probability, individual_probability)
                    receptor_id = str(cell.get("cell_id", cell.get("target_id", "UNKNOWN")))
                    receptor_ir[receptor_id] += (
                        scenario_frequency * ignition_probability * individual_probability
                    )
                    cell_rows.append(
                        {
                            "receptor_id": receptor_id,
                            "distance_m": distance,
                            "heat_flux_kw_m2": heat_flux,
                            "outdoor_fatality_probability": outdoor_probability,
                            "indoor_fatality_probability": indoor_probability,
                            "conditional_expected_fatalities": fatalities,
                        }
                    )
            else:
                conditional_fatalities = float(scenario["fatal_consequence_area_m2"]) * float(
                    defaults["default_population_density_per_m2"]
                )
                default_usage["default_population_density_per_m2"] += 1

            conditional_with_ignition = ignition_probability * conditional_fatalities
            pll = scenario_frequency * conditional_with_ignition
            segment_pll += pll
            event = {
                "annual_frequency": scenario_frequency * ignition_probability,
                "expected_fatalities": conditional_fatalities,
            }
            scenario_events.append(event)
            scenario_summary = {
                **scenario,
                "scenario_failure_frequency_per_year": scenario_frequency,
                "point_ignition_probability": ignition_probability,
                "conditional_expected_fatalities_given_jet_fire": conditional_fatalities,
                "conditional_expected_fatalities_per_initiating_failure": conditional_with_ignition,
                "maximum_conditional_fatality_probability": maximum_cell_probability,
                "pll_contribution_per_year": pll,
                "receptors": cell_rows,
            }
            if maximum_consequence is None or conditional_with_ignition > float(
                maximum_consequence["expected_fatalities"]
            ):
                maximum_consequence = {
                    "branch_id": "jet_fire",
                    "loc_id": scenario["scenario_id"],
                    "expected_fatalities": conditional_with_ignition,
                    "annual_frequency": scenario_frequency,
                }
            if dominant_scenario is None or pll > float(dominant_scenario["pll_contribution_per_year"]):
                dominant_scenario = scenario_summary

        maximum_receptor = max(receptor_ir, key=receptor_ir.get) if receptor_ir else None
        maximum_ir = receptor_ir[maximum_receptor] if maximum_receptor else None
        uncertainty_factor = float(evidence["uncertainty_factor"])
        if population_source == "model_population_density_prior":
            uncertainty_factor = min(
                float(defaults["maximum_uncertainty_factor"]), uncertainty_factor * 1.5
            )
        ranking.append(
            {
                "segment_id": segment_id,
                "start_km": float(segment["start_km"]),
                "end_km": float(segment["end_km"]),
                "length_km": length,
                "base_failure_frequency_per_km_year": base_frequency_density,
                "evidence_factor": evidence["evidence_factor"],
                "failure_frequency_per_km_year": frequency_density,
                "initiating_failure_frequency_per_year": annual_frequency,
                "risk_value_fatalities_per_year": segment_pll,
                "risk_value_lower_screening_bound": segment_pll / uncertainty_factor,
                "risk_value_upper_screening_bound": segment_pll * uncertainty_factor,
                "risk_density_fatalities_per_km_year": segment_pll / length if length > 0 else 0.0,
                "maximum_segment_individual_risk_per_year": maximum_ir,
                "maximum_segment_individual_risk_receptor_id": maximum_receptor,
                "individual_risk_status": (
                    "CALCULATED_FROM_SPATIAL_RECEPTORS"
                    if maximum_ir is not None
                    else "NOT_CALCULATED_MISSING_SPATIAL_RECEPTORS"
                ),
                "maximum_conditional_consequence": maximum_consequence or {"expected_fatalities": 0.0},
                "dominant_risk_scenario": dominant_scenario,
                "evidence_diagnostics": evidence,
                "uncertainty_factor": uncertainty_factor,
                "risk_level": {
                    "level": "SCREENING_ESTIMATE_NOT_ACCEPTANCE_JUDGED",
                    "label_zh": "证据定量筛查估计（未作接受性判定）",
                    "criterion_id": "NONE_PARTIAL_EVIDENCE",
                    "action": "用于排序和补数；正式接受性结论需完整QRA或经批准的筛查准则",
                },
                "leak_scenarios": scenario_rows,
                "segment_fn_curve": [],
            }
        )

    ranking.sort(key=lambda row: (-float(row["risk_value_fatalities_per_year"]), row["segment_id"]))
    total_risk = sum(float(row["risk_value_fatalities_per_year"]) for row in ranking)
    for index, row in enumerate(ranking, start=1):
        row["risk_value_rank"] = index
        row["fraction_of_pipeline_risk_value"] = (
            float(row["risk_value_fatalities_per_year"]) / total_risk if total_risk > 0 else 0.0
        )
    by_density = sorted(ranking, key=lambda row: (-float(row["risk_density_fatalities_per_km_year"]), row["segment_id"]))
    by_ir = sorted(
        ranking,
        key=lambda row: (
            row["maximum_segment_individual_risk_per_year"] is None,
            -float(row["maximum_segment_individual_risk_per_year"] or 0.0),
            row["segment_id"],
        ),
    )
    by_consequence = sorted(ranking, key=lambda row: (-float(row["maximum_conditional_consequence"]["expected_fatalities"]), row["segment_id"]))
    for index, row in enumerate(by_density, start=1):
        row["risk_density_rank"] = index
    for index, row in enumerate(by_ir, start=1):
        row["individual_risk_rank"] = index
    for index, row in enumerate(by_consequence, start=1):
        row["maximum_consequence_rank"] = index

    fn_curve = []
    if spatial_population_available:
        for threshold in (1.0, 5.0, 10.0, 30.0, 50.0, 100.0):
            fn_curve.append(
                {
                    "fatalities_at_least": threshold,
                    "cumulative_frequency_per_year": sum(
                        event["annual_frequency"]
                        for event in scenario_events
                        if event["expected_fatalities"] >= threshold
                    ),
                }
            )
    ir_rows = [
        row
        for row in ranking
        if row["maximum_segment_individual_risk_per_year"] is not None
    ]
    maximum_ir_row = max(
        ir_rows,
        key=lambda row: float(row["maximum_segment_individual_risk_per_year"]),
        default=None,
    )
    maximum_ir = (
        float(maximum_ir_row["maximum_segment_individual_risk_per_year"])
        if maximum_ir_row
        else None
    )
    return {
        "model_id": spec["model_id"],
        "model_version": spec["version"],
        "model_status": spec["status"],
        "method_type": spec["method_type"],
        "result_tier": "EVIDENCE_CONDITIONED_SCREENING_ESTIMATE",
        "formal_report_allowed": False,
        "formal_report_blockers": list(spec["formal_release_limitations"]),
        "formula_trace": list(spec["standard_formula_backbone"]),
        "evidence_update_note": spec["evidence_update_note"],
        "population_source": population_source,
        "default_parameter_usage": dict(sorted(default_usage.items())),
        "human_risk": {
            "judgement_status": "CALCULATED_NOT_ACCEPTANCE_JUDGED_PARTIAL_EVIDENCE",
            "individual_risk": {
                "available": maximum_ir is not None,
                "status": (
                    "CALCULATED_NOT_ACCEPTANCE_JUDGED"
                    if maximum_ir is not None
                    else "NOT_CALCULATED_MISSING_SPATIAL_RECEPTORS"
                ),
                "maximum": {
                    "value_per_year": maximum_ir,
                    "segment_id": maximum_ir_row["segment_id"] if maximum_ir_row else None,
                }
            },
            "societal_risk": {
                "pipeline_pll_per_year": total_risk,
                "pll_status": "SCREENING_ESTIMATE_WITH_MODEL_POPULATION_PRIOR"
                if not spatial_population_available
                else "CALCULATED_FROM_SPATIAL_RECEPTORS",
                "fn_curve_available": spatial_population_available,
                "fn_curve_status": (
                    "CALCULATED_NOT_ACCEPTANCE_JUDGED"
                    if spatial_population_available
                    else "NOT_CALCULATED_MISSING_POPULATION_DISTRIBUTION"
                ),
                "fn_curve": fn_curve,
            },
            "segment_risk": {"ranking": ranking},
        },
    }


__all__ = ["MODEL_SPEC_PATH", "calculate_adaptive_evidence_qra"]
