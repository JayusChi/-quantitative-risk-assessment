from __future__ import annotations

import argparse
import copy
import csv
import json
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from qra_engine.dynamic import DYNAMIC_SCHEMA_VERSION, run_dynamic_flow


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "workspace" / "inputs" / "江西省天然气"
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT
    / "workspace"
    / "outputs"
    / "江西省天然气_两处三级高后果区_失效概率虚拟试算_v1"
)

SOURCE_FILES = {
    "九江支线": SOURCE_ROOT / "江西省天然气_九江支线_现场资料变化点分段_QRA输入_v1.json",
    "芦溪支线": SOURCE_ROOT / "江西省天然气_芦溪支线_现有数据自适应QRA输入_v1.json",
}

OUTPUT_INPUT_FILES = {
    "九江支线": SOURCE_ROOT / "江西省天然气_九江支线_失效概率虚拟现场数据_v1.json",
    "芦溪支线": SOURCE_ROOT / "江西省天然气_芦溪支线_失效概率虚拟现场数据_v1.json",
}

BASE_FREQUENCY_LIBRARY = {
    "library_id": "JXNG-SYNTHETIC-FREQUENCY-LIBRARY-V1",
    "version": "1.0.0",
    "data_classification": "SYNTHETIC_TEST_ONLY",
    "unit": "per_km_year",
    "source": (
        "人工构造的数量级演示参数；只用于验证SY/T 6891.2频率计算、"
        "孔径守恒和Poisson概率换算，不代表EGIG、UKOPA或业主批准参数"
    ),
    "base_frequency_by_mechanism": {
        "external_corrosion": 1.20e-4,
        "internal_corrosion": 4.00e-5,
        "stress_corrosion_cracking": 3.00e-5,
        "manufacturing_construction": 2.00e-5,
        "third_party_damage": 1.50e-4,
        "natural_geohazard": 2.00e-5,
        "misoperation": 1.00e-5,
    },
    "loc_fraction_by_mechanism": {
        "external_corrosion": {
            "small_5mm": 0.60,
            "medium_25mm": 0.25,
            "large_100mm": 0.10,
            "rupture": 0.05,
        },
        "internal_corrosion": {
            "small_5mm": 0.65,
            "medium_25mm": 0.20,
            "large_100mm": 0.10,
            "rupture": 0.05,
        },
        "stress_corrosion_cracking": {
            "small_5mm": 0.10,
            "medium_25mm": 0.15,
            "large_100mm": 0.25,
            "rupture": 0.50,
        },
        "manufacturing_construction": {
            "small_5mm": 0.30,
            "medium_25mm": 0.25,
            "large_100mm": 0.20,
            "rupture": 0.25,
        },
        "third_party_damage": {
            "small_5mm": 0.35,
            "medium_25mm": 0.25,
            "large_100mm": 0.15,
            "rupture": 0.25,
        },
        "natural_geohazard": {
            "small_5mm": 0.15,
            "medium_25mm": 0.20,
            "large_100mm": 0.25,
            "rupture": 0.40,
        },
        "misoperation": {
            "small_5mm": 0.50,
            "medium_25mm": 0.25,
            "large_100mm": 0.15,
            "rupture": 0.10,
        },
    },
}

CORRECTION_MODEL = {
    "model_id": "frequency.correction.jiangxi.synthetic_log_linear.v1",
    "version": "1.0.0",
    "status": "SYNTHETIC_TEST_ONLY",
    "model_type": "log_linear_calibrated",
    "source": (
        "SY/T 6891.2-2020附录A的K_j接口；函数形式和系数为演示构造，"
        "未用业主事件数据校准、未获工程批准"
    ),
    "mechanisms": {
        "external_corrosion": {
            "intercept": -0.10,
            "minimum_factor": 0.20,
            "maximum_factor": 5.00,
            "terms": [
                {
                    "indicator_id": "external_corrosion.coating_defect_density_per_km",
                    "coefficient": 0.25,
                    "reference": 0.50,
                    "scale": 0.50,
                },
                {
                    "indicator_id": "external_corrosion.cp_off_potential_v",
                    "coefficient": 0.20,
                    "reference": -0.95,
                    "scale": 0.10,
                },
                {
                    "indicator_id": "external_corrosion.soil_resistivity_ohm_m",
                    "coefficient": -0.15,
                    "reference": 50.0,
                    "scale": 50.0,
                },
            ],
        },
        "internal_corrosion": {
            "intercept": -0.15,
            "minimum_factor": 0.20,
            "maximum_factor": 5.00,
            "terms": [
                {
                    "indicator_id": "internal_corrosion.free_water_probability",
                    "coefficient": 0.50,
                    "reference": 0.05,
                    "scale": 0.10,
                },
                {
                    "indicator_id": "internal_corrosion.coupon_corrosion_rate_mm_year",
                    "coefficient": 0.30,
                    "reference": 0.02,
                    "scale": 0.02,
                },
            ],
        },
        "stress_corrosion_cracking": {
            "intercept": -0.30,
            "minimum_factor": 0.20,
            "maximum_factor": 5.00,
            "terms": [
                {
                    "indicator_id": "inspection_integrity.crack_count",
                    "coefficient": 0.35,
                    "reference": 0.0,
                    "scale": 1.0,
                },
                {
                    "indicator_id": "inspection_integrity.unrepaired_critical_anomaly_count",
                    "coefficient": 0.50,
                    "reference": 0.0,
                    "scale": 1.0,
                },
            ],
        },
        "manufacturing_construction": {
            "intercept": -0.20,
            "minimum_factor": 0.20,
            "maximum_factor": 5.00,
            "terms": [
                {
                    "indicator_id": "construction.weld_reject_rate_fraction",
                    "coefficient": 0.30,
                    "reference": 0.01,
                    "scale": 0.02,
                },
                {
                    "indicator_id": "inspection_integrity.unrepaired_critical_anomaly_count",
                    "coefficient": 0.30,
                    "reference": 0.0,
                    "scale": 1.0,
                },
            ],
        },
        "third_party_damage": {
            "intercept": -0.10,
            "minimum_factor": 0.20,
            "maximum_factor": 5.00,
            "terms": [
                {
                    "indicator_id": "third_party.excavation_events_per_km_year",
                    "coefficient": 0.15,
                    "reference": 1.0,
                    "scale": 1.0,
                },
                {
                    "indicator_id": "third_party.burial_depth_m",
                    "coefficient": -0.25,
                    "reference": 1.20,
                    "scale": 0.40,
                },
                {
                    "indicator_id": "third_party.patrol_interval_days",
                    "coefficient": 0.12,
                    "reference": 3.0,
                    "scale": 2.0,
                },
            ],
        },
        "natural_geohazard": {
            "intercept": -0.20,
            "minimum_factor": 0.20,
            "maximum_factor": 5.00,
            "terms": [
                {
                    "indicator_id": "geohazard.landslide_susceptibility_index",
                    "coefficient": 0.60,
                    "reference": 0.10,
                    "scale": 0.20,
                },
                {
                    "indicator_id": "geohazard.maximum_scour_depth_m",
                    "coefficient": 0.25,
                    "reference": 0.10,
                    "scale": 0.50,
                },
            ],
        },
        "misoperation": {
            "intercept": -0.20,
            "minimum_factor": 0.20,
            "maximum_factor": 5.00,
            "terms": [
                {
                    "indicator_id": "management_emergency.procedure_compliance_fraction",
                    "coefficient": -0.50,
                    "reference": 0.95,
                    "scale": 0.05,
                },
                {
                    "indicator_id": "management_emergency.misoperation_event_count",
                    "coefficient": 0.50,
                    "reference": 0.0,
                    "scale": 1.0,
                },
            ],
        },
    },
}

VIRTUAL_PROFILES: dict[str, dict[str, float | int]] = {
    "station_controlled": {
        "external_corrosion.coating_defect_density_per_km": 0.30,
        "external_corrosion.cp_off_potential_v": -0.96,
        "external_corrosion.soil_resistivity_ohm_m": 70.0,
        "internal_corrosion.free_water_probability": 0.03,
        "internal_corrosion.coupon_corrosion_rate_mm_year": 0.015,
        "inspection_integrity.crack_count": 0,
        "inspection_integrity.unrepaired_critical_anomaly_count": 0,
        "construction.weld_reject_rate_fraction": 0.010,
        "third_party.excavation_events_per_km_year": 0.50,
        "third_party.burial_depth_m": 1.50,
        "third_party.patrol_interval_days": 1.0,
        "geohazard.landslide_susceptibility_index": 0.05,
        "geohazard.maximum_scour_depth_m": 0.00,
        "management_emergency.procedure_compliance_fraction": 0.98,
        "management_emergency.misoperation_event_count": 0,
    },
    "agricultural": {
        "external_corrosion.coating_defect_density_per_km": 0.50,
        "external_corrosion.cp_off_potential_v": -0.94,
        "external_corrosion.soil_resistivity_ohm_m": 65.0,
        "internal_corrosion.free_water_probability": 0.05,
        "internal_corrosion.coupon_corrosion_rate_mm_year": 0.020,
        "inspection_integrity.crack_count": 0,
        "inspection_integrity.unrepaired_critical_anomaly_count": 0,
        "construction.weld_reject_rate_fraction": 0.010,
        "third_party.excavation_events_per_km_year": 1.80,
        "third_party.burial_depth_m": 1.00,
        "third_party.patrol_interval_days": 3.0,
        "geohazard.landslide_susceptibility_index": 0.12,
        "geohazard.maximum_scour_depth_m": 0.20,
        "management_emergency.procedure_compliance_fraction": 0.96,
        "management_emergency.misoperation_event_count": 0,
    },
    "industrial": {
        "external_corrosion.coating_defect_density_per_km": 0.80,
        "external_corrosion.cp_off_potential_v": -0.91,
        "external_corrosion.soil_resistivity_ohm_m": 45.0,
        "internal_corrosion.free_water_probability": 0.08,
        "internal_corrosion.coupon_corrosion_rate_mm_year": 0.030,
        "inspection_integrity.crack_count": 0,
        "inspection_integrity.unrepaired_critical_anomaly_count": 0,
        "construction.weld_reject_rate_fraction": 0.015,
        "third_party.excavation_events_per_km_year": 3.00,
        "third_party.burial_depth_m": 1.20,
        "third_party.patrol_interval_days": 2.0,
        "geohazard.landslide_susceptibility_index": 0.10,
        "geohazard.maximum_scour_depth_m": 0.10,
        "management_emergency.procedure_compliance_fraction": 0.95,
        "management_emergency.misoperation_event_count": 0,
    },
    "dense_road": {
        "external_corrosion.coating_defect_density_per_km": 1.20,
        "external_corrosion.cp_off_potential_v": -0.88,
        "external_corrosion.soil_resistivity_ohm_m": 35.0,
        "internal_corrosion.free_water_probability": 0.08,
        "internal_corrosion.coupon_corrosion_rate_mm_year": 0.035,
        "inspection_integrity.crack_count": 1,
        "inspection_integrity.unrepaired_critical_anomaly_count": 0,
        "construction.weld_reject_rate_fraction": 0.020,
        "third_party.excavation_events_per_km_year": 5.00,
        "third_party.burial_depth_m": 1.00,
        "third_party.patrol_interval_days": 1.0,
        "geohazard.landslide_susceptibility_index": 0.10,
        "geohazard.maximum_scour_depth_m": 0.10,
        "management_emergency.procedure_compliance_fraction": 0.94,
        "management_emergency.misoperation_event_count": 0,
    },
    "rail_bridge": {
        "external_corrosion.coating_defect_density_per_km": 0.70,
        "external_corrosion.cp_off_potential_v": -0.92,
        "external_corrosion.soil_resistivity_ohm_m": 55.0,
        "internal_corrosion.free_water_probability": 0.05,
        "internal_corrosion.coupon_corrosion_rate_mm_year": 0.025,
        "inspection_integrity.crack_count": 1,
        "inspection_integrity.unrepaired_critical_anomaly_count": 0,
        "construction.weld_reject_rate_fraction": 0.020,
        "third_party.excavation_events_per_km_year": 2.50,
        "third_party.burial_depth_m": 1.40,
        "third_party.patrol_interval_days": 2.0,
        "geohazard.landslide_susceptibility_index": 0.20,
        "geohazard.maximum_scour_depth_m": 0.20,
        "management_emergency.procedure_compliance_fraction": 0.95,
        "management_emergency.misoperation_event_count": 0,
    },
    "waterway": {
        "external_corrosion.coating_defect_density_per_km": 1.00,
        "external_corrosion.cp_off_potential_v": -0.90,
        "external_corrosion.soil_resistivity_ohm_m": 30.0,
        "internal_corrosion.free_water_probability": 0.10,
        "internal_corrosion.coupon_corrosion_rate_mm_year": 0.040,
        "inspection_integrity.crack_count": 1,
        "inspection_integrity.unrepaired_critical_anomaly_count": 1,
        "construction.weld_reject_rate_fraction": 0.020,
        "third_party.excavation_events_per_km_year": 2.00,
        "third_party.burial_depth_m": 1.10,
        "third_party.patrol_interval_days": 3.0,
        "geohazard.landslide_susceptibility_index": 0.35,
        "geohazard.maximum_scour_depth_m": 1.00,
        "management_emergency.procedure_compliance_fraction": 0.93,
        "management_emergency.misoperation_event_count": 0,
    },
    "industrial_high_activity": {
        "external_corrosion.coating_defect_density_per_km": 1.50,
        "external_corrosion.cp_off_potential_v": -0.86,
        "external_corrosion.soil_resistivity_ohm_m": 30.0,
        "internal_corrosion.free_water_probability": 0.12,
        "internal_corrosion.coupon_corrosion_rate_mm_year": 0.050,
        "inspection_integrity.crack_count": 1,
        "inspection_integrity.unrepaired_critical_anomaly_count": 1,
        "construction.weld_reject_rate_fraction": 0.030,
        "third_party.excavation_events_per_km_year": 6.00,
        "third_party.burial_depth_m": 0.90,
        "third_party.patrol_interval_days": 2.0,
        "geohazard.landslide_susceptibility_index": 0.15,
        "geohazard.maximum_scour_depth_m": 0.20,
        "management_emergency.procedure_compliance_fraction": 0.92,
        "management_emergency.misoperation_event_count": 1,
    },
}

PROFILE_BY_SEGMENT = {
    "九江支线": {
        "JJ-SEG-01": "station_controlled",
        "JJ-SEG-02": "industrial",
        "JJ-SEG-03": "dense_road",
        "JJ-SEG-04": "dense_road",
        "JJ-SEG-05": "dense_road",
        "JJ-SEG-06": "industrial_high_activity",
        "JJ-SEG-07": "industrial",
        "JJ-SEG-08": "industrial_high_activity",
        "JJ-SEG-09": "industrial",
        "JJ-SEG-10": "rail_bridge",
        "JJ-SEG-11": "industrial_high_activity",
    },
    "芦溪支线": {
        "LX-SEG-01": "agricultural",
        "LX-SEG-02": "agricultural",
        "LX-SEG-03": "agricultural",
        "LX-SEG-04": "industrial",
        "LX-SEG-05": "industrial",
        "LX-SEG-06": "industrial_high_activity",
        "LX-SEG-07": "industrial",
        "LX-SEG-08": "industrial",
        "LX-SEG-09": "dense_road",
        "LX-SEG-10": "industrial_high_activity",
        "LX-SEG-11": "industrial",
        "LX-SEG-12": "industrial_high_activity",
        "LX-SEG-13": "industrial",
        "LX-SEG-14": "rail_bridge",
        "LX-SEG-15": "industrial_high_activity",
        "LX-SEG-16": "station_controlled",
    },
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _observation(value: float | int, line_name: str, segment_id: str) -> dict[str, Any]:
    return {
        "value": value,
        "quality": "D",
        "as_of": "2026-08-26",
        "source_ref": (
            f"synthetic://jiangxi-failure-probability-demo/{line_name}/{segment_id}"
        ),
        "review_status": "SYNTHETIC_NOT_FIELD_VERIFIED",
    }


def _profile_values(profile_id: str, segment_id: str) -> dict[str, float | int]:
    """Apply a small deterministic variation so equal-length demo segments remain distinct."""
    values = copy.deepcopy(VIRTUAL_PROFILES[profile_id])
    sequence = int(segment_id.rsplit("-", 1)[-1])
    values["third_party.excavation_events_per_km_year"] = round(
        float(values["third_party.excavation_events_per_km_year"])
        + 0.07 * (sequence % 5),
        3,
    )
    values["external_corrosion.coating_defect_density_per_km"] = round(
        float(values["external_corrosion.coating_defect_density_per_km"])
        + 0.02 * (sequence % 4),
        3,
    )
    return values


def build_case(line_name: str) -> dict[str, Any]:
    source_path = SOURCE_FILES[line_name]
    if not source_path.is_file():
        raise FileNotFoundError(f"源案例不存在：{source_path}")
    case = copy.deepcopy(_read_json(source_path))
    metadata = case.setdefault("metadata", {})
    metadata["case_id"] = f"{metadata['case_id']}-SYNTHETIC-FAILURE-PROBABILITY-V1"
    metadata["model_id"] = "pipeline.syt6891.2.frequency.dynamic.v1"
    metadata["data_classification"] = (
        "REAL_PROJECT_CONTEXT_WITH_SYNTHETIC_FAILURE_INPUTS_TEST_ONLY"
    )
    metadata["formal_qra_allowed"] = False
    metadata["warning"] = (
        "线路/HCA上下文继承现有资料；失效机理现场指标、基准频率、"
        "孔径比例和K_j校准系数全部为人工构造，仅用于软件联调和培训，"
        "不得用于真实资产风险判断、监管报送或正式报告。"
    )

    assessment = case.setdefault("assessment", {})
    assessment["failure_probability_horizon_years"] = 1.0
    case["frequency_library"] = copy.deepcopy(BASE_FREQUENCY_LIBRARY)
    case["frequency_correction_model"] = copy.deepcopy(CORRECTION_MODEL)

    indicators = case.setdefault("engineering_indicators", {})
    indicators.update(
        {
            "catalog_id": "qra.pipeline.engineering_indicators.v1",
            "catalog_version": "1.0.0",
            "data_classification": "SYNTHETIC_TEST_ONLY",
            "description": (
                "根据投标技术方案第11章、附录I.3和附录J空白/待填栏目构造的"
                "现场数据测试集；仅用于打通失效频率和失效概率节点"
            ),
        }
    )
    by_segment = indicators.setdefault("observations_by_segment", {})
    profile_map = PROFILE_BY_SEGMENT[line_name]
    actual_segment_ids = {str(row["segment_id"]) for row in case["segments"]}
    if actual_segment_ids != set(profile_map):
        raise ValueError(
            f"{line_name}管段与虚拟剖面映射不一致："
            f"缺少{sorted(actual_segment_ids - set(profile_map))}，"
            f"多余{sorted(set(profile_map) - actual_segment_ids)}"
        )
    for segment_id, profile_id in profile_map.items():
        by_segment[segment_id] = {
            indicator_id: _observation(value, line_name, segment_id)
            for indicator_id, value in _profile_values(profile_id, segment_id).items()
        }

    case["synthetic_failure_probability_input_register"] = {
        "status": "SYNTHETIC_TEST_ONLY",
        "source_document_sections": [
            "第11章 失效频率定量计算方案",
            "附录I.3 G2模型与场景检查表（证据/版本、责任/关闭空白栏）",
            "附录J 结构化数据模型与字段合同",
            "附录K K02-K07核心公式",
        ],
        "virtualized_input_groups": [
            "管段与里程",
            "外腐蚀/阴极保护",
            "内腐蚀",
            "裂纹与未修复缺陷",
            "制造施工质量",
            "第三方开挖/埋深/巡护",
            "地灾/冲刷",
            "管理与误操作",
            "分机理基准频率",
            "孔径条件分布",
            "K_j修正函数",
        ],
        "profile_by_segment": profile_map,
        "field_verification_required": True,
    }
    return case


def _segment_mechanism_frequency(result: dict[str, Any]) -> dict[str, dict[str, float]]:
    totals: dict[str, defaultdict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in result["loc_frequency"]:
        segment_id = str(row["segment_id"])
        for mechanism, value in row["mechanism_contribution"].items():
            totals[segment_id][mechanism] += float(value)
    return {segment_id: dict(values) for segment_id, values in totals.items()}


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run(output_root: Path) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    summary_cases = []
    result_rows: list[dict[str, Any]] = []
    field_rows: list[dict[str, Any]] = []

    for line_name in SOURCE_FILES:
        case = build_case(line_name)
        input_path = OUTPUT_INPUT_FILES[line_name]
        _write_json(input_path, case)
        case_output = output_root / line_name
        manifest = run_dynamic_flow(
            case,
            case_output,
            targets=["failure_frequency"],
            generate_charts=True,
            job_id=f"JXNG-{line_name}-SYNTHETIC-FAILURE-PROBABILITY-V1",
        )
        frequency_path = case_output / "nodes" / "failure_frequency.json"
        frequency_result = _read_json(frequency_path)
        if manifest["status"] != "PASS":
            raise AssertionError(f"{line_name}失效概率试算未通过：{manifest['status']}")

        correction = frequency_result["frequency_correction_model"]
        if correction["approved_for_formal_qra"]:
            raise AssertionError("合成K_j不得通过正式QRA门禁")
        segment_by_id = {str(row["segment_id"]): row for row in case["segments"]}
        mechanisms = _segment_mechanism_frequency(frequency_result)
        ranking = frequency_result["segment_ranking"]
        for ranked in ranking:
            segment_id = str(ranked["segment_id"])
            segment = segment_by_id[segment_id]
            mechanism_values = mechanisms[segment_id]
            dominant_mechanism = max(
                mechanism_values.items(),
                key=lambda item: (item[1], item[0]),
            )[0]
            result_rows.append(
                {
                    "线路": line_name,
                    "管段": segment_id,
                    "虚拟现场剖面": PROFILE_BY_SEGMENT[line_name][segment_id],
                    "起点_km": segment["start_km"],
                    "终点_km": segment["end_km"],
                    "长度_km": segment["length_km"],
                    "年失效频率_1每年": ranked["annual_frequency"],
                    "一年内至少一次失效概率": ranked[
                        "failure_probability_over_horizon"
                    ],
                    "主导失效机理": dominant_mechanism,
                    "主导机理频率_1每年": mechanism_values[dominant_mechanism],
                    "结果用途": "SYNTHETIC_TEST_ONLY",
                }
            )
            observation_values = {
                indicator_id: observation["value"]
                for indicator_id, observation in case["engineering_indicators"][
                    "observations_by_segment"
                ][segment_id].items()
            }
            field_rows.append(
                {
                    "线路": line_name,
                    "管段": segment_id,
                    "虚拟现场剖面": PROFILE_BY_SEGMENT[line_name][segment_id],
                    **observation_values,
                    "数据质量": "D",
                    "复核状态": "SYNTHETIC_NOT_FIELD_VERIFIED",
                }
            )

        summary_cases.append(
            {
                "line_name": line_name,
                "input_file": str(input_path),
                "output_directory": str(case_output),
                "segment_count": len(case["segments"]),
                "total_length_km": sum(float(row["length_km"]) for row in case["segments"]),
                "total_initiating_failure_frequency_per_year": frequency_result[
                    "total_initiating_frequency_per_year"
                ],
                "at_least_one_failure_probability_in_one_year": frequency_result[
                    "at_least_one_failure_probability_over_horizon"
                ],
                "top_three_segments": ranking[:3],
                "frequency_conservation_error": abs(
                    frequency_result["total_initiating_frequency_per_year"]
                    - sum(frequency_result["frequency_by_segment_per_year"].values())
                ),
                "formal_qra_allowed": False,
                "formal_release_blockers": correction["formal_release_blockers"],
                "numerical_result_sha256": manifest["numerical_result_sha256"],
            }
        )

    result_fields = [
        "线路",
        "管段",
        "虚拟现场剖面",
        "起点_km",
        "终点_km",
        "长度_km",
        "年失效频率_1每年",
        "一年内至少一次失效概率",
        "主导失效机理",
        "主导机理频率_1每年",
        "结果用途",
    ]
    _write_csv(output_root / "失效概率评价汇总.csv", result_rows, result_fields)
    field_fields = [
        "线路",
        "管段",
        "虚拟现场剖面",
        *next(iter(VIRTUAL_PROFILES.values())).keys(),
        "数据质量",
        "复核状态",
    ]
    _write_csv(output_root / "虚拟现场数据清单.csv", field_rows, field_fields)

    total_frequency = sum(
        float(row["total_initiating_failure_frequency_per_year"])
        for row in summary_cases
    )
    summary = {
        "schema_version": "1.0.0",
        "dynamic_schema_version": DYNAMIC_SCHEMA_VERSION,
        "generated_on": date.today().isoformat(),
        "status": "PASSED_SYNTHETIC_FAILURE_PROBABILITY_DEMO",
        "scope": "江西省天然气九江支线与芦溪支线三级高后果区失效概率模块联调",
        "method": {
            "frequency": "lambda_seg,m,k=lambda_base,m,k*L_seg*K_j",
            "probability": "P(N>=1)=1-exp(-lambda*t)",
            "probability_horizon_years": 1.0,
            "frequency_is_additive_probability_is_not": True,
        },
        "data_classification": "SYNTHETIC_TEST_ONLY",
        "formal_qra_allowed": False,
        "warning": (
            "合并概率只表示把两条独立演示管线视为一个Poisson事件集合的数学结果；"
            "正式评价必须分别使用批准的频率库、项目K_j和现场复核数据。"
        ),
        "cases": summary_cases,
        "combined_demo_frequency_per_year": total_frequency,
        "combined_demo_at_least_one_failure_probability_in_one_year": (
            -math.expm1(-total_frequency)
        ),
        "deliverables": {
            "segment_result_csv": "失效概率评价汇总.csv",
            "synthetic_field_csv": "虚拟现场数据清单.csv",
            "per_line_dynamic_outputs": [row["output_directory"] for row in summary_cases],
        },
    }
    _write_json(output_root / "试算总览.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="构造江西两处三级高后果区失效概率虚拟现场数据并运行动态频率节点"
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
    )
    args = parser.parse_args()
    summary = run(args.output_root.resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
