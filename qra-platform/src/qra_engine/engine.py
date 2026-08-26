from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .audit import sha256_json
from .consequence import HumanConsequenceAdapter, ScenarioContext, SyntheticHumanConsequenceAdapter
from .event_tree import calculate_event_tree
from .frequency import calculate_loc_frequencies, discretize_segment
from .frequency_correction import resolve_segment_correction_factors
from .gbt34346 import calculate_annex_c_secondary_assessment
from .indicators import build_indicator_coverage, load_indicator_catalog
from .model_registry import MODEL_SPEC_ROOT, find_model_registration
from .physical_consequence import AQT3046PipelineConsequenceAdapter
from .risk import (
    DEFAULT_FN_THRESHOLDS,
    aggregate_precomputed_human,
    build_fn_curve,
    build_segment_risk_table,
    ranked_segments,
)
from .risk_criteria import GBT34346_2017_ANNEX_C_IR, classify_individual_risk
from .validation import ValidationReport, validate_case


ENGINE_VERSION = "0.6.0"
class QRAEngine:
    def __init__(
        self,
        consequence_spec_path: Path | None = None,
        consequence_adapter: HumanConsequenceAdapter | None = None,
    ):
        if consequence_adapter is not None and consequence_spec_path is not None:
            raise ValueError("consequence_adapter and consequence_spec_path are mutually exclusive")
        if consequence_spec_path is not None:
            consequence_adapter = SyntheticHumanConsequenceAdapter(consequence_spec_path)
        self.consequence_adapter = consequence_adapter

    def validate(self, case: dict[str, Any]) -> ValidationReport:
        return validate_case(case)

    def run(
        self,
        case: dict[str, Any],
        *,
        profile: str = "aqt3046-physical",
        fn_thresholds: Iterable[float] = DEFAULT_FN_THRESHOLDS,
    ) -> dict[str, Any]:
        report = self.validate(case)
        report.raise_for_errors()
        input_hash = sha256_json(case)
        run_id = f"RUN-{input_hash[:16]}-{profile.upper().replace('-', '_')}"

        if profile == "golden-aggregate":
            human = aggregate_precomputed_human(case, fn_thresholds)
            result = self._base_result(case, run_id, input_hash, report, profile, None)
            result["human_risk"] = human
            return self._seal_result(result)
        if profile == "gbt34346-annex-c":
            assessment = calculate_annex_c_secondary_assessment(case)
            result = self._base_result(case, run_id, input_hash, report, profile, None)
            result["model_trace"]["secondary_assessment_model_id"] = assessment[
                "model_id"
            ]
            result["model_trace"]["human_consequence_model_used"] = False
            result["pipeline_secondary_assessment"] = assessment
            return self._seal_result(result)
        if profile not in ("synthetic-chain", "aqt3046-physical"):
            raise ValueError(f"unsupported run profile: {profile}")

        if self.consequence_adapter is not None:
            consequence_adapter = self.consequence_adapter
        elif profile == "synthetic-chain":
            consequence_adapter = SyntheticHumanConsequenceAdapter(
                MODEL_SPEC_ROOT / "human_synthetic_v1.json"
            )
        else:
            consequence_adapter = AQT3046PipelineConsequenceAdapter(case)

        human, diagnostics = self._run_spatial_chain(
            case,
            fn_thresholds,
            consequence_adapter,
            profile,
        )
        result = self._base_result(
            case,
            run_id,
            input_hash,
            report,
            profile,
            consequence_adapter,
        )
        result["calculation_diagnostics"] = diagnostics
        result["human_risk"] = human
        return self._seal_result(result)

    def _base_result(
        self,
        case: dict[str, Any],
        run_id: str,
        input_hash: str,
        report: ValidationReport,
        profile: str,
        consequence_adapter: HumanConsequenceAdapter | None,
    ) -> dict[str, Any]:
        registration = (
            find_model_registration(consequence_adapter.model_id)
            if consequence_adapter is not None
            else None
        )
        indicator_catalog = load_indicator_catalog()
        indicator_coverage = build_indicator_coverage(case, indicator_catalog)
        correction_resolution = resolve_segment_correction_factors(case)
        test_profile = profile in (
            "synthetic-chain",
            "golden-aggregate",
            "gbt34346-annex-c",
        )
        consequence_model_released = consequence_adapter is None or (
            registration is not None and registration.released
        )
        formal_report_allowed = (
            report.formal_report_allowed
            and consequence_model_released
            and not test_profile
        )
        formal_report_blockers: list[str] = []
        if not report.formal_report_allowed:
            formal_report_blockers.append("输入数据或参数未满足正式报告条件")
        if consequence_adapter is not None:
            if registration is None:
                formal_report_blockers.append("人员后果模型未登记")
            elif not registration.released:
                formal_report_blockers.append(
                    f"人员后果模型状态为{registration.status}"
                )
                formal_report_blockers.extend(registration.release_blockers)
        if test_profile:
            formal_report_blockers.append(f"计算配置{profile}仅用于开发或回归测试")
        formal_report_blockers.extend(
            correction_resolution.diagnostics["formal_release_blockers"]
        )
        if indicator_coverage["required_coverage_fraction"] < 1.0:
            formal_report_blockers.append(
                "标准工程指标必需观测不完整："
                f"{indicator_coverage['required_coverage_fraction']:.1%}"
            )

        return {
            "run": {
                "run_id": run_id,
                "assessment_id": case["assessment"]["assessment_id"],
                "case_id": case["metadata"]["case_id"],
                "engine_version": ENGINE_VERSION,
                "calculation_profile": profile,
                "data_classification": case["metadata"].get("data_classification"),
                "formal_report_allowed": formal_report_allowed,
                "formal_report_blockers": list(dict.fromkeys(formal_report_blockers)),
            },
            "validation": report.to_dict(),
            "model_trace": {
                "qra_model_id": case["metadata"]["model_id"],
                "frequency_library_id": case["frequency_library"]["library_id"],
                "frequency_library_version": case["frequency_library"]["version"],
                "engineering_indicator_catalog_id": indicator_catalog.catalog_id,
                "engineering_indicator_catalog_version": indicator_catalog.version,
                "frequency_correction_model_id": correction_resolution.diagnostics[
                    "model_id"
                ],
                "frequency_correction_model_version": correction_resolution.diagnostics[
                    "version"
                ],
                "frequency_correction_model_status": correction_resolution.diagnostics[
                    "status"
                ],
                "human_consequence_model_id": (
                    consequence_adapter.model_id if consequence_adapter is not None else None
                ),
                "human_consequence_model_version": (
                    consequence_adapter.model_version
                    if consequence_adapter is not None
                    else None
                ),
                "human_consequence_model_status": (
                    consequence_adapter.status if consequence_adapter is not None else "NOT_USED"
                ),
                "human_risk_criterion_id": GBT34346_2017_ANNEX_C_IR.criterion_id,
                "external_software_dependency": None,
            },
            "audit": {
                "input_sha256": input_hash,
                "result_sha256": None,
                "result_hash_scope": "canonical JSON with audit.result_sha256 set to null",
                "deterministic": True,
            },
        }

    @staticmethod
    def _seal_result(result: dict[str, Any]) -> dict[str, Any]:
        result["audit"]["result_sha256"] = sha256_json(result)
        return result

    def _run_spatial_chain(
        self,
        case: dict[str, Any],
        fn_thresholds: Iterable[float],
        consequence_adapter: HumanConsequenceAdapter,
        calculation_profile: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        correction_resolution = resolve_segment_correction_factors(case)
        loc_frequencies = calculate_loc_frequencies(case, correction_resolution)
        segment_by_id = {segment["segment_id"]: segment for segment in case["segments"]}
        spacing_m = float(case["assessment"]["leak_point_initial_spacing_m"])
        leak_points_by_segment = {
            segment_id: discretize_segment(segment, spacing_m)
            for segment_id, segment in segment_by_id.items()
        }

        ir_by_cell: defaultdict[str, float] = defaultdict(float)
        ir_by_segment_by_cell: defaultdict[str, defaultdict[str, float]] = defaultdict(
            lambda: defaultdict(float)
        )
        pll_by_segment: defaultdict[str, float] = defaultdict(float)
        branch_frequency: defaultdict[str, float] = defaultdict(float)
        frequency_and_fatalities: list[tuple[float, float]] = []
        frequency_and_fatalities_by_segment: defaultdict[
            str, list[tuple[float, float]]
        ] = defaultdict(list)
        maximum_consequence_by_segment: dict[str, dict[str, Any]] = {}
        dominant_risk_scenario_by_segment: dict[str, dict[str, Any]] = {}
        scenario_contributions: list[dict[str, Any]] = []
        scenario_count = 0
        ignition_model = getattr(
            consequence_adapter,
            "ignition_model",
            case["ignition_model"],
        )

        for loc_frequency in loc_frequencies:
            segment = segment_by_id[loc_frequency.segment_id]
            for leak_point in leak_points_by_segment[loc_frequency.segment_id]:
                release_rate = consequence_adapter.release_rate_kg_s(
                    loc_frequency.loc_id,
                    segment_id=loc_frequency.segment_id,
                    leak_chainage_km=leak_point.chainage_km,
                )
                branches = calculate_event_tree(
                    ignition_model,
                    segment["area_activity"],
                    release_rate,
                )
                point_frequency = loc_frequency.annual_frequency * leak_point.frequency_share
                for weather in case["weather_joint_probability"]:
                    weather_probability = float(weather["probability"])
                    for branch in branches:
                        scenario_count += 1
                        annual_frequency = point_frequency * weather_probability * branch.conditional_probability
                        branch_frequency[branch.branch_id] += annual_frequency
                        context = ScenarioContext(
                            segment_id=loc_frequency.segment_id,
                            loc_id=loc_frequency.loc_id,
                            branch_id=branch.branch_id,
                            leak_x_m=leak_point.x_m,
                            leak_y_m=leak_point.y_m,
                            leak_chainage_km=leak_point.chainage_km,
                            release_rate_kg_s=release_rate,
                            stability_class=weather["stability_class"],
                            wind_speed_m_s=float(weather["wind_speed_m_s"]),
                            wind_direction_from=weather["wind_direction_from"],
                        )

                        expected_fatalities = 0.0
                        maximum_probability = 0.0
                        maximum_effect: dict[str, Any] | None = None
                        for cell in case["population_cells"]:
                            effect = consequence_adapter.evaluate(
                                context,
                                cell["xy_m"],
                                exposure_context="individual_outdoor",
                            )
                            probability = effect.fatality_probability
                            if probability >= maximum_probability:
                                maximum_effect = {
                                    "cell_id": cell["cell_id"],
                                    **effect.to_dict(),
                                }
                            maximum_probability = max(maximum_probability, probability)
                            ir_contribution = annual_frequency * probability
                            ir_by_cell[cell["cell_id"]] += ir_contribution
                            ir_by_segment_by_cell[loc_frequency.segment_id][
                                cell["cell_id"]
                            ] += ir_contribution
                            period = weather["time_period"]
                            population = float(cell[f"population_{period}"])
                            outdoor_fraction = float(
                                cell.get(f"outdoor_fraction_{period}", 1.0)
                            )
                            if not 0.0 <= outdoor_fraction <= 1.0:
                                raise ValueError(
                                    f"{cell['cell_id']}的{period}室外比例必须位于[0,1]"
                                )
                            societal_outdoor = consequence_adapter.evaluate(
                                context,
                                cell["xy_m"],
                                exposure_context="societal_outdoor",
                            ).fatality_probability
                            societal_indoor = consequence_adapter.evaluate(
                                context,
                                cell["xy_m"],
                                exposure_context="societal_indoor",
                            ).fatality_probability
                            societal_probability = (
                                outdoor_fraction * societal_outdoor
                                + (1.0 - outdoor_fraction) * societal_indoor
                            )
                            expected_fatalities += societal_probability * population

                        pll_contribution = annual_frequency * expected_fatalities
                        pll_by_segment[loc_frequency.segment_id] += pll_contribution
                        frequency_and_fatalities.append((annual_frequency, expected_fatalities))
                        frequency_and_fatalities_by_segment[loc_frequency.segment_id].append(
                            (annual_frequency, expected_fatalities)
                        )
                        scenario_summary = {
                            "segment_id": loc_frequency.segment_id,
                            "loc_id": loc_frequency.loc_id,
                            "leak_point_id": leak_point.leak_point_id,
                            "weather_id": weather["weather_id"],
                            "branch_id": branch.branch_id,
                            "release_rate_kg_s": release_rate,
                            "annual_frequency": annual_frequency,
                            "expected_fatalities": expected_fatalities,
                            "pll_contribution_per_year": pll_contribution,
                            "maximum_conditional_fatality_probability": maximum_probability,
                            "maximum_effect": maximum_effect,
                        }
                        summary_metrics = getattr(
                            consequence_adapter, "scenario_summary_metrics", None
                        )
                        if callable(summary_metrics):
                            scenario_summary.update(summary_metrics(context))
                        current_maximum = maximum_consequence_by_segment.get(
                            loc_frequency.segment_id
                        )
                        if (
                            current_maximum is None
                            or expected_fatalities > current_maximum["expected_fatalities"]
                        ):
                            maximum_consequence_by_segment[loc_frequency.segment_id] = dict(
                                scenario_summary
                            )
                        current_dominant = dominant_risk_scenario_by_segment.get(
                            loc_frequency.segment_id
                        )
                        if (
                            current_dominant is None
                            or pll_contribution
                            > current_dominant["pll_contribution_per_year"]
                        ):
                            dominant_risk_scenario_by_segment[loc_frequency.segment_id] = dict(
                                scenario_summary
                            )
                        if pll_contribution > 0.0:
                            scenario_contributions.append(scenario_summary)

        for segment_id in segment_by_id:
            pll_by_segment.setdefault(segment_id, 0.0)
        for cell in case["population_cells"]:
            ir_by_cell.setdefault(cell["cell_id"], 0.0)

        pipeline_pll = math.fsum(
            pll_by_segment[segment_id] for segment_id in sorted(pll_by_segment)
        )
        max_cell = max(ir_by_cell, key=ir_by_cell.get) if ir_by_cell else None
        scenario_contributions.sort(
            key=lambda row: (-row["pll_contribution_per_year"], row["segment_id"], row["leak_point_id"])
        )

        total_initiating_frequency = math.fsum(
            row.annual_frequency for row in loc_frequencies
        )
        initiating_frequency_by_segment: defaultdict[str, float] = defaultdict(float)
        for row in loc_frequencies:
            initiating_frequency_by_segment[row.segment_id] += row.annual_frequency
        total_branch_frequency = math.fsum(
            branch_frequency[branch_id] for branch_id in sorted(branch_frequency)
        )
        segment_risk = build_segment_risk_table(
            segments=case["segments"],
            pll_by_segment=dict(pll_by_segment),
            ir_by_segment_and_receptor={
                segment_id: dict(values)
                for segment_id, values in ir_by_segment_by_cell.items()
            },
            maximum_consequence_by_segment=maximum_consequence_by_segment,
            dominant_risk_scenario_by_segment=dominant_risk_scenario_by_segment,
            scenario_frequency_and_fatalities_by_segment=dict(
                frequency_and_fatalities_by_segment
            ),
            initiating_frequency_by_segment=dict(initiating_frequency_by_segment),
            thresholds=fn_thresholds,
        )
        diagnostics = {
            "frequency_unit": "per_year",
            "total_initiating_frequency_per_year": total_initiating_frequency,
            "total_expanded_branch_frequency_per_year": total_branch_frequency,
            "frequency_balance_error": total_branch_frequency - total_initiating_frequency,
            "loc_frequency": [row.to_dict() for row in loc_frequencies],
            "branch_frequency_per_year": dict(sorted(branch_frequency.items())),
            "leak_point_count": sum(len(points) for points in leak_points_by_segment.values()),
            "expanded_scenario_count": scenario_count,
            "engineering_indicator_coverage": build_indicator_coverage(case),
            "frequency_correction_model": correction_resolution.diagnostics,
        }
        model_diagnostics = getattr(consequence_adapter, "model_diagnostics", None)
        if callable(model_diagnostics):
            diagnostics["physical_consequence_model"] = model_diagnostics()
        human = {
            "calculation_profile": calculation_profile,
            "consequence_model": {
                "model_id": consequence_adapter.model_id,
                "version": consequence_adapter.model_version,
                "status": consequence_adapter.status,
            },
            "individual_risk": {
                "unit": "per_year",
                "by_population_cell": dict(sorted(ir_by_cell.items())),
                "maximum": {
                    "cell_id": max_cell,
                    "value_per_year": ir_by_cell[max_cell] if max_cell else 0.0,
                },
                "acceptability": classify_individual_risk(
                    ir_by_cell[max_cell] if max_cell else 0.0
                ),
                "criterion": GBT34346_2017_ANNEX_C_IR.to_dict(),
                "note": "IR采用100%室外个体死亡概率且不乘人口；社会风险按每个时段的室内外人口比例计算。",
            },
            "societal_risk": {
                "pll_unit": "fatalities_per_year",
                "pipeline_pll_per_year": pipeline_pll,
                "segment_pll_per_year": dict(sorted(pll_by_segment.items())),
                "fn_curve": build_fn_curve(frequency_and_fatalities, fn_thresholds),
                "segment_ranking": ranked_segments(dict(pll_by_segment)),
            },
            "segment_risk": segment_risk,
            "top_scenario_contributions": scenario_contributions[:50],
            "judgement_status": "IR_JUDGED_WITH_GBT34346_REFERENCE_FN_NOT_JUDGED",
        }
        return human, diagnostics
