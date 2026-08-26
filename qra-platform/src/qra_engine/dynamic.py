from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Callable, Iterable

from .adaptive_risk import calculate_adaptive_evidence_qra
from .audit import sha256_json, sha256_numerical_result
from .aqt3046 import (
    adiabatic_pipe_rupture_mass_flow_rate,
    fanning_friction_factor_fully_rough,
    gas_orifice_mass_flow_rate,
    horizontal_jet_fire_threshold_distance_m,
)
from .engine import QRAEngine
from .data_categories import resolve_data_categories
from .frequency import calculate_loc_frequencies, discretize_segment
from .frequency_correction import resolve_segment_correction_factors
from .gbt34346 import calculate_annex_c_secondary_assessment
from .gas_properties import gas_properties_from_case
from .indicators import build_indicator_coverage, load_indicator_catalog
from .model_registry import MODEL_SPEC_ROOT
from .reporting import build_risk_matrix, render_charts, write_risk_matrix_files
from .validation import validate_case


DYNAMIC_SCHEMA_VERSION = "1.1.0"


@dataclass(frozen=True, slots=True)
class Requirement:
    path: str
    label_zh: str
    all_items: bool = False


@dataclass(frozen=True, slots=True)
class CalculationNode:
    node_id: str
    label_zh: str
    standard: str
    description: str
    dependencies: tuple[str, ...]
    requirements: tuple[Requirement, ...]
    output_ids: tuple[str, ...]
    execute: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
    preflight: Callable[[dict[str, Any]], list[dict[str, str]]] | None = None


def _values_at_path(root: Any, path: str) -> list[Any]:
    values = [root]
    for token in path.split("."):
        next_values: list[Any] = []
        for value in values:
            if token == "*":
                if isinstance(value, list):
                    next_values.extend(value)
                elif isinstance(value, dict):
                    next_values.extend(value.values())
            elif isinstance(value, dict) and token in value:
                next_values.append(value[token])
        values = next_values
        if not values:
            break
    return [value for value in values if value is not None]


def _collection_for_wildcard(case: dict[str, Any], path: str) -> list[Any] | dict[str, Any] | None:
    prefix = path.split(".*.", 1)[0]
    values = _values_at_path(case, prefix)
    if len(values) == 1 and isinstance(values[0], (list, dict)):
        return values[0]
    return None


def _requirement_missing(case: dict[str, Any], requirement: Requirement) -> bool:
    values = _values_at_path(case, requirement.path)
    if not values:
        return True
    if not requirement.all_items or ".*." not in requirement.path:
        return False
    collection = _collection_for_wildcard(case, requirement.path)
    expected = len(collection) if collection is not None else 0
    return expected == 0 or len(values) != expected


def _missing_requirements(
    case: dict[str, Any], requirements: Iterable[Requirement]
) -> list[dict[str, str]]:
    return [
        {"path": requirement.path, "label_zh": requirement.label_zh}
        for requirement in requirements
        if _requirement_missing(case, requirement)
    ]


def _write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _indicator_group_detection(case: dict[str, Any]) -> list[dict[str, Any]]:
    coverage = build_indicator_coverage(case)
    rows: list[dict[str, Any]] = []
    for group_id, stats in coverage["group_coverage"].items():
        covered = int(stats["covered_observation_slots"])
        expected = int(stats["expected_observation_slots"])
        rows.append(
            {
                "data_group_id": group_id,
                "name_zh": stats["name_zh"],
                "detected": covered > 0,
                "covered_observation_slots": covered,
                "expected_observation_slots": expected,
                "coverage_fraction": float(stats["coverage_fraction"]),
            }
        )
    return rows


def _data_inventory(case: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    groups = _indicator_group_detection(case)
    explicit = case.get("engineering_indicators", {})
    explicit_count = len(explicit.get("observations_global", {}))
    explicit_count += sum(
        len(rows) for rows in explicit.get("observations_by_segment", {}).values()
    )
    explicit_count += sum(
        len(rows) for rows in explicit.get("observations_by_archetype", {}).values()
    )
    detected = [row for row in groups if row["detected"]]
    categories = resolve_data_categories(case)
    return {
        "schema_version": DYNAMIC_SCHEMA_VERSION,
        "top_level_sections": sorted(case),
        "top_level_section_count": len(case),
        "registered_indicator_group_count": len(groups),
        "detected_indicator_group_count": len(detected),
        "detected_indicator_groups": detected,
        "all_indicator_groups": groups,
        "explicit_engineering_observation_count": explicit_count,
        "data_categories": categories,
        "note": (
            "数据类别数量只用于盘点；算法能否运行由具体必需字段、单位、覆盖范围和上游结果共同决定。"
        ),
    }


def _indicator_coverage(case: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    return build_indicator_coverage(case)


def _segment_geometry(case: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    rows = [
        {
            "segment_id": row["segment_id"],
            "start_km": float(row["start_km"]),
            "end_km": float(row["end_km"]),
            "length_km": float(row["length_km"]),
        }
        for row in case["segments"]
    ]
    rows.sort(key=lambda row: (row["start_km"], row["segment_id"]))
    return {
        "segment_count": len(rows),
        "total_segment_length_km": sum(row["length_km"] for row in rows),
        "segments": rows,
    }


def _frequency_preflight(case: dict[str, Any]) -> list[dict[str, str]]:
    try:
        resolve_segment_correction_factors(case)
    except (KeyError, TypeError, ValueError) as exc:
        return [
            {
                "path": "frequency_correction_model/segment_correction_factor/engineering_indicators",
                "label_zh": f"失效频率修正模型输入不完整：{exc}",
            }
        ]
    return []


def _failure_frequency(case: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    resolution = resolve_segment_correction_factors(case)
    loc_rows = calculate_loc_frequencies(case, resolution)
    by_segment: defaultdict[str, float] = defaultdict(float)
    by_mechanism: defaultdict[str, float] = defaultdict(float)
    by_loc: defaultdict[str, float] = defaultdict(float)
    for row in loc_rows:
        by_segment[row.segment_id] += row.annual_frequency
        by_loc[row.loc_id] += row.annual_frequency
        for mechanism, value in row.mechanism_contribution.items():
            by_mechanism[mechanism] += value
    ranking = sorted(by_segment.items(), key=lambda item: (-item[1], item[0]))
    return {
        "model_id": "pipeline.syt6891.2.frequency.dynamic.v1",
        "source": "SY/T 6891.2-2020 Annex A",
        "unit": "per_year",
        "total_initiating_frequency_per_year": sum(by_segment.values()),
        "frequency_by_segment_per_year": dict(sorted(by_segment.items())),
        "frequency_by_mechanism_per_year": dict(sorted(by_mechanism.items())),
        "frequency_by_loc_per_year": dict(sorted(by_loc.items())),
        "segment_ranking": [
            {"rank": index, "segment_id": segment_id, "annual_frequency": value}
            for index, (segment_id, value) in enumerate(ranking, start=1)
        ],
        "loc_frequency": [row.to_dict() for row in loc_rows],
        "frequency_correction_model": resolution.diagnostics,
    }


def _leak_points(case: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    spacing = float(case["assessment"]["leak_point_initial_spacing_m"])
    rows = []
    counts = {}
    for segment in case["segments"]:
        points = discretize_segment(segment, spacing)
        counts[str(segment["segment_id"])] = len(points)
        rows.extend(point.to_dict() for point in points)
    return {
        "model_id": "pipeline.syt6891.2.leak_point_discretization.v1",
        "initial_spacing_m": spacing,
        "leak_point_count": len(rows),
        "leak_point_count_by_segment": counts,
        "leak_points": rows,
    }


def _source_term_parameters(case: dict[str, Any]) -> dict[str, Any]:
    return case["standard_formula_test_parameters"]["aqt3046_physical_chain"]


def _aqt3046_source_term(case: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    parameters = _source_term_parameters(case)
    pipeline = case["pipeline"]
    spec = json.loads(
        (MODEL_SPEC_ROOT / "human_aqt3046_v1.json").read_text(encoding="utf-8")
    )
    ambient = float(parameters["ambient_pressure_pa_abs"])
    upstream = float(pipeline["operating_pressure_mpa"]) * 1.0e6
    basis = parameters.get("operating_pressure_basis", "gauge")
    if basis == "gauge":
        upstream += ambient
    elif basis != "absolute":
        raise ValueError("operating_pressure_basis必须为gauge或absolute")
    temperature = float(pipeline["operating_temperature_k"])
    if pipeline.get("gas_composition_mole_fraction") is not None:
        gas_properties = gas_properties_from_case(case)
        gas_property_trace = gas_properties.to_dict()
        molar_mass = gas_properties.molar_mass_kg_mol
        gamma = gas_properties.heat_capacity_ratio
        heat_of_combustion = gas_properties.lower_heating_value_j_kg
    else:
        molar_mass = float(parameters["molar_mass_kg_mol"])
        gamma = float(parameters["gamma"])
        heat_of_combustion = float(parameters["heat_of_combustion_j_kg"])
        gas_property_trace = {
            "property_model_id": "provided_parameter_set_fallback.v1",
            "source": "standard_formula_test_parameters.aqt3046_physical_chain",
            "molar_mass_kg_mol": molar_mass,
            "heat_capacity_ratio": gamma,
            "lower_heating_value_j_kg": heat_of_combustion,
            "reason": "pipeline.gas_composition_mole_fraction未提供",
        }
    discharge = float(parameters["gas_discharge_coefficient"])
    roughness = float(parameters["pipe_absolute_roughness_mm"]) / 1000.0
    minimum_length = float(parameters.get("minimum_rupture_flow_length_m", 1.0))
    orifices = {
        key: float(value) for key, value in spec["loc_orifice_diameter_m"].items()
    }
    rows: list[dict[str, Any]] = []
    for segment in case["segments"]:
        segment_id = str(segment["segment_id"])
        chainage = (float(segment["start_km"]) + float(segment["end_km"])) / 2.0
        for loc_id, diameter in orifices.items():
            release = gas_orifice_mass_flow_rate(
                upstream_pressure_pa_abs=upstream,
                downstream_pressure_pa_abs=ambient,
                temperature_k=temperature,
                molar_mass_kg_mol=molar_mass,
                gamma=gamma,
                discharge_coefficient=discharge,
                orifice_diameter_m=diameter,
            )
            rows.append(
                {
                    "segment_id": segment_id,
                    "loc_id": loc_id,
                    "evaluation_chainage_km": chainage,
                    "mass_flow_rate_kg_s": release.mass_flow_rate_kg_s,
                    "flow_regime": release.flow_regime,
                }
            )
        inner_diameter = (
            float(segment["outside_diameter_mm"])
            - 2.0 * float(segment["wall_thickness_mm"])
        ) / 1000.0
        friction = fanning_friction_factor_fully_rough(
            inner_diameter_m=inner_diameter,
            absolute_roughness_m=roughness,
        )
        lengths = (
            max(
                (chainage - float(segment["upstream_valve_km"])) * 1000.0,
                minimum_length,
            ),
            max(
                (float(segment["downstream_valve_km"]) - chainage) * 1000.0,
                minimum_length,
            ),
        )
        rupture_flow = sum(
            adiabatic_pipe_rupture_mass_flow_rate(
                upstream_pressure_pa_abs=upstream,
                upstream_temperature_k=temperature,
                molar_mass_kg_mol=molar_mass,
                gamma=gamma,
                inner_diameter_m=inner_diameter,
                effective_length_m=length,
                fanning_friction_factor=friction,
            ).mass_flow_rate_kg_s
            for length in lengths
        )
        rows.append(
            {
                "segment_id": segment_id,
                "loc_id": "rupture",
                "evaluation_chainage_km": chainage,
                "mass_flow_rate_kg_s": rupture_flow,
                "flow_regime": "two_sided_adiabatic_initial",
            }
        )
    return {
        "model_id": "human.aqt3046.source_term.dynamic.v1",
        "source": "AQ/T 3046-2013 E.13-E.22",
        "gas_mixture_properties": gas_property_trace,
        "evaluation_basis": "each segment midpoint; rupture is initial two-sided flow",
        "rows": rows,
    }


def _jet_fire_thresholds(case: dict[str, Any], upstream: dict[str, Any]) -> dict[str, Any]:
    source = upstream["aqt3046_source_term"]
    parameters = _source_term_parameters(case)
    heat_of_combustion = float(
        source["gas_mixture_properties"]["lower_heating_value_j_kg"]
    )
    thresholds = [5.0, 12.5, 37.5]
    rows = []
    for source_row in source["rows"]:
        for threshold in thresholds:
            distance = horizontal_jet_fire_threshold_distance_m(
                heat_of_combustion_j_kg=heat_of_combustion,
                mass_flow_rate_kg_s=float(source_row["mass_flow_rate_kg_s"]),
                radiative_fraction=float(parameters["radiative_fraction"]),
                threshold_heat_flux_kw_m2=threshold,
            )
            rows.append(
                {
                    "segment_id": source_row["segment_id"],
                    "loc_id": source_row["loc_id"],
                    "threshold_heat_flux_kw_m2": threshold,
                    "threshold_distance_m": distance,
                }
            )
    return {
        "model_id": "human.aqt3046.jet_fire_threshold.dynamic.v1",
        "source": "AQ/T 3046-2013 E.57-E.59",
        "rows": rows,
    }


def _gbt_preflight(case: dict[str, Any]) -> list[dict[str, str]]:
    try:
        parameters = case["standard_formula_test_parameters"]["gbt34346_annex_c"]
        parameter_segments = parameters["segments"]
        segment_ids = {str(row["segment_id"]) for row in case["segments"]}
        missing = sorted(segment_ids - set(parameter_segments))
        if missing:
            return [
                {
                    "path": "standard_formula_test_parameters.gbt34346_annex_c.segments",
                    "label_zh": f"缺少管段参数：{', '.join(missing)}",
                }
            ]
    except (KeyError, TypeError) as exc:
        return [
            {
                "path": "standard_formula_test_parameters.gbt34346_annex_c",
                "label_zh": f"GB/T 34346附录C参数不完整：{exc}",
            }
        ]
    return []


def _gbt(case: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    return calculate_annex_c_secondary_assessment(case)


def _human_qra_preflight(case: dict[str, Any]) -> list[dict[str, str]]:
    report = validate_case(case)
    return [
        {"path": issue.path, "label_zh": issue.message}
        for issue in report.errors
    ]


def _human_qra(case: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    return QRAEngine().run(case, profile="aqt3046-physical")


def _adaptive_evidence_qra(case: dict[str, Any], _: dict[str, Any]) -> dict[str, Any]:
    return calculate_adaptive_evidence_qra(case)


def _risk_matrix(case: dict[str, Any], upstream: dict[str, Any]) -> dict[str, Any]:
    source_node_id = (
        "human_qra" if "human_qra" in upstream else "adaptive_evidence_qra"
    )
    matrix = build_risk_matrix(
        upstream[source_node_id], case.get("risk_matrix_criteria")
    )
    matrix["source_node_id"] = source_node_id
    matrix["result_tier"] = (
        "FULL_SPATIAL_HUMAN_QRA"
        if source_node_id == "human_qra"
        else "EVIDENCE_CONDITIONED_SCREENING_ESTIMATE"
    )
    return matrix


NODE_REGISTRY: tuple[CalculationNode, ...] = (
    CalculationNode(
        "data_inventory",
        "数据能力盘点",
        "平台数据合同",
        "识别已提供的数据段和工程指标组。",
        (),
        (),
        ("data_inventory_json",),
        _data_inventory,
    ),
    CalculationNode(
        "indicator_coverage",
        "工程指标覆盖率",
        "QRA工程指标目录v1",
        "统计17组、246项指标的已提供观测与缺口。",
        ("data_inventory",),
        (),
        ("indicator_coverage_json", "indicator_coverage_chart"),
        _indicator_coverage,
    ),
    CalculationNode(
        "segment_geometry",
        "管段几何与里程",
        "SY/T 6891.2-2020",
        "形成可供后续频率、泄漏点和后果计算使用的管段表。",
        ("data_inventory",),
        (
            Requirement("segments.*.segment_id", "每段管段编号", True),
            Requirement("segments.*.start_km", "每段起点里程", True),
            Requirement("segments.*.end_km", "每段终点里程", True),
            Requirement("segments.*.length_km", "每段长度", True),
        ),
        ("segment_geometry_json", "segment_length_chart"),
        _segment_geometry,
    ),
    CalculationNode(
        "failure_frequency",
        "管段失效频率",
        "SY/T 6891.2-2020 Annex A",
        "按失效机理、修正系数、管段长度和孔径比例计算频率。",
        ("segment_geometry",),
        (
            Requirement(
                "frequency_library.base_frequency_by_mechanism",
                "分机理基准频率库",
            ),
            Requirement(
                "frequency_library.loc_fraction_by_mechanism",
                "分机理孔径比例",
            ),
        ),
        ("failure_frequency_json", "failure_frequency_chart"),
        _failure_frequency,
        _frequency_preflight,
    ),
    CalculationNode(
        "leak_point_discretization",
        "泄漏点离散",
        "SY/T 6891.2-2020",
        "按管段坐标和初始间距生成频率守恒的泄漏点。",
        ("segment_geometry",),
        (
            Requirement(
                "assessment.leak_point_initial_spacing_m", "泄漏点初始间距"
            ),
            Requirement("segments.*.start_xy_m", "每段起点坐标", True),
            Requirement("segments.*.end_xy_m", "每段终点坐标", True),
        ),
        ("leak_points_json", "leak_point_count_chart"),
        _leak_points,
    ),
    CalculationNode(
        "aqt3046_source_term",
        "AQ/T 3046泄漏源项",
        "AQ/T 3046-2013 E.13-E.22",
        "独立计算小孔、中孔、大孔和完全破裂初始释放率。",
        ("segment_geometry",),
        (
            Requirement("pipeline.operating_pressure_mpa", "运行压力"),
            Requirement("pipeline.operating_temperature_k", "运行温度"),
            Requirement("segments.*.outside_diameter_mm", "每段外径", True),
            Requirement("segments.*.wall_thickness_mm", "每段壁厚", True),
            Requirement("segments.*.upstream_valve_km", "每段上游阀里程", True),
            Requirement("segments.*.downstream_valve_km", "每段下游阀里程", True),
            Requirement(
                "standard_formula_test_parameters.aqt3046_physical_chain.ambient_pressure_pa_abs",
                "环境绝压",
            ),
            Requirement(
                "standard_formula_test_parameters.aqt3046_physical_chain.molar_mass_kg_mol",
                "摩尔质量",
            ),
            Requirement(
                "standard_formula_test_parameters.aqt3046_physical_chain.gamma",
                "绝热指数",
            ),
            Requirement(
                "standard_formula_test_parameters.aqt3046_physical_chain.gas_discharge_coefficient",
                "气体泄放系数",
            ),
            Requirement(
                "standard_formula_test_parameters.aqt3046_physical_chain.pipe_absolute_roughness_mm",
                "管壁绝对粗糙度",
            ),
        ),
        ("source_term_json", "release_rate_chart"),
        _aqt3046_source_term,
    ),
    CalculationNode(
        "jet_fire_thresholds",
        "喷射火影响距离",
        "AQ/T 3046-2013 E.57-E.59",
        "根据已计算释放率输出5、12.5和37.5 kW/m2阈值距离。",
        ("aqt3046_source_term",),
        (
            Requirement(
                "standard_formula_test_parameters.aqt3046_physical_chain.heat_of_combustion_j_kg",
                "燃烧热",
            ),
            Requirement(
                "standard_formula_test_parameters.aqt3046_physical_chain.radiative_fraction",
                "辐射分数",
            ),
        ),
        ("jet_fire_threshold_json", "jet_fire_distance_chart"),
        _jet_fire_thresholds,
    ),
    CalculationNode(
        "gbt34346_annex_c",
        "GB/T 34346附录C定量校核",
        "GB/T 34346-2017 Annex C",
        "独立执行附录C二级评价公式。",
        ("segment_geometry",),
        (
            Requirement("pipeline.operating_pressure_mpa", "运行压力"),
            Requirement("pipeline.operating_temperature_k", "运行温度"),
            Requirement("segments.*.outside_diameter_mm", "每段外径", True),
            Requirement("segments.*.wall_thickness_mm", "每段壁厚", True),
        ),
        ("gbt34346_result_json", "gbt34346_ranking_chart"),
        _gbt,
        _gbt_preflight,
    ),
    CalculationNode(
        "adaptive_evidence_qra",
        "自适应证据定量风险",
        "GB/T 34346-2017 Annex C + AQ/T 3046-2013",
        "以标准公式为骨架，按现有指标更新管段失效概率；缺失指标边缘化并扩大不确定性带，始终形成风险值与排序。",
        ("segment_geometry",),
        (),
        (
            "adaptive_evidence_qra_json",
            "adaptive_risk_ranking_chart",
            "adaptive_uncertainty_chart",
            "adaptive_evidence_factor_chart",
        ),
        _adaptive_evidence_qra,
    ),
    CalculationNode(
        "human_qra",
        "完整人员域QRA",
        "SY/T 6891.2-2020 + AQ/T 3046-2013",
        "执行泄漏点、天气、点火事件树、物理后果、人员伤害、IR、F-N和PLL。",
        (
            "failure_frequency",
            "leak_point_discretization",
            "aqt3046_source_term",
        ),
        (),
        ("human_qra_json", "ir_chart", "fn_chart", "pll_chart"),
        _human_qra,
        _human_qra_preflight,
    ),
    CalculationNode(
        "risk_matrix",
        "自适应风险结果展示矩阵",
        "定量结果展示准则（不替代接受性判定）",
        "完整QRA可用时优先采用完整QRA；否则根据证据定量风险结果生成5x5展示矩阵。",
        ("adaptive_evidence_qra",),
        (),
        ("risk_matrix_json", "risk_matrix_csv", "risk_matrix_chart"),
        _risk_matrix,
    ),
)

NODE_BY_ID = {node.node_id: node for node in NODE_REGISTRY}


def dynamic_node_catalog() -> dict[str, Any]:
    return {
        "schema_version": DYNAMIC_SCHEMA_VERSION,
        "nodes": [
            {
                "node_id": node.node_id,
                "label_zh": node.label_zh,
                "standard": node.standard,
                "description": node.description,
                "dependencies": list(node.dependencies),
                "required_inputs": [
                    {
                        "path": requirement.path,
                        "label_zh": requirement.label_zh,
                        "all_items": requirement.all_items,
                    }
                    for requirement in node.requirements
                ],
                "outputs": list(node.output_ids),
            }
            for node in NODE_REGISTRY
        ],
    }


def _selected_node_ids(targets: Iterable[str] | None) -> set[str]:
    if not targets:
        return set(NODE_BY_ID)
    requested = {str(target) for target in targets}
    unknown = requested - set(NODE_BY_ID)
    if unknown:
        raise ValueError(f"未知动态计算节点：{', '.join(sorted(unknown))}")
    selected: set[str] = set()

    def add(node_id: str) -> None:
        if node_id in selected:
            return
        selected.add(node_id)
        for dependency in NODE_BY_ID[node_id].dependencies:
            add(dependency)

    for node_id in requested:
        add(node_id)
    selected.add("data_inventory")
    selected.add("indicator_coverage")
    return selected


def plan_dynamic_flow(
    case: dict[str, Any], targets: Iterable[str] | None = None
) -> dict[str, Any]:
    selected = _selected_node_ids(targets)
    states: dict[str, str] = {}
    plan = []
    for node in NODE_REGISTRY:
        if node.node_id not in selected:
            continue
        blocked_dependencies = [
            dependency
            for dependency in node.dependencies
            if states.get(dependency) != "RUNNABLE"
        ]
        missing = _missing_requirements(case, node.requirements)
        if not blocked_dependencies and not missing and node.preflight is not None:
            missing.extend(node.preflight(case))
        status = "RUNNABLE" if not blocked_dependencies and not missing else "SKIPPED"
        states[node.node_id] = status
        plan.append(
            {
                "sequence": len(plan) + 1,
                "node_id": node.node_id,
                "label_zh": node.label_zh,
                "standard": node.standard,
                "status": status,
                "dependencies": list(node.dependencies),
                "blocked_dependencies": blocked_dependencies,
                "missing_inputs": missing,
                "planned_outputs": list(node.output_ids),
            }
        )
    return {
        "schema_version": DYNAMIC_SCHEMA_VERSION,
        "selection_mode": "AUTO_BY_AVAILABLE_DATA" if targets is None else "TARGETS_WITH_DEPENDENCIES",
        "requested_targets": list(targets or []),
        "plan": plan,
        "runnable_node_ids": [row["node_id"] for row in plan if row["status"] == "RUNNABLE"],
        "skipped_node_ids": [row["node_id"] for row in plan if row["status"] == "SKIPPED"],
    }


def _svg_bar_chart(
    title: str,
    subtitle: str,
    rows: list[tuple[str, float]],
    path: Path,
    *,
    unit: str,
) -> Path:
    width = 1250
    height = max(520, 150 + len(rows) * 34)
    left, top, plot_w, row_h = 250, 115, 850, 30
    maximum = max((value for _, value in rows), default=1.0) or 1.0
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>text{font-family:'Microsoft YaHei','SimHei',Arial,sans-serif;fill:#243447}.title{font-size:26px;font-weight:700}.sub{font-size:14px;fill:#66788A}.label{font-size:14px}</style>",
        f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>',
        f'<text x="45" y="45" class="title">{title}</text>',
        f'<text x="45" y="73" class="sub">{subtitle}</text>',
    ]
    for index, (label, value) in enumerate(rows):
        y = top + index * row_h
        bar_w = value / maximum * plot_w
        parts.append(f'<text x="{left-12}" y="{y+19}" text-anchor="end" class="label">{label}</text>')
        parts.append(f'<rect x="{left}" y="{y+3}" width="{bar_w:.2f}" height="21" rx="3" fill="#5B9BD5"/>')
        parts.append(f'<text x="{left+bar_w+8:.2f}" y="{y+19}" class="label">{value:.4g}</text>')
    parts.append(f'<text x="{left+plot_w/2}" y="{height-28}" text-anchor="middle" class="sub">{unit}</text>')
    parts.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return path


def _render_node_charts(
    node_id: str,
    result: dict[str, Any],
    all_results: dict[str, Any],
    output_dir: Path,
) -> list[Path]:
    if node_id == "indicator_coverage":
        rows = [
            (stats["name_zh"], float(stats["coverage_fraction"]) * 100.0)
            for stats in result["group_coverage"].values()
        ]
        return [
            _svg_bar_chart(
                "工程指标组覆盖率",
                "覆盖表示已识别的数据槽，不代表指标已进入模型。",
                rows,
                output_dir / "01_指标组覆盖率.svg",
                unit="覆盖率（%）",
            )
        ]
    if node_id == "segment_geometry":
        rows = [(row["segment_id"], row["length_km"]) for row in result["segments"]]
        return [
            _svg_bar_chart(
                "管段长度",
                "仅依据当前输入中能够识别的管段几何数据。",
                rows,
                output_dir / "02_管段长度.svg",
                unit="km",
            )
        ]
    if node_id == "failure_frequency":
        rows = [
            (row["segment_id"], float(row["annual_frequency"]))
            for row in result["segment_ranking"]
        ]
        return [
            _svg_bar_chart(
                "管段起始失效频率排序",
                "SY/T 6891.2附录A频率节点独立输出。",
                rows,
                output_dir / "03_管段失效频率.svg",
                unit="1/年",
            )
        ]
    if node_id == "leak_point_discretization":
        rows = [
            (segment_id, float(count))
            for segment_id, count in result["leak_point_count_by_segment"].items()
        ]
        return [
            _svg_bar_chart(
                "管段泄漏点数量",
                "泄漏点频率份额在每个管段内守恒为1。",
                rows,
                output_dir / "04_泄漏点数量.svg",
                unit="个",
            )
        ]
    if node_id == "aqt3046_source_term":
        rows = [
            (f"{row['segment_id']}/{row['loc_id']}", float(row["mass_flow_rate_kg_s"]))
            for row in result["rows"]
        ]
        rows.sort(key=lambda item: -item[1])
        return [
            _svg_bar_chart(
                "AQ/T 3046泄漏释放率",
                "小孔/中孔/大孔孔口流与完全破裂初始两侧流。",
                rows,
                output_dir / "05_泄漏释放率.svg",
                unit="kg/s",
            )
        ]
    if node_id == "jet_fire_thresholds":
        rows = [
            (
                f"{row['segment_id']}/{row['loc_id']}/{row['threshold_heat_flux_kw_m2']}kW",
                float(row["threshold_distance_m"]),
            )
            for row in result["rows"]
            if float(row["threshold_heat_flux_kw_m2"]) == 37.5
        ]
        rows.sort(key=lambda item: -item[1])
        return [
            _svg_bar_chart(
                "喷射火37.5 kW/m2影响距离",
                "AQ/T 3046喷射火阈值距离节点独立输出。",
                rows,
                output_dir / "06_喷射火影响距离.svg",
                unit="m",
            )
        ]
    if node_id == "gbt34346_annex_c":
        rows = [
            (row["segment_id"], float(row["segment_risk_fatalities_per_year"]))
            for row in result["segment_ranking"]
        ]
        return [
            _svg_bar_chart(
                "GB/T 34346附录C管段风险排序",
                "独立校核链，不替代完整空间QRA。",
                rows,
                output_dir / "07_GBT34346风险排序.svg",
                unit="人/年",
            )
        ]
    if node_id == "adaptive_evidence_qra":
        ranking = result["human_risk"]["segment_risk"]["ranking"]
        risk_rows = [
            (row["segment_id"], float(row["risk_value_fatalities_per_year"]))
            for row in ranking
        ]
        upper_rows = [
            (row["segment_id"], float(row["risk_value_upper_screening_bound"]))
            for row in ranking
        ]
        factor_rows = [
            (row["segment_id"], float(row["evidence_factor"]))
            for row in ranking
        ]
        return [
            _svg_bar_chart(
                "现有证据条件下的管段风险排序",
                "PLL筛查估计；缺失证据按中性贡献处理并进入不确定性带。",
                risk_rows,
                output_dir / "08_证据定量风险排序.svg",
                unit="人/年",
            ),
            _svg_bar_chart(
                "管段风险筛查上界",
                "该上界是模型不确定性带，不是统计置信上限或风险接受阈值。",
                upper_rows,
                output_dir / "09_风险不确定性上界.svg",
                unit="人/年",
            ),
            _svg_bar_chart(
                "完整性证据失效频率更新因子",
                "因子大于1提高基准失效概率，小于1降低；系数须由项目数据校准。",
                factor_rows,
                output_dir / "10_证据更新因子.svg",
                unit="倍",
            ),
        ]
    if node_id == "risk_matrix":
        source_node_id = str(result.get("source_node_id"))
        source_result = all_results[source_node_id]
        if source_node_id == "human_qra":
            return render_charts(source_result, result, output_dir / "human_qra")
        return render_charts(
            source_result,
            result,
            output_dir / "adaptive_evidence_qra",
            (
                "segment_pll_ranking",
                "risk_matrix",
                "route_profile",
                "fn_curve",
                "priority_bubble",
            ),
        )
    return []


def _format_dashboard_number(value: Any) -> str:
    if isinstance(value, float):
        if value != 0.0 and (abs(value) < 0.001 or abs(value) >= 10000):
            return f"{value:.4e}"
        return f"{value:.6g}"
    return str(value)


def _format_chainage(value: Any) -> str:
    try:
        return f"{float(value):.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "—"


def _format_optional_metric(
    value: Any,
    format_spec: str,
    *,
    suffix: str = "",
    missing: str = "不适用/未计算",
) -> str:
    if value is None:
        return missing
    try:
        number = float(value)
    except (TypeError, ValueError):
        return missing
    if not math.isfinite(number):
        return missing
    return f"{format(number, format_spec)}{suffix}"


def _write_dynamic_dashboard(
    case: dict[str, Any],
    results: dict[str, Any],
    capability: dict[str, Any],
    node_records: list[dict[str, Any]],
    output_path: Path,
    *,
    job_id: str,
) -> Path:
    completed = capability["completed_node_ids"]
    skipped = capability["skipped_node_ids"]
    failed = capability["failed_node_ids"]
    category_manifest = (
        capability.get("detected_data", {}).get("data_categories")
        or resolve_data_categories(case)
    )
    category_count = int(category_manifest["category_count"])
    metadata = case.get("metadata", {})
    project_name = str(metadata.get("project_name") or metadata.get("case_id") or "管道定量风险评估")
    case_id = str(metadata.get("case_id") or job_id)
    data_classification = str(metadata.get("data_classification") or "未标记")
    segment_count = len(case.get("segments", []))
    dashboard_source = results.get("human_qra") or results.get("adaptive_evidence_qra", {})
    human = dashboard_source.get("human_risk", {})
    ranking = sorted(
        human.get("segment_risk", {}).get("ranking", []),
        key=lambda row: (int(row.get("risk_value_rank", 10**9)), str(row.get("segment_id", ""))),
    )
    segments = [row for row in case.get("segments", []) if isinstance(row, dict)]
    segments_by_id = {str(row.get("segment_id", "")): row for row in segments}
    ranking_by_segment = {str(row.get("segment_id", "")): row for row in ranking}
    receptors_by_segment: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for cell in case.get("population_cells", []):
        if isinstance(cell, dict):
            receptors_by_segment[str(cell.get("segment_id", ""))].append(cell)
    raw_targets = (
        case.get("raw_data_categories", {})
        .get("high_consequence_targets", {})
        .get("records", [])
    )
    raw_targets_by_segment: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for target in raw_targets:
        if isinstance(target, dict):
            raw_targets_by_segment[str(target.get("segment_id", ""))].append(target)
    matrix = results.get("risk_matrix", {})
    matrix_by_segment = {str(row["segment_id"]): row for row in matrix.get("segments", [])}
    risk_result = capability.get("risk_result", {})
    tier = str(risk_result.get("result_tier") or "NO_QUANTITATIVE_RISK_RESULT")
    tier_label = {
        "EVIDENCE_CONDITIONED_SCREENING_ESTIMATE": "现有证据定量筛查",
        "FULL_SPATIAL_HUMAN_QRA": "完整空间人员域QRA",
    }.get(tier, "尚未形成定量风险结果")
    pipeline_pll = float(human.get("societal_risk", {}).get("pipeline_pll_per_year") or 0.0)
    maximum_ir = float(human.get("individual_risk", {}).get("maximum", {}).get("value_per_year") or 0.0)
    top = ranking[0] if ranking else {}
    top_segment = str(top.get("segment_id") or "—")
    top_share = float(top.get("fraction_of_pipeline_risk_value") or 0.0)
    average_coverage = (
        sum(float(row.get("evidence_diagnostics", {}).get("coverage_fraction") or 0.0) for row in ranking)
        / len(ranking)
        if ranking
        else 0.0
    )

    def target_name(target: dict[str, Any]) -> str:
        return str(
            target.get("receptor_name")
            or target.get("target_name")
            or target.get("receptor_type")
            or target.get("target_type")
            or target.get("source_text")
            or target.get("cell_id")
            or target.get("target_id")
            or "未命名受体"
        )

    def segment_target_labels(segment_id: str) -> list[str]:
        labels: list[str] = []
        for target in [
            *receptors_by_segment.get(segment_id, []),
            *raw_targets_by_segment.get(segment_id, []),
        ]:
            label = target_name(target)
            if label not in labels:
                labels.append(label)
        return labels

    def segment_display_label(segment: dict[str, Any]) -> str:
        survey_point = segment.get("survey_point_id")
        if survey_point not in (None, ""):
            return f"踏勘点{survey_point}"
        return str(
            segment.get("segment_name")
            or segment.get("segment_label")
            or segment.get("map_label")
            or "输入管段"
        )

    def segment_position_text(segment_id: str) -> str:
        segment = segments_by_id.get(segment_id, {})
        point_text = segment_display_label(segment)
        return (
            f"{point_text}，"
            f"{_format_chainage(segment.get('start_km'))}–{_format_chainage(segment.get('end_km'))} km"
        )

    top_position = segment_position_text(top_segment) if top_segment != "—" else "—"
    top_card_value = f"{top_segment} / {top_position.split('，', 1)[0]}" if top_segment != "—" else "—"

    cards = [
        ("定量结果", "已生成" if risk_result.get("available") else "未生成", "green" if risk_result.get("available") else "red"),
        ("结果层级", tier_label, "blue"),
        ("评估管段", f"{segment_count} 段", "blue"),
        ("全线PLL", f"{pipeline_pll:.4e} 人/年" if human else "—", "navy"),
        ("最高风险管段", top_card_value, "amber"),
        ("最高段风险占比", f"{top_share:.1%}" if ranking else "—", "amber"),
        ("最大IR估计", f"{maximum_ir:.4e} /年" if human else "—", "navy"),
        ("平均证据覆盖", f"{average_coverage:.0%}" if ranking else "—", "green"),
    ]
    cards_html = "".join(
        '<article class="metric-card accent-'
        + accent
        + '"><div class="metric-label">'
        + escape(label)
        + '</div><div class="metric-value">'
        + escape(value)
        + "</div></article>"
        for label, value, accent in cards
    )

    assumptions = case.get("calculation_assumptions", {})
    segmentation_basis = str(
        assumptions.get("segmentation_basis")
        or "按照输入JSON中segments数组的起止里程划分。"
    )
    coordinate_basis = str(
        assumptions.get("coordinate_basis")
        or "根据输入管段坐标绘制的模型线路示意，不叠加在线地理底图。"
    )
    receptor_distance = assumptions.get("receptor_lateral_distance_m")
    receptor_basis = str(
        assumptions.get("receptor_distance_basis")
        or "受体位置与距离沿用输入数据。"
    )

    locator_html = ""
    if segments:
        route_start = min(float(row.get("start_km") or 0.0) for row in segments)
        route_end = max(float(row.get("end_km") or 0.0) for row in segments)
        route_span = max(1e-9, route_end - route_start)
        svg_width = 1400.0
        svg_height = 360.0
        left_margin = 72.0
        route_width = svg_width - 2.0 * left_margin
        route_y = 180.0

        def route_x(chainage: Any) -> float:
            return left_margin + (
                (float(chainage or route_start) - route_start) / route_span
            ) * route_width

        band_colors = {
            "LOW": "#91a4b4",
            "MEDIUM": "#e0b83f",
            "MEDIUM_HIGH": "#e5893d",
            "HIGH": "#d94b55",
        }
        svg_parts = [
            f'<svg class="segment-route-svg" viewBox="0 0 {svg_width:.0f} {svg_height:.0f}" role="img" aria-label="管段划分与受体位置示意图">',
            '<rect x="1" y="1" width="1398" height="358" rx="18" fill="#f8fbfd" stroke="#dfe7ee"/>',
            '<text x="36" y="38" class="route-title">管段与受体定位示意</text>',
            '<text x="36" y="60" class="route-subtitle">点击彩色管段可跳转至下方明细；图形坐标性质见页面说明</text>',
            '<text x="36" y="88" class="route-side-label">北侧 / 坐标正向</text>',
            '<text x="36" y="332" class="route-side-label">南侧 / 坐标负向</text>',
            f'<text x="{left_margin:.1f}" y="224" class="route-end-label" text-anchor="start">{escape(str(case.get("pipeline", {}).get("hca_start_marker") or "起点"))}</text>',
            f'<text x="{left_margin + route_width:.1f}" y="224" class="route-end-label" text-anchor="end">{escape(str(case.get("pipeline", {}).get("hca_end_marker") or "终点"))}</text>',
        ]
        for segment in segments:
            segment_id = str(segment.get("segment_id", ""))
            x1 = route_x(segment.get("start_km"))
            x2 = route_x(segment.get("end_km"))
            mid_x = (x1 + x2) / 2.0
            matrix_row = matrix_by_segment.get(segment_id, {})
            band = str(matrix_row.get("display_risk_band") or "LOW")
            color = band_colors.get(band, "#5b9bd5")
            risk_row = ranking_by_segment.get(segment_id, {})
            rank = risk_row.get("risk_value_rank")
            survey_point = segment.get("survey_point_id")
            point_label = str(
                survey_point
                if survey_point not in (None, "")
                else segment.get("map_label") or segment_id
            )
            tooltip = (
                f"{segment_id}｜{segment_position_text(segment_id)}｜"
                f"风险排名：{rank or '—'}｜周边目标：{'、'.join(segment_target_labels(segment_id)) or '未记录'}"
            )
            svg_parts.extend(
                [
                    f'<a href="#segment-{escape(segment_id, quote=True)}">',
                    f'<line class="route-segment" x1="{x1:.2f}" y1="{route_y:.2f}" x2="{x2:.2f}" y2="{route_y:.2f}" stroke="{color}"><title>{escape(tooltip)}</title></line>',
                    f'<text x="{mid_x:.2f}" y="160" class="route-segment-label" text-anchor="middle">{escape(point_label)}</text>',
                    f'<text x="{mid_x:.2f}" y="201" class="route-rank-label" text-anchor="middle">{escape(f"第{rank}" if rank else "—")}</text>',
                    '</a>',
                ]
            )
            side_groups: dict[str, list[dict[str, Any]]] = {
                "north": [],
                "south": [],
                "unknown": [],
            }
            for cell in receptors_by_segment.get(segment_id, []):
                xy = cell.get("xy_m")
                side_value = str(
                    cell.get("side") or cell.get("side_status") or ""
                ).strip().lower()
                if side_value in {"未知", "未定位", "unknown", "unlocated"}:
                    side = "unknown"
                elif isinstance(xy, list) and len(xy) >= 2 and float(xy[1]) < 0:
                    side = "south"
                else:
                    side = "north"
                side_groups[side].append(cell)
            for side, cells in side_groups.items():
                if not cells:
                    continue
                day = sum(float(cell.get("population_day") or 0.0) for cell in cells)
                night = sum(float(cell.get("population_night") or 0.0) for cell in cells)
                population_peak = max(day, night)
                radius = min(22.0, 7.0 + math.sqrt(max(0.0, population_peak)) / 4.0)
                bubble_y = 104.0 if side in {"north", "unknown"} else 272.0
                text_y = 78.0 if side in {"north", "unknown"} else 310.0
                names = "、".join(target_name(cell) for cell in cells)
                side_text = {"north": "北侧", "south": "南侧", "unknown": "侧向未定位（上方示意）"}[side]
                tooltip = f"{segment_id} {side_text}｜{names}｜日{day:g}人 / 夜{night:g}人"
                svg_parts.extend(
                    [
                        f'<circle class="route-receptor" cx="{mid_x:.2f}" cy="{bubble_y:.2f}" r="{radius:.2f}"><title>{escape(tooltip)}</title></circle>',
                        f'<text x="{mid_x:.2f}" y="{text_y:.2f}" class="route-pop-label" text-anchor="middle">日{day:g}/夜{night:g}</text>',
                        f'<line x1="{mid_x:.2f}" y1="{bubble_y + (radius if side in {"north", "unknown"} else -radius):.2f}" x2="{mid_x:.2f}" y2="{route_y + (-9 if side in {"north", "unknown"} else 9):.2f}" stroke="#b7c7d4" stroke-dasharray="3 4"/>',
                    ]
                )
        svg_parts.extend(
            [
                '<g class="route-legend" transform="translate(1030 34)">',
                '<circle cx="0" cy="0" r="7" fill="#7952a2" fill-opacity=".72"/><text x="13" y="4">人口/目标受体</text>',
                '<line x1="120" y1="0" x2="148" y2="0" stroke="#d94b55" stroke-width="10" stroke-linecap="round"/><text x="157" y="4">高风险带</text>',
                '<line x1="240" y1="0" x2="268" y2="0" stroke="#91a4b4" stroke-width="10" stroke-linecap="round"/><text x="277" y="4">低风险带</text>',
                '</g>',
                '</svg>',
            ]
        )

        locator_rows = []
        for segment in segments:
            segment_id = str(segment.get("segment_id", ""))
            risk_row = ranking_by_segment.get(segment_id, {})
            point_label = segment_display_label(segment)
            cells = receptors_by_segment.get(segment_id, [])
            day = sum(float(cell.get("population_day") or 0.0) for cell in cells)
            night = sum(float(cell.get("population_night") or 0.0) for cell in cells)
            targets = segment_target_labels(segment_id)
            target_summary = "、".join(targets[:5]) + (f" 等{len(targets)}项" if len(targets) > 5 else "")
            locator_rows.append(
                f'<tr id="segment-{escape(segment_id, quote=True)}"><td><b>{escape(segment_id)}</b></td>'
                f'<td>{escape(point_label)}</td>'
                f'<td class="number">{_format_chainage(segment.get("start_km"))}–{_format_chainage(segment.get("end_km"))} km</td>'
                f'<td class="number">{float(segment.get("length_km") or 0.0):.4f} km</td>'
                f'<td>{escape(target_summary or "未记录")}</td>'
                f'<td class="number">{day:g} / {night:g}</td>'
                f'<td><span class="rank">{escape(str(risk_row.get("risk_value_rank") or "—"))}</span></td>'
                f'<td class="number">{float(risk_row.get("risk_value_fatalities_per_year") or 0.0):.4e}</td></tr>'
            )
        distance_note = (
            f"统一受体代理距离：{_format_dashboard_number(receptor_distance)} m。{receptor_basis}"
            if receptor_distance is not None
            else receptor_basis
        )
        locator_html = (
            '<section id="locator"><div class="section-heading"><div><div class="eyebrow">空间定位</div><h2>管段定位与划分依据</h2></div>'
            '<p>先理解“哪一段”，再查看其风险值。当前图为计算模型线路示意，不自动等同于真实GIS地图。</p></div>'
            '<div class="segmentation-notes">'
            f'<div><span>划分依据</span><b>{escape(segmentation_basis)}</b></div>'
            f'<div><span>坐标性质</span><b>{escape(coordinate_basis)}</b></div>'
            f'<div><span>受体定位</span><b>{escape(distance_note)}</b></div>'
            '</div><div class="route-map-card">'
            + "".join(svg_parts)
            + '</div><div class="table-wrap segment-table"><table><thead><tr><th>计算管段</th><th>现场对应</th><th>起止里程</th><th>长度</th><th>周边目标</th><th>昼/夜人口</th><th>风险排名</th><th>PLL（人/年）</th></tr></thead><tbody>'
            + "".join(locator_rows)
            + '</tbody></table></div></section>'
        )

    loc_labels = {
        "small_5mm": "5 mm小孔泄漏",
        "medium_25mm": "25 mm中孔泄漏",
        "large_100mm": "100 mm大孔泄漏",
        "large_150mm": "150 mm大孔泄漏",
        "rupture": "完全破裂",
    }
    branch_labels = {
        "jet_fire": "喷射火",
        "flash_fire": "闪火",
        "vce": "蒸气云爆炸",
        "safe_dispersion": "未点火扩散",
    }

    def scenario_label(scenario: dict[str, Any]) -> str:
        loc_id = str(scenario.get("scenario_id") or scenario.get("loc_id") or "")
        branch_id = str(scenario.get("branch_id") or "")
        parts = []
        if loc_id:
            parts.append(loc_labels.get(loc_id, loc_id))
        if branch_id:
            parts.append(branch_labels.get(branch_id, branch_id))
        return "—" if not parts else "—".join(parts)

    dominant = top.get("dominant_risk_scenario", {}) if top else {}
    dominant_label = scenario_label(dominant)
    lower_bound = _format_optional_metric(
        top.get("risk_value_lower_screening_bound"), ".3e"
    )
    upper_bound = _format_optional_metric(
        top.get("risk_value_upper_screening_bound"), ".3e"
    )
    fatal_distance = _format_optional_metric(
        dominant.get("fatal_heat_flux_distance_m"), ".1f", suffix=" m"
    )
    result_summary_html = (
        '<section id="overview" class="summary-panel"><div><div class="eyebrow">本次评估结论</div>'
        f'<h2>{escape(top_segment)} 是当前最高风险管段</h2>'
        f'<p class="top-location"><b>对应位置：</b>{escape(top_position)}；周边目标：{escape("、".join(segment_target_labels(top_segment)) or "未记录")}</p>'
        f'<p>本次根据 <b>{category_count} 类输入数据</b>完成 {segment_count} 个管段的{escape(tier_label)}。'
        f'{escape(top_segment)} 的PLL为 <b>{float(top.get("risk_value_fatalities_per_year") or 0.0):.4e} 人/年</b>，'
        f'占全线风险的 <b>{top_share:.1%}</b>；主导场景为 <b>{escape(dominant_label)}</b>。</p></div>'
        '<div class="summary-stats">'
        f'<div><span>筛查下界</span><b>{escape(lower_bound)}</b></div>'
        f'<div><span>当前估计</span><b>{float(top.get("risk_value_fatalities_per_year") or 0.0):.3e}</b></div>'
        f'<div><span>筛查上界</span><b>{escape(upper_bound)}</b></div>'
        f'<div><span>致死热辐射距离</span><b>{escape(fatal_distance)}</b></div>'
        "</div></section>"
        if ranking
        else '<section id="overview" class="notice danger"><b>尚未形成管段风险排序。</b> 请先提供可识别的管段评估单元。</section>'
    )

    priority_rows = []
    for row in ranking:
        diagnostics = row.get("evidence_diagnostics", {})
        observed_terms = sorted(
            (term for term in diagnostics.get("terms", []) if term.get("status") == "OBSERVED"),
            key=lambda term: -float(term.get("log_factor_contribution") or 0.0),
        )
        drivers = "、".join(str(term.get("name_zh")) for term in observed_terms[:3]) or "模型先验"
        matrix_row = matrix_by_segment.get(str(row.get("segment_id")), {})
        scenario = row.get("dominant_risk_scenario", {})
        scenario_display = scenario_label(scenario)
        scenario_distance = _format_optional_metric(
            scenario.get("fatal_heat_flux_distance_m"), ".1f", suffix=" m"
        )
        interval_lower = _format_optional_metric(
            row.get("risk_value_lower_screening_bound"), ".2e"
        )
        interval_upper = _format_optional_metric(
            row.get("risk_value_upper_screening_bound"), ".2e"
        )
        band = str(matrix_row.get("display_risk_band") or "LOW")
        segment_id = str(row.get("segment_id", ""))
        target_summary = "、".join(segment_target_labels(segment_id)[:4]) or "未记录目标"
        priority_rows.append(
            f'<tr><td><span class="rank">{escape(str(row.get("risk_value_rank", "")))}</span></td>'
            f'<td><b>{escape(segment_id)}</b><small>{escape(segment_position_text(segment_id))}</small><small>{escape(target_summary)}</small></td>'
            f'<td class="number">{float(row.get("risk_value_fatalities_per_year") or 0.0):.4e}</td>'
            f'<td>{float(row.get("fraction_of_pipeline_risk_value") or 0.0):.1%}</td>'
            f'<td><span class="risk-band band-{escape(band.lower().replace("_", "-"))}">{escape(str(matrix_row.get("display_risk_band_zh") or "筛查"))}</span></td>'
            f'<td>{escape(scenario_display)}<small>{escape(scenario_distance)}</small></td>'
            f'<td>{escape(drivers)}</td>'
            f'<td>{float(diagnostics.get("coverage_fraction") or 0.0):.0%}</td>'
            f'<td class="number">{escape(interval_lower)} – {escape(interval_upper)}</td></tr>'
        )
    risk_html = (
        '<section id="ranking"><div class="section-heading"><div><div class="eyebrow">优先级</div><h2>管段风险排序与驱动因素</h2></div>'
        '<p>按PLL由高到低排列；展示色带用于筛选，不等同于风险接受结论。</p></div>'
        '<div class="table-wrap risk-table"><table><thead><tr><th>排名</th><th>管段/里程</th><th>PLL（人/年）</th><th>全线占比</th><th>展示风险带</th><th>主导场景/距离</th><th>主要证据驱动</th><th>证据覆盖</th><th>筛查区间</th></tr></thead><tbody>'
        + "".join(priority_rows)
        + "</tbody></table></div></section>"
        if priority_rows
        else ""
    )

    chart_items: list[tuple[str, str, str]] = []
    for row in node_records:
        for chart in row.get("charts", []):
            stem = Path(str(chart)).stem
            title = stem.split("_", 1)[1] if "_" in stem else stem
            chart_items.append((str(chart), title, str(row["label_zh"])))
    featured_names = {"管段PLL排序", "风险矩阵", "沿线风险剖面"}
    data_names = {"指标组覆盖率", "管段长度", "泄漏点数量"}

    def chart_markup(items: list[tuple[str, str, str]], *, wide: bool = False) -> str:
        return "".join(
            '<figure class="chart-card'
            + (" wide" if wide else "")
            + '"><div class="chart-title">'
            + escape(title)
            + '</div><div class="chart-source">'
            + escape(source)
            + '</div><img loading="lazy" src="'
            + escape(path)
            + '" alt="'
            + escape(title)
            + '"></figure>'
            for path, title, source in items
        )

    featured = [item for item in chart_items if item[1] in featured_names]
    supporting = [item for item in chart_items if item[1] not in featured_names | data_names]
    data_figures = [item for item in chart_items if item[1] in data_names]
    charts_html = (
        '<section id="charts"><div class="section-heading"><div><div class="eyebrow">核心图谱</div><h2>风险分布与优先级</h2></div>'
        '<p>图表均由本次计算结果自动生成，可单独打开SVG查看。</p></div><div class="figure-stack">'
        + chart_markup(featured, wide=True)
        + '</div><h3 class="subheading">风险诊断图</h3><div class="figure-grid">'
        + chart_markup(supporting)
        + '</div><h3 class="subheading">数据基础图</h3><div class="figure-grid compact">'
        + chart_markup(data_figures)
        + "</div></section>"
        if chart_items
        else '<section id="charts"><div class="notice">本次未生成图表。请确认运行命令未使用 --no-charts。</div></section>'
    )

    category_rows = "".join(
        "<tr><td>"
        + escape(str(category.get("name_zh", category.get("category_id", ""))))
        + "</td><td><code>"
        + escape(str(category.get("category_id", "")))
        + "</code></td><td>"
        + escape(str(category.get("record_count", "")))
        + "</td></tr>"
        for category in category_manifest.get("categories", [])
    )
    node_rows = []
    status_labels = {
        "COMPLETED": "已完成",
        "SKIPPED_MISSING_INPUT": "待补充数据",
        "SKIPPED_DEPENDENCY_FAILED": "上游未完成",
        "FAILED_ISOLATED": "运行异常",
    }
    for row in node_records:
        result_link = f'<a href="{escape(str(row["result"]))}">查看JSON</a>' if row.get("result") else "—"
        missing = "；".join(str(item.get("label_zh", item.get("path", ""))) for item in row.get("missing_inputs", []))
        blocked = "、".join(str(item) for item in row.get("blocked_dependencies", []))
        reason = missing or (f"上游节点：{blocked}" if blocked else "—")
        status = str(row["status"])
        status_class = "ok" if status == "COMPLETED" else "bad" if status == "FAILED_ISOLATED" else "warn"
        node_rows.append(
            f'<tr><td>{row["sequence"]}</td><td><b>{escape(str(row["label_zh"]))}</b></td><td>{escape(str(row["standard"]))}</td>'
            f'<td><span class="status {status_class}">{escape(status_labels.get(status, status))}</span></td><td>{escape(reason)}</td><td>{result_link}</td></tr>'
        )
    missing_rows = "".join(
        "<tr><td>" + escape(str(item["label_zh"])) + "</td><td><code>" + escape(str(item["path"])) + "</code></td></tr>"
        for item in capability.get("missing_inputs", [])
    ) or '<tr><td colspan="2">当前没有待补充输入。</td></tr>'
    usage_rows = "".join(
        "<tr><td>" + escape(str(row["name_zh"])) + "</td><td>" + escape("、".join(row["registered_consumer_node_ids"]) or "尚未注册")
        + "</td><td>" + escape("、".join(row["completed_consumer_node_ids"]) or "—") + "</td><td>" + ("是" if row["directly_consumed"] else "否") + "</td></tr>"
        for row in capability.get("data_group_algorithm_usage", [])
    )
    details_html = f"""<section id="details"><div class="section-heading"><div><div class="eyebrow">可追溯性</div><h2>计算与数据明细</h2></div><p>默认折叠，审查或补数时展开。</p></div>
<details><summary>输入数据类别 <span>{category_count} 类</span></summary><div class="table-wrap"><table><thead><tr><th>数据类别</th><th>类别ID</th><th>记录数</th></tr></thead><tbody>{category_rows}</tbody></table></div></details>
<details><summary>动态计算节点 <span>{len(completed)} 已完成 / {len(node_records)} 总计</span></summary><div class="table-wrap"><table><thead><tr><th>#</th><th>节点</th><th>标准/依据</th><th>状态</th><th>说明</th><th>结果</th></tr></thead><tbody>{''.join(node_rows)}</tbody></table></div></details>
<details><summary>继续升级完整QRA所需数据 <span>{len(capability.get('missing_inputs', []))} 项</span></summary><div class="table-wrap"><table><thead><tr><th>数据项</th><th>JSON路径</th></tr></thead><tbody>{missing_rows}</tbody></table></div></details>
<details><summary>指标组与算法使用情况</summary><div class="table-wrap"><table><thead><tr><th>指标组</th><th>已注册算法</th><th>本次完成算法</th><th>已进入计算</th></tr></thead><tbody>{usage_rows}</tbody></table></div></details></section>"""

    blockers = dashboard_source.get("formal_report_blockers") or dashboard_source.get("run", {}).get("formal_report_blockers", [])
    blockers_html = "".join("<li>" + escape(str(item)) + "</li>" for item in blockers)
    boundary_html = (
        '<section id="boundary" class="boundary"><div><div class="eyebrow">使用边界</div><h2>结果可以排序和辅助决策，但尚不能直接作为接受性结论</h2>'
        f'<p>流程状态为 {escape(str(capability["status"]))}：已完成 {len(completed)} 个节点，待补数 {len(skipped)} 个，运行异常 {len(failed)} 个。'
        '缺失数据不会阻断已有证据计算，但会影响结果层级和不确定性。</p></div><ul>'
        + blockers_html
        + "</ul></section>"
    )
    synthetic_html = (
        '<div class="synthetic-banner"><b>测试数据：</b>'
        + escape(str(metadata.get("warning") or "本次输入标记为虚拟/测试数据，不得用于真实工程结论。"))
        + "</div>"
        if "SYNTHETIC" in data_classification.upper()
        else ""
    )

    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(project_name)}｜QRA计算结果</title><style>
:root{{--navy:#123858;--navy-2:#1f557e;--blue:#2876b3;--cyan:#5b9bd5;--bg:#f3f6f9;--panel:#fff;--text:#203142;--muted:#66788a;--line:#dfe7ee;--green:#2f7d4a;--amber:#a85f08;--red:#b42318;--shadow:0 8px 28px rgba(20,57,86,.08)}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--bg);color:var(--text);font-family:'Microsoft YaHei','PingFang SC',Arial,sans-serif;line-height:1.6}}
.hero{{background:linear-gradient(125deg,#102f4a 0%,#18517a 58%,#2876b3 100%);color:#fff;padding:42px max(24px,5vw) 34px;position:relative;overflow:hidden}}.hero:after{{content:"";position:absolute;width:420px;height:420px;border:70px solid rgba(255,255,255,.05);border-radius:50%;right:-110px;top:-220px}}.hero-inner{{max-width:1500px;margin:auto;position:relative;z-index:1}}.hero-top{{display:flex;justify-content:space-between;gap:24px;align-items:flex-start}}.hero h1{{font-size:34px;line-height:1.28;margin:7px 0 10px}}.hero p{{margin:0;color:#d9e9f5}}.hero-meta{{display:flex;gap:9px;flex-wrap:wrap;margin-top:18px}}.pill{{border:1px solid rgba(255,255,255,.26);background:rgba(255,255,255,.10);border-radius:999px;padding:6px 12px;font-size:13px}}.result-pill{{background:#dff6e7;color:#1b6837;border:0;font-weight:700;white-space:nowrap}}
.nav{{background:#fff;border-bottom:1px solid var(--line);position:sticky;top:0;z-index:10;box-shadow:0 3px 14px rgba(18,56,88,.05)}}.nav-inner{{max-width:1500px;margin:auto;padding:0 26px;display:flex;gap:28px;overflow:auto}}.nav a{{color:#41596f;text-decoration:none;padding:14px 0;border-bottom:3px solid transparent;white-space:nowrap;font-size:14px}}.nav a:hover{{color:var(--blue);border-color:var(--blue)}}
main{{max-width:1500px;margin:auto;padding:26px}}section{{margin-bottom:30px}}.synthetic-banner{{max-width:1500px;margin:18px auto 0;background:#fff5df;color:#7a4a09;border:1px solid #f2d49b;border-radius:10px;padding:12px 16px}}
.metrics{{display:grid;grid-template-columns:repeat(4,minmax(185px,1fr));gap:14px}}.metric-card{{background:var(--panel);border-radius:12px;padding:18px 19px;border-top:4px solid var(--cyan);box-shadow:var(--shadow);min-height:108px}}.metric-card.accent-green{{border-color:#55a872}}.metric-card.accent-amber{{border-color:#e0a044}}.metric-card.accent-navy{{border-color:var(--navy)}}.metric-card.accent-red{{border-color:var(--red)}}.metric-label{{color:var(--muted);font-size:13px}}.metric-value{{font-size:21px;line-height:1.35;font-weight:750;margin-top:9px;word-break:break-word}}
.summary-panel{{display:grid;grid-template-columns:1.35fr 1fr;gap:28px;background:linear-gradient(135deg,#fff,#edf5fb);border:1px solid #d8e6f2;border-radius:15px;padding:28px;box-shadow:var(--shadow)}}.eyebrow{{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--blue);font-weight:800}}h2{{font-size:24px;line-height:1.35;margin:5px 0 10px}}h3.subheading{{font-size:18px;margin:24px 0 12px}}.summary-panel p{{font-size:16px;margin:0;color:#41596f}}.summary-stats{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}.summary-stats div{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:13px}}.summary-stats span,.summary-stats b{{display:block}}.summary-stats span{{font-size:12px;color:var(--muted)}}.summary-stats b{{font-size:17px;margin-top:3px}}
.summary-panel .top-location{{margin:0 0 12px;padding:9px 12px;border-radius:8px;background:#e6f1f8;color:#234b68;font-size:14px}}
.section-heading{{display:flex;justify-content:space-between;align-items:end;gap:24px;margin-bottom:14px}}.section-heading h2{{margin-bottom:0}}.section-heading p{{margin:0;color:var(--muted);font-size:14px;max-width:560px;text-align:right}}
.segmentation-notes{{display:grid;grid-template-columns:1.35fr 1fr 1fr;gap:12px;margin-bottom:14px}}.segmentation-notes>div{{background:#fff;border:1px solid var(--line);border-radius:11px;padding:14px 16px;box-shadow:0 3px 12px rgba(20,57,86,.04)}}.segmentation-notes span,.segmentation-notes b{{display:block}}.segmentation-notes span{{color:var(--muted);font-size:12px;margin-bottom:4px}}.segmentation-notes b{{font-size:13px;line-height:1.55}}.route-map-card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:12px;box-shadow:var(--shadow);overflow:auto;margin-bottom:14px}}.segment-route-svg{{display:block;width:100%;min-width:980px;height:auto}}.route-title{{font-size:20px;font-weight:750;fill:#123858}}.route-subtitle,.route-side-label,.route-end-label{{font-size:12px;fill:#66788a}}.route-segment{{stroke-width:13;stroke-linecap:round;cursor:pointer;transition:stroke-width .15s,filter .15s}}.route-segment:hover{{stroke-width:19;filter:drop-shadow(0 2px 3px rgba(18,56,88,.25))}}.route-segment-label{{font-size:12px;font-weight:800;fill:#203142;pointer-events:none}}.route-rank-label{{font-size:10px;fill:#66788a;pointer-events:none}}.route-receptor{{fill:#7952a2;fill-opacity:.72;stroke:#fff;stroke-width:2}}.route-pop-label{{font-size:10px;fill:#573d73}}.route-legend text{{font-size:11px;fill:#66788a}}.segment-table tr:target{{background:#fff0c9;scroll-margin-top:72px}}.segment-table td:nth-child(1),.segment-table td:nth-child(2){{white-space:nowrap}}
.table-wrap{{overflow:auto;background:#fff;border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}}table{{width:100%;border-collapse:collapse;min-width:880px}}th,td{{padding:12px 13px;border-bottom:1px solid #e8eef3;text-align:left;vertical-align:middle;font-size:14px}}th{{background:#eaf2f8;color:var(--navy);font-size:13px;white-space:nowrap;position:sticky;top:0}}tr:last-child td{{border-bottom:0}}tbody tr:hover{{background:#f8fbfd}}td.number{{font-family:Consolas,'Courier New',monospace;white-space:nowrap}}td small{{display:block;color:var(--muted);font-size:12px}}.rank{{display:inline-grid;place-items:center;width:28px;height:28px;border-radius:50%;background:#e7f1f8;color:var(--navy);font-weight:800}}.risk-table tbody tr:nth-child(-n+3){{background:#fffaf2}}.risk-table tbody tr:nth-child(-n+3) .rank{{background:#f4b45c;color:#563307}}.risk-band,.status{{display:inline-block;border-radius:999px;padding:4px 9px;font-size:12px;font-weight:750;white-space:nowrap}}.band-low{{background:#eef2f5;color:#526270}}.band-medium{{background:#fff0b7;color:#795800}}.band-medium-high{{background:#ffe0c4;color:#8b4500}}.band-high{{background:#ffd7dc;color:#951d2b}}.status.ok{{background:#e4f4e9;color:var(--green)}}.status.warn{{background:#fff2d7;color:var(--amber)}}.status.bad{{background:#fee8e7;color:var(--red)}}
.figure-stack{{display:grid;gap:20px}}.figure-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}}.figure-grid.compact{{grid-template-columns:repeat(3,minmax(0,1fr))}}.chart-card{{margin:0;background:#fff;border:1px solid var(--line);border-radius:13px;padding:18px;box-shadow:var(--shadow);overflow:hidden}}.chart-card.wide{{padding:22px}}.chart-title{{font-size:17px;font-weight:750}}.chart-source{{font-size:12px;color:var(--muted);margin:1px 0 10px}}.chart-card img{{display:block;width:100%;height:auto;max-height:780px;object-fit:contain}}
details{{background:#fff;border:1px solid var(--line);border-radius:11px;margin-bottom:10px;box-shadow:0 3px 12px rgba(20,57,86,.04)}}summary{{cursor:pointer;padding:15px 18px;font-weight:750;list-style:none}}summary::-webkit-details-marker{{display:none}}summary:after{{content:"＋";float:right;color:var(--blue)}}details[open] summary:after{{content:"－"}}summary span{{font-weight:400;color:var(--muted);font-size:13px;margin-left:8px}}details .table-wrap{{border-radius:0 0 10px 10px;box-shadow:none;border-width:1px 0 0}}code{{color:#315b7d;white-space:nowrap}}a{{color:#1769aa;text-decoration:none}}a:hover{{text-decoration:underline}}
.boundary{{display:grid;grid-template-columns:1.2fr 1fr;gap:28px;background:#fff8ec;border:1px solid #f2d49b;border-left:7px solid #dda144;border-radius:12px;padding:24px}}.boundary p{{margin:0;color:#6a553b}}.boundary ul{{margin:0;padding-left:20px;color:#6a553b}}.notice{{background:#fff;border-radius:12px;padding:18px;border-left:6px solid var(--red)}}footer{{color:var(--muted);text-align:center;padding:0 20px 34px;font-size:13px}}
@media(max-width:1050px){{.metrics{{grid-template-columns:repeat(2,1fr)}}.summary-panel,.boundary{{grid-template-columns:1fr}}.segmentation-notes{{grid-template-columns:1fr}}.figure-grid.compact{{grid-template-columns:1fr 1fr}}}}@media(max-width:720px){{.hero{{padding:28px 20px}}.hero h1{{font-size:27px}}.hero-top,.section-heading{{display:block}}.result-pill{{display:inline-block;margin-top:14px}}main{{padding:18px}}.metrics,.summary-stats,.figure-grid,.figure-grid.compact{{grid-template-columns:1fr}}.section-heading p{{text-align:left;margin-top:6px}}.nav-inner{{padding:0 18px}}}}
</style></head><body>
<header class="hero"><div class="hero-inner"><div class="hero-top"><div><div class="eyebrow" style="color:#93c9ed">动态QRA计算结果</div><h1>{escape(project_name)}</h1><p>基于现有数据自动匹配计算节点，并随数据完整度升级评估层级</p></div><span class="pill result-pill">{'风险结果已生成' if risk_result.get('available') else '等待可计算输入'}</span></div><div class="hero-meta"><span class="pill">案例：{escape(case_id)}</span><span class="pill">输入：{category_count} 类数据</span><span class="pill">结果：{escape(tier_label)}</span><span class="pill">流程：{escape(str(capability['status']))}</span></div></div></header>
<nav class="nav"><div class="nav-inner"><a href="#overview">结论总览</a><a href="#locator">管段定位</a><a href="#ranking">管段排序</a><a href="#charts">风险图谱</a><a href="#details">计算明细</a><a href="#boundary">使用边界</a></div></nav>
{synthetic_html}<main><section class="metrics">{cards_html}</section>{result_summary_html}{locator_html}{risk_html}{charts_html}{details_html}{boundary_html}</main>
<footer>计算任务：{escape(job_id)} ｜ 数据标记：{escape(data_classification)} ｜ 本页面由QRA计算引擎自动生成</footer></body></html>"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def run_dynamic_flow(
    case: dict[str, Any],
    output_dir: Path,
    *,
    targets: Iterable[str] | None = None,
    generate_charts: bool = True,
    job_id: str = "DYNAMIC-QRA",
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = plan_dynamic_flow(case, targets)
    plan_path = _write_json(output_dir / "execution_plan.json", plan)
    results: dict[str, Any] = {}
    node_records: list[dict[str, Any]] = []
    generated_files: list[Path] = [plan_path]
    plan_by_id = {row["node_id"]: row for row in plan["plan"]}
    runtime_status: dict[str, str] = {}

    for node in NODE_REGISTRY:
        if node.node_id not in plan_by_id:
            continue
        planned = plan_by_id[node.node_id]
        if planned["status"] != "RUNNABLE":
            runtime_status[node.node_id] = "SKIPPED_MISSING_INPUT"
            node_records.append(
                {
                    **planned,
                    "status": "SKIPPED_MISSING_INPUT",
                    "result": None,
                    "charts": [],
                }
            )
            continue
        failed_dependencies = [
            dependency
            for dependency in node.dependencies
            if runtime_status.get(dependency) != "COMPLETED"
        ]
        if failed_dependencies:
            runtime_status[node.node_id] = "SKIPPED_DEPENDENCY_FAILED"
            node_records.append(
                {
                    **planned,
                    "status": "SKIPPED_DEPENDENCY_FAILED",
                    "blocked_dependencies": failed_dependencies,
                    "result": None,
                    "charts": [],
                }
            )
            continue
        try:
            result = node.execute(case, results)
            results[node.node_id] = result
            result_path = _write_json(output_dir / "nodes" / f"{node.node_id}.json", result)
            generated_files.append(result_path)
            chart_paths = (
                _render_node_charts(
                    node.node_id, result, results, output_dir / "charts"
                )
                if generate_charts
                else []
            )
            generated_files.extend(chart_paths)
            if node.node_id == "risk_matrix":
                matrix_paths = write_risk_matrix_files(result, output_dir / "derived")
                generated_files.extend(matrix_paths)
            runtime_status[node.node_id] = "COMPLETED"
            node_records.append(
                {
                    **planned,
                    "status": "COMPLETED",
                    "result": _relative(result_path, output_dir),
                    "charts": [_relative(path, output_dir) for path in chart_paths],
                }
            )
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            runtime_status[node.node_id] = "FAILED_ISOLATED"
            node_records.append(
                {
                    **planned,
                    "status": "FAILED_ISOLATED",
                    "runtime_error": str(exc),
                    "result": None,
                    "charts": [],
                }
            )

    completed = [row for row in node_records if row["status"] == "COMPLETED"]
    skipped = [row for row in node_records if row["status"].startswith("SKIPPED")]
    failed = [row for row in node_records if row["status"] == "FAILED_ISOLATED"]
    computational_completed = [
        row
        for row in completed
        if row["node_id"] not in {"data_inventory", "indicator_coverage"}
    ]
    status = "PASS"
    if skipped or failed:
        status = "PARTIAL" if completed else "BLOCK"
    if not computational_completed and (skipped or failed):
        status = "PARTIAL_DATA_ONLY"

    inventory = results.get("data_inventory", {})
    completed_node_ids = {row["node_id"] for row in completed}
    group_node_map: dict[str, set[str]] = {
        "geometry_material": {
            "segment_geometry",
            "failure_frequency",
            "leak_point_discretization",
            "aqt3046_source_term",
            "gbt34346_annex_c",
            "adaptive_evidence_qra",
            "human_qra",
        },
        "operation_medium": {
            "aqt3046_source_term",
            "gbt34346_annex_c",
            "adaptive_evidence_qra",
            "human_qra",
        },
        "valve_control": {"aqt3046_source_term", "human_qra"},
        "weather_terrain": {"human_qra"},
        "population_receptors": {"adaptive_evidence_qra", "human_qra"},
        "ignition_congestion": {"human_qra"},
        "consequence_parameters": {
            "aqt3046_source_term",
            "jet_fire_thresholds",
            "human_qra",
        },
        "external_corrosion": {"adaptive_evidence_qra"},
        "inspection_integrity": {"adaptive_evidence_qra"},
        "management_emergency": {"adaptive_evidence_qra"},
    }
    # 失效威胁指标只有被校准修正模型的项显式引用时，才算由频率算法直接消费。
    correction = case.get("frequency_correction_model", {})
    if correction.get("model_type") == "log_linear_calibrated":
        for model in correction.get("mechanisms", {}).values():
            for term in model.get("terms", []):
                indicator_id = str(term.get("indicator_id", ""))
                if "." in indicator_id:
                    group_node_map.setdefault(indicator_id.split(".", 1)[0], set()).add(
                        "failure_frequency"
                    )
    group_algorithm_usage = []
    for group in inventory.get("detected_indicator_groups", []):
        group_id = group["data_group_id"]
        registered_nodes = sorted(group_node_map.get(group_id, set()))
        completed_consumers = sorted(set(registered_nodes) & completed_node_ids)
        group_algorithm_usage.append(
            {
                "data_group_id": group_id,
                "name_zh": group["name_zh"],
                "registered_consumer_node_ids": registered_nodes,
                "completed_consumer_node_ids": completed_consumers,
                "directly_consumed": bool(completed_consumers),
            }
        )
    unmatched_groups = sorted(
        row["data_group_id"]
        for row in group_algorithm_usage
        if not row["registered_consumer_node_ids"]
    )
    recognized_not_consumed = sorted(
        row["data_group_id"]
        for row in group_algorithm_usage
        if row["registered_consumer_node_ids"] and not row["completed_consumer_node_ids"]
    )
    available_outputs = sorted(
        ["dynamic_dashboard_html"]
        + [output_id
        for node in NODE_REGISTRY
        if node.node_id in completed_node_ids
        for output_id in node.output_ids]
    )
    unavailable_outputs = [
        {
            "node_id": row["node_id"],
            "outputs": row["planned_outputs"],
            "reason": row["status"],
            "missing_inputs": row.get("missing_inputs", []),
            "blocked_dependencies": row.get("blocked_dependencies", []),
        }
        for row in node_records
        if row["status"] != "COMPLETED"
    ]
    missing_inputs = []
    seen_missing: set[tuple[str, str]] = set()
    for row in unavailable_outputs:
        for missing in row["missing_inputs"]:
            key = (missing["path"], missing["label_zh"])
            if key not in seen_missing:
                missing_inputs.append(missing)
                seen_missing.add(key)

    capability_report = {
        "schema_version": DYNAMIC_SCHEMA_VERSION,
        "status": status,
        "risk_result": {
            "available": "risk_matrix" in completed_node_ids,
            "source_node_id": (
                "human_qra"
                if "human_qra" in completed_node_ids
                else (
                    "adaptive_evidence_qra"
                    if "adaptive_evidence_qra" in completed_node_ids
                    else None
                )
            ),
            "result_tier": (
                "FULL_SPATIAL_HUMAN_QRA"
                if "human_qra" in completed_node_ids
                else (
                    "EVIDENCE_CONDITIONED_SCREENING_ESTIMATE"
                    if "adaptive_evidence_qra" in completed_node_ids
                    else None
                )
            ),
            "metric": "PLL_expected_fatalities_per_year",
            "formal_acceptance_judgement_allowed": bool(
                results.get("human_qra", {}).get("run", {}).get(
                    "formal_report_allowed", False
                )
            ),
        },
        "detected_data": inventory,
        "completed_node_ids": [row["node_id"] for row in completed],
        "skipped_node_ids": [row["node_id"] for row in skipped],
        "failed_node_ids": [row["node_id"] for row in failed],
        "available_outputs": available_outputs,
        "unavailable_outputs": unavailable_outputs,
        "missing_inputs": missing_inputs,
        "data_group_algorithm_usage": group_algorithm_usage,
        "detected_groups_without_registered_calculation": unmatched_groups,
        "detected_groups_with_algorithm_not_completed": recognized_not_consumed,
        "recommendation": (
            "只补充所需目标节点列出的缺失字段；无需为了运行已完成节点而补齐完整QRA输入。"
        ),
    }
    capability_path = _write_json(
        output_dir / "capability_report.json", capability_report
    )
    generated_files.append(capability_path)
    dashboard_path = _write_dynamic_dashboard(
        case,
        results,
        capability_report,
        node_records,
        output_dir / "report_dashboard.html",
        job_id=job_id,
    )
    generated_files.append(dashboard_path)

    manifest = {
        "schema_version": DYNAMIC_SCHEMA_VERSION,
        "job_id": job_id,
        "status": status,
        "selection_mode": plan["selection_mode"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "execution_plan": _relative(plan_path, output_dir),
        "capability_report": _relative(capability_path, output_dir),
        "dashboard": _relative(dashboard_path, output_dir),
        "nodes": node_records,
        "files": [
            {
                "path": _relative(path, output_dir),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in sorted(set(generated_files))
        ],
        "input_sha256": sha256_json(case),
        "numerical_result_sha256": sha256_numerical_result(
            {
                "schema_version": DYNAMIC_SCHEMA_VERSION,
                "node_results": {
                    node_id: results[node_id] for node_id in sorted(results)
                },
            }
        ),
        "numerical_result_hash_scope": (
            "canonical JSON of completed node result documents keyed by node_id; "
            "excludes job_id, timestamps, paths, embedded hashes and rendered artifacts; "
            "floats use 12 significant digits and result-record arrays use canonical ordering"
        ),
        "audit_manifest_sha256": None,
    }
    manifest["audit_manifest_sha256"] = sha256_json(manifest)
    manifest_path = _write_json(output_dir / "dynamic_manifest.json", manifest)
    manifest["manifest_path"] = str(manifest_path)
    manifest["output_directory"] = str(output_dir)
    return manifest


__all__ = [
    "DYNAMIC_SCHEMA_VERSION",
    "NODE_REGISTRY",
    "dynamic_node_catalog",
    "plan_dynamic_flow",
    "run_dynamic_flow",
]
