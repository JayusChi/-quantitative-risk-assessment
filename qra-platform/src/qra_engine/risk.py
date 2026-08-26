from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from .risk_criteria import GBT34346_2017_ANNEX_C_IR, classify_individual_risk


DEFAULT_FN_THRESHOLDS = (1.0, 5.0, 10.0, 30.0, 50.0, 100.0)


def build_fn_curve(
    scenario_frequency_and_fatalities: Iterable[tuple[float, float]],
    thresholds: Iterable[float] = DEFAULT_FN_THRESHOLDS,
) -> list[dict[str, float]]:
    rows = list(scenario_frequency_and_fatalities)
    return [
        {
            "fatalities_at_least": float(threshold),
            "cumulative_frequency_per_year": sum(
                frequency for frequency, fatalities in rows if fatalities >= threshold
            ),
        }
        for threshold in thresholds
    ]


def ranked_segments(values: dict[str, float]) -> list[dict[str, float | int | str]]:
    total = sum(values.values())
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    return [
        {
            "rank": index,
            "segment_id": segment_id,
            "pll_per_year": value,
            "fraction_of_pipeline_pll": value / total if total > 0 else 0.0,
        }
        for index, (segment_id, value) in enumerate(ordered, start=1)
    ]


def _rank_map(values: dict[str, float]) -> dict[str, int]:
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    return {segment_id: rank for rank, (segment_id, _) in enumerate(ordered, start=1)}


def build_segment_risk_table(
    *,
    segments: list[dict[str, Any]],
    pll_by_segment: dict[str, float],
    ir_by_segment_and_receptor: dict[str, dict[str, float]],
    maximum_consequence_by_segment: dict[str, dict[str, Any]],
    dominant_risk_scenario_by_segment: dict[str, dict[str, Any]],
    scenario_frequency_and_fatalities_by_segment: dict[str, list[tuple[float, float]]],
    initiating_frequency_by_segment: dict[str, float] | None = None,
    thresholds: Iterable[float] = DEFAULT_FN_THRESHOLDS,
) -> dict[str, Any]:
    """按AQ/T 3046式(15)输出管段PLL，并按GB/T 34346附录C进行IR分级。"""
    segment_by_id = {str(segment["segment_id"]): segment for segment in segments}
    segment_ids = list(segment_by_id)
    pll_values = {segment_id: float(pll_by_segment.get(segment_id, 0.0)) for segment_id in segment_ids}
    density_values = {
        segment_id: pll_values[segment_id] / float(segment_by_id[segment_id]["length_km"])
        for segment_id in segment_ids
    }
    consequence_values = {
        segment_id: float(
            maximum_consequence_by_segment.get(segment_id, {}).get("expected_fatalities", 0.0)
        )
        for segment_id in segment_ids
    }
    max_ir_values: dict[str, float] = {}
    max_ir_receptors: dict[str, str | None] = {}
    for segment_id in segment_ids:
        contributions = ir_by_segment_and_receptor.get(segment_id, {})
        if contributions:
            receptor_id, value = sorted(
                contributions.items(), key=lambda item: (-float(item[1]), item[0])
            )[0]
            max_ir_values[segment_id] = float(value)
            max_ir_receptors[segment_id] = receptor_id
        else:
            max_ir_values[segment_id] = 0.0
            max_ir_receptors[segment_id] = None

    pll_rank = _rank_map(pll_values)
    density_rank = _rank_map(density_values)
    consequence_rank = _rank_map(consequence_values)
    ir_rank = _rank_map(max_ir_values)
    total_pll = sum(pll_values.values())
    rows: list[dict[str, Any]] = []
    for segment_id in segment_ids:
        segment = segment_by_id[segment_id]
        classification = classify_individual_risk(max_ir_values[segment_id])
        rows.append(
            {
                "segment_id": segment_id,
                "start_km": float(segment["start_km"]),
                "end_km": float(segment["end_km"]),
                "length_km": float(segment["length_km"]),
                "risk_value_fatalities_per_year": pll_values[segment_id],
                "risk_value_rank": pll_rank[segment_id],
                "fraction_of_pipeline_risk_value": (
                    pll_values[segment_id] / total_pll if total_pll > 0.0 else 0.0
                ),
                "risk_density_fatalities_per_km_year": density_values[segment_id],
                "risk_density_rank": density_rank[segment_id],
                "maximum_segment_individual_risk_per_year": max_ir_values[segment_id],
                "maximum_segment_individual_risk_receptor_id": max_ir_receptors[segment_id],
                "individual_risk_rank": ir_rank[segment_id],
                "risk_level": classification,
                "maximum_conditional_consequence": maximum_consequence_by_segment.get(
                    segment_id,
                    {"expected_fatalities": 0.0},
                ),
                "maximum_consequence_rank": consequence_rank[segment_id],
                "dominant_risk_scenario": dominant_risk_scenario_by_segment.get(segment_id),
                "initiating_failure_frequency_per_year": (
                    float(initiating_frequency_by_segment.get(segment_id, 0.0))
                    if initiating_frequency_by_segment is not None
                    else None
                ),
                "segment_fn_curve": build_fn_curve(
                    scenario_frequency_and_fatalities_by_segment.get(segment_id, []),
                    thresholds,
                ),
            }
        )
    rows.sort(key=lambda row: (row["risk_value_rank"], row["segment_id"]))
    return {
        "risk_value_definition": {
            "name": "segment_potential_loss_of_life",
            "formula": "R_segment = PLL_segment = sum_s(f_s * N_s)",
            "source": "AQ/T 3046—2013 formula (15)",
            "unit": "expected_fatalities_per_year",
            "normalization": "risk_density = R_segment / segment_length_km",
        },
        "individual_risk_criterion": GBT34346_2017_ANNEX_C_IR.to_dict(),
        "fn_acceptability": {
            "status": "CALCULATED_NOT_JUDGED",
            "reason": "尚未批准适用于本项目的数值F-N可接受曲线，禁止用示意图代替正式准则。",
        },
        "ranking": rows,
        "high_risk_segment_ids": [
            row["segment_id"]
            for row in rows
            if row["risk_level"]["level"] == "HIGH_UNACCEPTABLE"
        ],
        "alarp_segment_ids": [
            row["segment_id"]
            for row in rows
            if row["risk_level"]["level"] == "MEDIUM_ALARP"
        ],
        "acceptable_segment_ids": [
            row["segment_id"]
            for row in rows
            if row["risk_level"]["level"] == "LOW_ACCEPTABLE"
        ],
    }


def aggregate_precomputed_human(
    case: dict[str, Any],
    thresholds: Iterable[float] = DEFAULT_FN_THRESHOLDS,
) -> dict[str, Any]:
    scenarios = case["mock_adapter_output"]["scenario_outcomes"]
    pll_by_segment: defaultdict[str, float] = defaultdict(float)
    frequency_and_fatalities: list[tuple[float, float]] = []
    for row in scenarios:
        frequency = float(row["annual_frequency"])
        fatalities = float(row["expected_fatalities"])
        pll_by_segment[row["segment_id"]] += frequency * fatalities
        frequency_and_fatalities.append((frequency, fatalities))

    ir_by_receptor: defaultdict[str, float] = defaultdict(float)
    for contributions in case["mock_adapter_output"]["ir_contribution_by_segment_and_receptor"].values():
        for receptor_id, value in contributions.items():
            ir_by_receptor[receptor_id] += float(value)

    max_receptor = max(ir_by_receptor, key=ir_by_receptor.get) if ir_by_receptor else None
    return {
        "calculation_profile": "golden-aggregate",
        "consequence_model": {
            "model_id": case["mock_adapter_output"]["adapter_id"],
            "status": case["mock_adapter_output"]["status"],
        },
        "individual_risk": {
            "unit": "per_year",
            "by_receptor": dict(sorted(ir_by_receptor.items())),
            "maximum": {
                "receptor_id": max_receptor,
                "value_per_year": ir_by_receptor[max_receptor] if max_receptor else 0.0,
            },
        },
        "societal_risk": {
            "pll_unit": "fatalities_per_year",
            "pipeline_pll_per_year": sum(pll_by_segment.values()),
            "segment_pll_per_year": dict(sorted(pll_by_segment.items())),
            "fn_curve": build_fn_curve(frequency_and_fatalities, thresholds),
            "segment_ranking": ranked_segments(dict(pll_by_segment)),
        },
        "scenario_count": len(scenarios),
        "judgement_status": "CALCULATED_NOT_JUDGED",
    }
