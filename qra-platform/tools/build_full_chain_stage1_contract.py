"""Build and validate the M1.5 stage-1 field/source/node contract.

The stage-1 contract deliberately combines three views that were previously
separate: the published Part 1 field dictionary, explicit qra-input JSON Schema
properties, and reviewed runtime inputs used by the eleven dynamic nodes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from qra_engine.dynamic import dynamic_node_catalog  # noqa: E402

CONTRACT_ROOT = PROJECT_ROOT / "resources" / "contracts" / "part1" / "v1"
MAPPING_ROOT = PROJECT_ROOT / "resources" / "mappings"
OUTPUT_ROOT = PROJECT_ROOT / "resources" / "synthetic" / "full-chain-v1"
FIELD_DICTIONARY_PATH = CONTRACT_ROOT / "field_dictionary.json"
QRA_INPUT_SCHEMA_PATH = CONTRACT_ROOT / "schemas" / "qra-input.schema.json"

MATRIX_COLUMNS = (
    "field_id",
    "business_name",
    "target_path",
    "data_layer",
    "source_document",
    "source_location_type",
    "source_unit",
    "target_unit",
    "criticality",
    "extraction_method",
    "target_nodes",
    "missing_policy",
    "conflict_policy",
    "evidence_required",
    "value_type",
    "cardinality",
    "contract_sources",
    "node_requirement",
    "default_or_prior",
    "mapping_coverage",
    "extraction_coverage",
    "assembly_coverage",
    "coverage_gap",
    "notes",
)

DATA_LAYERS = {"PROJECT_FACT", "MODEL_PARAMETER", "RUN_ASSUMPTION"}
CRITICALITIES = {"BLOCKING", "IMPORTANT", "OPTIONAL"}
RAW_DOCUMENT_SUFFIXES = {".xlsx", ".csv", ".docx", ".pdf", ".png", ".jpg", ".jpeg"}

SOURCE_DOCUMENTS = {
    "PIPELINE": "01_管线与管段台账.xlsx",
    "OPERATION": "02_运行工况.csv",
    "GAS": "03_天然气组分.xlsx",
    "CORROSION": "04_CIPS与腐蚀检测.xlsx",
    "DEFECT": "05_缺陷与维修记录.docx",
    "THIRD_PARTY": "06_第三方与巡线记录.xlsx",
    "POPULATION": "07_人口和敏感受体.xlsx",
    "WEATHER": "08_气象联合概率.csv",
    "SCAN": "09_现场检查扫描件.pdf",
    "PHOTO": "10_现场照片说明.png",
}

GROUP_SOURCE = {
    "governance": "SCAN",
    "geometry_material": "PIPELINE",
    "construction": "SCAN",
    "operation_medium": "OPERATION",
    "valve_control": "OPERATION",
    "external_corrosion": "CORROSION",
    "internal_corrosion": "CORROSION",
    "scc_fatigue": "DEFECT",
    "manufacturing_defects": "DEFECT",
    "third_party": "THIRD_PARTY",
    "geohazard": "THIRD_PARTY",
    "inspection_integrity": "DEFECT",
    "weather_terrain": "WEATHER",
    "population_receptors": "POPULATION",
    "ignition_congestion": "PHOTO",
    "management_emergency": "SCAN",
}

GROUP_RAW_BRIDGE = {
    "external_corrosion",
    "inspection_integrity",
    "management_emergency",
    "population_receptors",
    "third_party",
    "valve_control",
}

MODEL_PACK_BY_PREFIX = {
    "frequency_library": "synthetic-frequency-library-v1",
    "frequency_correction_model": "synthetic-frequency-correction-v1",
    "segment_correction_factor": "synthetic-frequency-correction-v1",
    "ignition_model": "synthetic-ignition-model-v1",
    "standard_formula_test_parameters": "synthetic-consequence-parameters-v1",
    "risk_matrix_criteria": "synthetic-risk-criteria-v1",
    "damage_model": "synthetic-consequence-parameters-v1",
    "mock_adapter_output": "synthetic-model-validation-fixture-v1",
    "expected_aggregation": "synthetic-model-validation-fixture-v1",
    "validation_expectations": "synthetic-model-validation-fixture-v1",
    "system_parameters": "synthetic-consequence-parameters-v1",
}
VERSIONED_PARAMETER_PACKS = set(MODEL_PACK_BY_PREFIX.values())

RUN_ASSUMPTION_PREFIXES = (
    "schema_version",
    "metadata",
    "assessment",
    "extensions",
    "data_category_manifest",
)

RUNTIME_FIELD_DEFINITIONS: dict[str, tuple[str, str, str | None]] = {
    "assessment.reference_height_m": ("受体参考高度", "number", "m"),
    "assessment.failure_probability_horizon_years": ("失效概率时间跨度", "number", "year"),
    "assessment.convergence": ("收敛判据", "object", None),
    "assessment.judgement_status_by_domain": ("分域判定状态", "object", None),
    "engineering_indicators.data_classification": ("工程指标数据分类", "enum", None),
    "engineering_indicators.description": ("工程指标说明", "string", None),
    "engineering_indicators.observations_global": ("全局工程指标观测", "object", None),
    "engineering_indicators.segment_archetype": ("管段原型", "object", None),
    "engineering_indicators.observations_by_archetype": ("按原型工程指标观测", "object", None),
    "engineering_indicators.observations_by_segment": ("逐段工程指标观测", "object", None),
    "frequency_library.library_id": ("失效频率库编号", "string", None),
    "frequency_library.version": ("失效频率库版本", "string", None),
    "frequency_library.data_classification": ("失效频率库数据分类", "enum", None),
    "frequency_correction_model.model_id": ("频率修正模型编号", "string", None),
    "frequency_correction_model.version": ("频率修正模型版本", "string", None),
    "frequency_correction_model.status": ("频率修正模型状态", "enum", None),
    "frequency_correction_model.model_type": ("频率修正模型类型", "enum", None),
    "frequency_correction_model.source": ("频率修正模型来源", "string", None),
    "ignition_model.model_status": ("点火模型状态", "enum", None),
    "ignition_model.immediate_ignition_probability": ("立即点火概率", "object", None),
    "ignition_model.delayed_ignition_test_probability": ("延迟点火测试概率", "object", None),
    "ignition_model.vce_given_delayed_test_probability": ("延迟点火后VCE测试概率", "object", None),
    "risk_matrix_criteria": ("风险矩阵展示准则", "object", None),
    "system_parameters.adaptive_evidence_qra": ("自适应证据风险模型参数", "object", None),
}

AQT_PARAMETER_FIELDS = {
    "data_classification": ("后果参数数据分类", "enum", None),
    "operating_pressure_basis": ("运行压力基准", "enum", None),
    "ambient_pressure_pa_abs": ("环境绝压", "number", "Pa_abs"),
    "ambient_temperature_k": ("环境温度", "number", "K"),
    "molar_mass_kg_mol": ("摩尔质量", "number", "kg/mol"),
    "gamma": ("绝热指数", "number", None),
    "gas_discharge_coefficient": ("气体泄放系数", "number", None),
    "heat_of_combustion_j_kg": ("燃烧热", "number", "J/kg"),
    "radiative_fraction": ("辐射分数", "number", "fraction"),
    "lfl_volume_fraction": ("爆炸下限体积分数", "number", "fraction"),
    "effective_release_height_m": ("有效释放高度", "number", "m"),
    "thermal_exposure_time_s": ("热暴露时间", "number", "s"),
    "minimum_effect_distance_m": ("最小效应距离", "number", "m"),
    "pipe_absolute_roughness_mm": ("管壁绝对粗糙度", "number", "mm"),
    "minimum_rupture_flow_length_m": ("破裂流最小等效长度", "number", "m"),
    "jet_direction_mode": ("喷射火方向模式", "enum", None),
    "ignition_model": ("物理链点火模型参数", "object", None),
    "segments": ("物理链逐段参数", "object", None),
    "segments.*.terrain": ("逐段地形类型", "enum", None),
    "segments.*.explosion_source_volume_m3": ("逐段爆炸源体积", "number", "m3"),
    "segments.*.tno_source_strength": ("逐段TNO源强", "integer", None),
}

GBT_PARAMETER_FIELDS = {
    "data_classification": ("附录C参数数据分类", "enum", None),
    "operating_pressure_basis": ("附录C运行压力基准", "enum", None),
    "ambient_pressure_pa_abs": ("附录C环境绝压", "number", "Pa_abs"),
    "molar_mass_kg_mol": ("附录C摩尔质量", "number", "kg/mol"),
    "gamma": ("附录C绝热指数", "number", None),
    "gas_discharge_coefficient_c13": ("附录C气体泄放系数C.13", "number", None),
    "heat_of_combustion_j_kg": ("附录C燃烧热", "number", "J/kg"),
    "radiative_fraction": ("附录C辐射分数", "number", "fraction"),
    "fatal_heat_flux_threshold_kw_m2": ("附录C致死热通量阈值", "number", "kW/m2"),
    "segments": ("附录C逐段参数", "object", None),
    "segments.*.management_scores_c3_to_c8": ("逐段管理评分C.3-C.8", "object", None),
    "segments.*.damage_component_factors": ("逐段损伤分量因子", "object", None),
    "segments.*.damage_component_weights": ("逐段损伤分量权重", "object", None),
    "segments.*.point_ignition_probability": ("逐段点火概率", "number", "fraction"),
    "segments.*.population_density_per_m2": ("逐段人口密度", "number", "person/m2"),
}

for key, value in AQT_PARAMETER_FIELDS.items():
    RUNTIME_FIELD_DEFINITIONS[f"standard_formula_test_parameters.aqt3046_physical_chain.{key}"] = (
        value
    )
for key, value in GBT_PARAMETER_FIELDS.items():
    RUNTIME_FIELD_DEFINITIONS[f"standard_formula_test_parameters.gbt34346_annex_c.{key}"] = value


def _spec(paths: tuple[str, ...], kind: str, note: str) -> list[tuple[str, str, str]]:
    return [(path, kind, note) for path in paths]


HUMAN_REQUIRED = (
    "metadata.case_id",
    "metadata.model_id",
    "metadata.data_classification",
    "assessment.assessment_id",
    "assessment.reference_height_m",
    "assessment.leak_point_initial_spacing_m",
    "pipeline.design_pressure_mpa",
    "pipeline.operating_pressure_mpa",
    "pipeline.operating_temperature_k",
    "pipeline.gas_composition_mole_fraction",
    "segments.*.segment_id",
    "segments.*.start_km",
    "segments.*.end_km",
    "segments.*.length_km",
    "segments.*.start_xy_m",
    "segments.*.end_xy_m",
    "segments.*.outside_diameter_mm",
    "segments.*.wall_thickness_mm",
    "segments.*.upstream_valve_km",
    "segments.*.downstream_valve_km",
    "segments.*.area_activity",
    "frequency_library.library_id",
    "frequency_library.version",
    "frequency_library.unit",
    "frequency_library.base_frequency_by_mechanism",
    "frequency_library.loc_fraction_by_mechanism",
    "frequency_correction_model",
    "segment_correction_factor",
    "weather_joint_probability.*.weather_id",
    "weather_joint_probability.*.time_period",
    "weather_joint_probability.*.stability_class",
    "weather_joint_probability.*.wind_speed_m_s",
    "weather_joint_probability.*.wind_direction_from",
    "weather_joint_probability.*.probability",
    "population_cells.*.cell_id",
    "population_cells.*.xy_m",
    "population_cells.*.population_day",
    "population_cells.*.population_night",
    "ignition_model.immediate_ignition_probability",
    *tuple(
        f"standard_formula_test_parameters.aqt3046_physical_chain.{key}"
        for key in AQT_PARAMETER_FIELDS
    ),
)

GBT_REQUIRED = (
    *tuple(
        f"standard_formula_test_parameters.gbt34346_annex_c.{key}" for key in GBT_PARAMETER_FIELDS
    ),
)

NODE_CURATED_INPUTS: dict[str, list[tuple[str, str, str]]] = {
    "data_inventory": _spec(
        ("data_category_manifest", "raw_data_categories", "engineering_indicators.catalog_id"),
        "OPTIONAL",
        "盘点资料类别和已识别字段。",
    ),
    "indicator_coverage": _spec(
        (
            "engineering_indicators.catalog_id",
            "engineering_indicators.catalog_version",
            "engineering_indicators.observations_global",
            "engineering_indicators.observations_by_archetype",
            "engineering_indicators.observations_by_segment",
        ),
        "OPTIONAL",
        "无观测时节点仍运行并报告零覆盖。",
    ),
    "segment_geometry": [],
    "failure_frequency": [
        ("frequency_library.unit", "RUNTIME_REQUIRED", "首版只接受per_km_year。"),
        ("frequency_correction_model", "RUNTIME_REQUIRED_FOR_S00", "S00使用provided_factors模型。"),
        ("segment_correction_factor", "RUNTIME_REQUIRED_FOR_S00", "S00逐段分机理修正因子。"),
        (
            "assessment.failure_probability_horizon_years",
            "RUN_ASSUMPTION_REQUIRED_FOR_M1_5",
            "当前代码默认1年；M1.5必须显式写入。",
        ),
    ],
    "leak_point_discretization": _spec(
        ("segments.*.segment_id", "segments.*.start_km", "segments.*.end_km"),
        "UPSTREAM_REQUIRED",
        "泄漏点标识和里程插值依赖管段几何。",
    ),
    "aqt3046_source_term": _spec(
        (
            "segments.*.segment_id",
            "segments.*.start_km",
            "segments.*.end_km",
            "pipeline.gas_composition_mole_fraction",
            "standard_formula_test_parameters.aqt3046_physical_chain.operating_pressure_basis",
            "standard_formula_test_parameters.aqt3046_physical_chain.minimum_rupture_flow_length_m",
        ),
        "RUNTIME_REQUIRED_FOR_S00",
        "源项运行时直接读取或作为合成物性权威来源。",
    ),
    "jet_fire_thresholds": [],
    "gbt34346_annex_c": _spec(
        GBT_REQUIRED,
        "PREFLIGHT_REQUIRED",
        "附录C实现直接读取版本化合成参数包。",
    ),
    "adaptive_evidence_qra": [
        (
            "system_parameters.adaptive_evidence_qra",
            "MODEL_SPEC_REQUIRED",
            "版本化先验和证据更新模型。",
        ),
        ("population_cells.*.xy_m", "OPTIONAL", "缺失时使用并披露版本化人口密度先验。"),
        ("population_cells.*.population_day", "OPTIONAL", "缺失时使用并披露版本化人口密度先验。"),
        ("population_cells.*.population_night", "OPTIONAL", "缺失时使用并披露版本化人口密度先验。"),
    ],
    "human_qra": _spec(
        HUMAN_REQUIRED,
        "PREFLIGHT_OR_RUNTIME_REQUIRED",
        "完整空间人员QRA校验或运行时直接读取。",
    )
    + _spec(
        ("population_cells.*.outdoor_fraction_day", "population_cells.*.outdoor_fraction_night"),
        "OPTIONAL_WITH_EXPLICIT_PRIOR",
        "缺失时当前实现使用1.0；M1.5参数包必须登记该先验。",
    ),
    "risk_matrix": [("risk_matrix_criteria", "OPTIONAL", "缺失时采用版本化展示矩阵准则。")],
}

DEFAULTS_AND_PRIORS = {
    "assessment.failure_probability_horizon_years": (
        "CURRENT_CODE_DEFAULT=1.0 year; M1.5_RUN_ASSUMPTION_MUST_BE_EXPLICIT"
    ),
    "population_cells.*.outdoor_fraction_day": (
        "CURRENT_CODE_DEFAULT=1.0; REGISTERED_IN_SYNTHETIC_CONSEQUENCE_PARAMETERS_V1"
    ),
    "population_cells.*.outdoor_fraction_night": (
        "CURRENT_CODE_DEFAULT=1.0; REGISTERED_IN_SYNTHETIC_CONSEQUENCE_PARAMETERS_V1"
    ),
    "standard_formula_test_parameters.aqt3046_physical_chain.operating_pressure_basis": (
        "CURRENT_CODE_DEFAULT=gauge; PARAMETER_PACK_MUST_BE_EXPLICIT"
    ),
    "standard_formula_test_parameters.aqt3046_physical_chain.ambient_temperature_k": (
        "FALLBACK=pipeline.operating_temperature_k; PARAMETER_PACK_VALUE_PREFERRED"
    ),
    "standard_formula_test_parameters.aqt3046_physical_chain.minimum_effect_distance_m": (
        "CURRENT_CODE_DEFAULT=1.0 m; PARAMETER_PACK_MUST_BE_EXPLICIT"
    ),
    "standard_formula_test_parameters.aqt3046_physical_chain.minimum_rupture_flow_length_m": (
        "CURRENT_CODE_DEFAULT=1.0 m; PARAMETER_PACK_MUST_BE_EXPLICIT"
    ),
    "risk_matrix_criteria": "VERSIONED_SYSTEM_CRITERIA_PRIOR",
    "system_parameters.adaptive_evidence_qra": "VERSIONED_MODEL_SPEC_PRIOR",
}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON顶层必须是对象：{path}")
    return value


def _field_id_from_path(path: str) -> str:
    prefixes = {
        "segments.*.": "segment.",
        "weather_joint_probability.*.": "weather_case.",
        "population_cells.*.": "population_cell.",
    }
    for prefix, replacement in prefixes.items():
        if path.startswith(prefix):
            return replacement + path.removeprefix(prefix)
    return path.replace(".*.", ".item.").replace("*", "item")


def _schema_type(definition: dict[str, Any]) -> str:
    value = definition.get("type", "object")
    if isinstance(value, list):
        useful = [item for item in value if item != "null"]
        return str(useful[0] if len(useful) == 1 else "any")
    return str(value)


def _explicit_schema_fields(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    structural_containers = {"system_parameters"}

    def walk(definition: dict[str, Any], path: str) -> None:
        properties = definition.get("properties")
        items = definition.get("items")
        item_properties = items.get("properties") if isinstance(items, dict) else None
        if isinstance(properties, dict) and properties:
            for key, child in properties.items():
                child_path = f"{path}.{key}" if path else key
                walk(child, child_path)
            return
        if isinstance(item_properties, dict) and item_properties:
            for key, child in item_properties.items():
                walk(child, f"{path}.*.{key}")
            return
        if path in structural_containers:
            return
        result[path] = {
            "value_type": _schema_type(definition),
            "cardinality": "MANY" if "*" in path else "OPTIONAL_ONE",
        }

    for key, definition in schema.get("properties", {}).items():
        walk(definition, key)
    return result


def _mapping_targets() -> set[str]:
    result: set[str] = set()
    for path in sorted(MAPPING_ROOT.rglob("*.json")):
        profile = _read_json(path)
        for table in profile.get("tables", []):
            target = str(table["target"])
            for field in table.get("fields", []):
                name = str(field["target"])
                if target == "segments":
                    result.add(f"segments.*.{name}")
                elif target == "pipeline":
                    result.add(f"pipeline.{name}")
                elif target == "population_cells":
                    if name in {"x_m", "y_m"}:
                        result.add("population_cells.*.xy_m")
                    else:
                        result.add(f"population_cells.*.{name}")
                else:
                    result.add(f"{target}.*.{name}")
    return result


def _parameter_pack(path: str, indicator_group: str | None) -> str:
    if indicator_group == "consequence_parameters":
        return "synthetic-consequence-parameters-v1"
    prefix = next(
        (key for key in MODEL_PACK_BY_PREFIX if path == key or path.startswith(f"{key}.")), None
    )
    if prefix is None:
        return "synthetic-consequence-parameters-v1"
    return MODEL_PACK_BY_PREFIX[prefix]


def _data_layer(path: str, indicator_group: str | None) -> str:
    if indicator_group == "consequence_parameters":
        return "MODEL_PARAMETER"
    if any(path == key or path.startswith(f"{key}.") for key in MODEL_PACK_BY_PREFIX):
        return "MODEL_PARAMETER"
    if path.startswith(RUN_ASSUMPTION_PREFIXES):
        if path == "assessment.coordinate_system":
            return "PROJECT_FACT"
        return "RUN_ASSUMPTION"
    if path.startswith("engineering_indicators.") and path.split(".", 2)[-1] in {
        "catalog_id",
        "catalog_version",
        "data_classification",
        "description",
        "segment_archetype",
    }:
        return "RUN_ASSUMPTION"
    return "PROJECT_FACT"


def _project_source(path: str, indicator_group: str | None) -> str:
    if indicator_group in GROUP_SOURCE:
        return SOURCE_DOCUMENTS[GROUP_SOURCE[indicator_group]]
    if path.startswith("segments") or path.startswith("pipeline"):
        if "gas_composition" in path:
            return SOURCE_DOCUMENTS["GAS"]
        if any(token in path for token in ("operating_", "design_pressure", "service")):
            return SOURCE_DOCUMENTS["OPERATION"]
        return SOURCE_DOCUMENTS["PIPELINE"]
    if path.startswith("weather_joint_probability"):
        return SOURCE_DOCUMENTS["WEATHER"]
    if path.startswith("population_cells"):
        return SOURCE_DOCUMENTS["POPULATION"]
    if path.startswith("raw_data_categories"):
        return "|".join(SOURCE_DOCUMENTS.values())
    if path == "assessment.coordinate_system":
        return SOURCE_DOCUMENTS["PIPELINE"]
    return SOURCE_DOCUMENTS["SCAN"]


def _source_for(path: str, layer: str, indicator_group: str | None) -> str:
    if layer == "PROJECT_FACT":
        return _project_source(path, indicator_group)
    if layer == "MODEL_PARAMETER":
        return f"parameter-pack:{_parameter_pack(path, indicator_group)}"
    if path == "data_category_manifest":
        return "system-generated:source-pack-manifest-v1"
    if path.startswith("metadata"):
        return "project-wizard:S00_BASELINE"
    return "run-assumption:S00_BASELINE-v1"


def _source_location(source: str) -> str:
    if source.startswith(
        ("parameter-pack:", "system-generated:", "run-assumption:", "project-wizard:")
    ):
        return "system"
    suffixes = {Path(item).suffix.casefold() for item in source.split("|")}
    if suffixes <= {".xlsx", ".csv"}:
        return "cell"
    if suffixes == {".docx"}:
        return "paragraph"
    if suffixes == {".pdf"}:
        return "page/bbox"
    if suffixes <= {".png", ".jpg", ".jpeg"}:
        return "bbox"
    return "mixed"


def _extraction_method(source: str) -> str:
    location = _source_location(source)
    return {
        "cell": "deterministic",
        "paragraph": "llm",
        "page/bbox": "ocr",
        "bbox": "ocr",
        "mixed": "deterministic/ocr/llm",
        "system": "system",
    }[location]


def _unit_from_path(path: str) -> str | None:
    suffixes = (
        ("_kg_mol", "kg/mol"),
        ("_j_kg", "J/kg"),
        ("_kw_m2", "kW/m2"),
        ("_pa_abs", "Pa_abs"),
        ("_per_km_year", "1/(km·year)"),
        ("_per_year", "1/year"),
        ("_mm_year", "mm/year"),
        ("_m_year", "m/year"),
        ("_m_s", "m/s"),
        ("_mpa", "MPa"),
        ("_km", "km"),
        ("_mm", "mm"),
        ("_m3", "m3"),
        ("_m2", "m2"),
        ("_m", "m"),
        ("_s", "s"),
        ("_k", "K"),
        ("_fraction", "fraction"),
    )
    token = path.rsplit(".", 1)[-1]
    return next((unit for suffix, unit in suffixes if token.endswith(suffix)), None)


def _node_inputs() -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    required: dict[str, dict[str, str]] = defaultdict(dict)
    optional: dict[str, dict[str, str]] = defaultdict(dict)
    catalog = dynamic_node_catalog()
    for node in catalog["nodes"]:
        node_id = str(node["node_id"])
        for item in node["required_inputs"]:
            required[node_id][str(item["path"])] = "EXPLICIT_REQUIRED"
        for path, kind, _note in NODE_CURATED_INPUTS[node_id]:
            target = optional if kind.startswith("OPTIONAL") else required
            target[node_id][path] = kind
    return required, optional


def build_rows() -> tuple[list[dict[str, str]], dict[str, Any], dict[str, Any]]:
    dictionary = _read_json(FIELD_DICTIONARY_PATH)
    schema = _read_json(QRA_INPUT_SCHEMA_PATH)
    dictionary_by_path = {str(item["target_path"]): item for item in dictionary["fields"]}
    schema_by_path = _explicit_schema_fields(schema)
    required, optional = _node_inputs()
    all_paths = set(dictionary_by_path) | set(schema_by_path) | set(RUNTIME_FIELD_DEFINITIONS)
    all_paths |= {path for values in required.values() for path in values}
    all_paths |= {path for values in optional.values() for path in values}
    mapping_targets = _mapping_targets()

    required_by_path: dict[str, dict[str, str]] = defaultdict(dict)
    optional_by_path: dict[str, dict[str, str]] = defaultdict(dict)
    for node_id, values in required.items():
        for path, kind in values.items():
            required_by_path[path][node_id] = kind
    for node_id, values in optional.items():
        for path, kind in values.items():
            optional_by_path[path][node_id] = kind

    rows: list[dict[str, str]] = []
    for path in sorted(all_paths, key=lambda value: (_field_id_from_path(value), value)):
        definition = dictionary_by_path.get(path, {})
        runtime = RUNTIME_FIELD_DEFINITIONS.get(path)
        indicator_group = definition.get("indicator_group_id")
        layer = _data_layer(path, indicator_group)
        source = _source_for(path, layer, indicator_group)
        value_type = str(
            definition.get("value_type")
            or (runtime[1] if runtime else None)
            or schema_by_path.get(path, {}).get("value_type")
            or "object"
        )
        target_unit = (
            definition.get("canonical_unit")
            or (runtime[2] if runtime else None)
            or _unit_from_path(path)
            or ""
        )
        source_unit = target_unit
        if layer == "PROJECT_FACT" and target_unit == "K":
            source_unit = "°C"
        elif layer == "PROJECT_FACT" and target_unit == "fraction":
            source_unit = "%"

        nodes = set(definition.get("required_by_nodes") or [])
        nodes.update(required_by_path[path])
        nodes.update(optional_by_path[path])
        if definition.get("indicator_id"):
            nodes.update({"data_inventory", "indicator_coverage", "adaptive_evidence_qra"})
        if path.startswith(
            ("pipeline", "segments", "population_cells", "weather_joint_probability")
        ):
            nodes.add("data_inventory")

        requirement_values = sorted(
            set(required_by_path[path].values()) | set(optional_by_path[path].values())
        )
        if required_by_path[path]:
            criticality = "BLOCKING"
        elif definition.get("required_level") == "CONTRACT_REQUIRED":
            criticality = "BLOCKING"
        elif definition.get("indicator_requirement") in {"REQUIRED", "CONDITIONAL"}:
            criticality = "IMPORTANT"
        elif nodes:
            criticality = "IMPORTANT"
        else:
            criticality = "OPTIONAL"

        contract_sources = []
        if path in dictionary_by_path:
            contract_sources.append("FIELD_DICTIONARY")
        if path in schema_by_path:
            contract_sources.append("QRA_INPUT_SCHEMA")
        if path in RUNTIME_FIELD_DEFINITIONS or required_by_path[path] or optional_by_path[path]:
            contract_sources.append("NODE_RUNTIME_REVIEW")

        if layer != "PROJECT_FACT":
            mapping_coverage = "NOT_APPLICABLE_SYSTEM_INJECTION"
        elif path in mapping_targets:
            mapping_coverage = "DETERMINISTIC_MAPPING_AVAILABLE"
        elif indicator_group in GROUP_RAW_BRIDGE:
            mapping_coverage = "RAW_CATEGORY_BRIDGE_AVAILABLE"
        else:
            mapping_coverage = "NOT_MAPPED"

        extraction_coverage = (
            "FIELD_DICTIONARY_SUPPORTED" if path in dictionary_by_path else "FIELD_DICTIONARY_GAP"
        )
        if path in dictionary_by_path:
            assembly_coverage = (
                "GENERIC_REVIEW_ASSEMBLY_REQUIRES_ENTITY_CONTAINER"
                if "*" in path
                else "GENERIC_REVIEW_ASSEMBLY_SUPPORTED"
            )
        elif layer == "MODEL_PARAMETER":
            assembly_coverage = "PARAMETER_PACK_MERGE_REQUIRED"
        else:
            assembly_coverage = "REVIEW_ASSEMBLY_GAP"

        gaps = []
        if layer == "PROJECT_FACT" and mapping_coverage == "NOT_MAPPED":
            gaps.append("DETERMINISTIC_MAPPING_GAP")
        if extraction_coverage == "FIELD_DICTIONARY_GAP" and layer == "PROJECT_FACT":
            gaps.append("EXTRACTION_SCHEMA_GAP")
        if assembly_coverage == "REVIEW_ASSEMBLY_GAP":
            gaps.append("REVIEW_ASSEMBLY_GAP")
        if assembly_coverage == "PARAMETER_PACK_MERGE_REQUIRED":
            gaps.append("PARAMETER_PACK_MERGE_NOT_IMPLEMENTED")

        if layer == "MODEL_PARAMETER":
            missing_policy = "BLOCK_NODE_IF_VERSIONED_PARAMETER_PACK_MISSING"
            conflict_policy = "VERSIONED_PACK_SELECTION_ONLY"
        elif layer == "RUN_ASSUMPTION":
            missing_policy = (
                "BLOCK_NODE_AND_REQUIRE_EXPLICIT_RUN_VALUE"
                if criticality == "BLOCKING"
                else "REQUIRE_EXPLICIT_RECORD_OR_DOCUMENTED_NOT_APPLICABLE"
            )
            conflict_policy = "EXPLICIT_DECISION_REQUIRED"
        else:
            missing_policy = (
                "BLOCK_NODE_AND_REQUEST_SOURCE"
                if criticality == "BLOCKING"
                else "OMIT_AND_RECORD_GAP_NO_SILENT_ZERO"
            )
            conflict_policy = "BLOCK_AND_REQUIRE_HUMAN_DECISION"

        rows.append(
            {
                "field_id": str(definition.get("field_id") or _field_id_from_path(path)),
                "business_name": str(
                    definition.get("name_zh")
                    or (runtime[0] if runtime else None)
                    or path.rsplit(".", 1)[-1]
                ),
                "target_path": path,
                "data_layer": layer,
                "source_document": source,
                "source_location_type": _source_location(source),
                "source_unit": str(source_unit),
                "target_unit": str(target_unit),
                "criticality": criticality,
                "extraction_method": _extraction_method(source),
                "target_nodes": "|".join(sorted(nodes)),
                "missing_policy": missing_policy,
                "conflict_policy": conflict_policy,
                "evidence_required": "true" if layer == "PROJECT_FACT" else "false",
                "value_type": value_type,
                "cardinality": str(
                    definition.get("cardinality")
                    or schema_by_path.get(path, {}).get("cardinality")
                    or ("MANY" if "*" in path else "OPTIONAL_ONE")
                ),
                "contract_sources": "|".join(contract_sources),
                "node_requirement": "|".join(requirement_values) or "OPTIONAL_CONTRACT_FIELD",
                "default_or_prior": DEFAULTS_AND_PRIORS.get(path, "NONE"),
                "mapping_coverage": mapping_coverage,
                "extraction_coverage": extraction_coverage,
                "assembly_coverage": assembly_coverage,
                "coverage_gap": "|".join(gaps) or "NONE",
                "notes": str(definition.get("description") or ""),
            }
        )

    row_by_path = {row["target_path"]: row for row in rows}
    node_contract = {
        "schema_version": "1.0.0",
        "stage_status": "S1_FULL_CONTRACT_MAPPED",
        "source": "dynamic_node_catalog + reviewed runtime consumption",
        "nodes": [],
    }
    dynamic_by_id = {row["node_id"]: row for row in dynamic_node_catalog()["nodes"]}
    for node_id in dynamic_by_id:
        node_contract["nodes"].append(
            {
                "node_id": node_id,
                "dependencies": list(dynamic_by_id[node_id]["dependencies"]),
                "required_inputs": [
                    {
                        "path": path,
                        "requirement_kind": kind,
                        "field_id": row_by_path[path]["field_id"],
                        "data_layer": row_by_path[path]["data_layer"],
                        "source_document": row_by_path[path]["source_document"],
                    }
                    for path, kind in sorted(required[node_id].items())
                ],
                "optional_inputs": [
                    {
                        "path": path,
                        "requirement_kind": kind,
                        "field_id": row_by_path[path]["field_id"],
                        "data_layer": row_by_path[path]["data_layer"],
                        "source_document": row_by_path[path]["source_document"],
                    }
                    for path, kind in sorted(optional[node_id].items())
                ],
            }
        )

    contract_export = {
        "schema_version": "1.0.0",
        "part1_contract_id": dictionary["dictionary_id"],
        "part1_contract_version": dictionary["version"],
        "field_dictionary_field_count": len(dictionary_by_path),
        "explicit_schema_field_count": len(schema_by_path),
        "runtime_review_field_count": len(RUNTIME_FIELD_DEFINITIONS),
        "matrix_union_field_count": len(rows),
        "fields": [
            {
                "field_id": row["field_id"],
                "target_path": row["target_path"],
                "business_name": row["business_name"],
                "value_type": row["value_type"],
                "target_unit": row["target_unit"],
                "contract_sources": row["contract_sources"].split("|")
                if row["contract_sources"]
                else [],
            }
            for row in rows
        ],
    }
    return rows, node_contract, contract_export


def validate_rows(
    rows: list[dict[str, str]], node_contract: dict[str, Any], contract_export: dict[str, Any]
) -> dict[str, Any]:
    errors: list[str] = []
    paths = [row["target_path"] for row in rows]
    field_ids = [row["field_id"] for row in rows]
    if len(paths) != len(set(paths)):
        errors.append("target_path不唯一")
    if len(field_ids) != len(set(field_ids)):
        errors.append("field_id不唯一")
    for row in rows:
        missing_columns = [column for column in MATRIX_COLUMNS if column not in row]
        if missing_columns:
            errors.append(f"{row.get('target_path')}缺少列：{missing_columns}")
        if row["data_layer"] not in DATA_LAYERS:
            errors.append(f"{row['target_path']}数据层无效")
        if row["criticality"] not in CRITICALITIES:
            errors.append(f"{row['target_path']}关键性无效")
        if not row["source_document"]:
            errors.append(f"{row['target_path']}未分配来源")
        if "DEFAULT_ZERO" in row["missing_policy"]:
            errors.append(f"{row['target_path']}存在静默补零策略")
        if row["default_or_prior"] != "NONE" and not row["source_document"]:
            errors.append(f"{row['target_path']}默认或先验未登记来源")
        if row["data_layer"] == "PROJECT_FACT":
            source_documents = set(row["source_document"].split("|"))
            suffixes = {Path(item).suffix.casefold() for item in source_documents}
            if (
                not suffixes
                or not suffixes <= RAW_DOCUMENT_SUFFIXES
                or not source_documents <= set(SOURCE_DOCUMENTS.values())
            ):
                errors.append(f"{row['target_path']}项目事实未分配原始资料")
            if row["evidence_required"] != "true":
                errors.append(f"{row['target_path']}项目事实未要求证据")
        if row["data_layer"] == "MODEL_PARAMETER":
            parameter_pack = row["source_document"].removeprefix("parameter-pack:")
            if (
                not row["source_document"].startswith("parameter-pack:")
                or parameter_pack not in VERSIONED_PARAMETER_PACKS
                or "-v" not in parameter_pack
            ):
                errors.append(f"{row['target_path']}模型参数未分配版本参数包")
        if row["data_layer"] == "RUN_ASSUMPTION" and not row["source_document"].startswith(
            ("run-assumption:", "system-generated:", "project-wizard:")
        ):
            errors.append(f"{row['target_path']}场景假设未明确记录")

    row_by_path = {row["target_path"]: row for row in rows}
    current_nodes = [row["node_id"] for row in dynamic_node_catalog()["nodes"]]
    exported_nodes = [row["node_id"] for row in node_contract["nodes"]]
    if current_nodes != exported_nodes:
        errors.append("节点目录发生漂移")
    for node in node_contract["nodes"]:
        for item in node["required_inputs"]:
            row = row_by_path.get(item["path"])
            if row is None:
                errors.append(f"{node['node_id']}必需输入未进入矩阵：{item['path']}")
            elif not row["source_document"]:
                errors.append(f"{node['node_id']}必需输入无来源：{item['path']}")

    dictionary_paths = {
        str(item["target_path"]) for item in _read_json(FIELD_DICTIONARY_PATH)["fields"]
    }
    schema_paths = set(_explicit_schema_fields(_read_json(QRA_INPUT_SCHEMA_PATH)))
    if not dictionary_paths <= set(paths):
        errors.append("字段字典存在未导出字段")
    if not schema_paths <= set(paths):
        errors.append("qra-input Schema存在未导出字段")

    counts = {
        "matrix_field_count": len(rows),
        "field_dictionary_field_count": contract_export["field_dictionary_field_count"],
        "explicit_schema_field_count": contract_export["explicit_schema_field_count"],
        "runtime_review_field_count": contract_export["runtime_review_field_count"],
        "node_count": len(node_contract["nodes"]),
        "required_node_input_count": sum(
            len(node["required_inputs"]) for node in node_contract["nodes"]
        ),
        "optional_node_input_count": sum(
            len(node["optional_inputs"]) for node in node_contract["nodes"]
        ),
        "project_fact_count": sum(row["data_layer"] == "PROJECT_FACT" for row in rows),
        "model_parameter_count": sum(row["data_layer"] == "MODEL_PARAMETER" for row in rows),
        "run_assumption_count": sum(row["data_layer"] == "RUN_ASSUMPTION" for row in rows),
        "blocking_field_count": sum(row["criticality"] == "BLOCKING" for row in rows),
        "historical_coverage_gap_field_count_at_stage1_freeze": sum(
            row["coverage_gap"] != "NONE" for row in rows
        ),
    }
    return {
        "schema_version": "1.0.0",
        "stage_id": "S1",
        "status": "S1_FULL_CONTRACT_MAPPED" if not errors else "S1_CONTRACT_BLOCKED",
        "passed": not errors,
        "counts": counts,
        "checks": {
            "all_dictionary_fields_exported": dictionary_paths <= set(paths),
            "all_explicit_schema_fields_exported": schema_paths <= set(paths),
            "all_eleven_nodes_exported": current_nodes == exported_nodes
            and len(current_nodes) == 11,
            "all_required_node_inputs_have_sources": not any(
                "必需输入" in error for error in errors
            ),
            "all_project_facts_have_raw_documents": not any(
                "项目事实" in error for error in errors
            ),
            "all_model_parameters_have_versioned_packs": not any(
                "模型参数" in error for error in errors
            ),
            "all_run_assumptions_are_explicit": not any("场景假设" in error for error in errors),
            "no_unregistered_silent_zero_default": not any("静默补零" in error for error in errors),
            "stage1_freeze_implementation_gaps_are_classified": all(
                row["coverage_gap"] for row in rows
            ),
        },
        "errors": errors,
    }


def _csv_bytes(rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=MATRIX_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def build_artifacts() -> dict[Path, bytes]:
    rows, node_contract, contract_export = build_rows()
    acceptance = validate_rows(rows, node_contract, contract_export)
    gaps = [row for row in rows if row["coverage_gap"] != "NONE"]
    gap_register = {
        "schema_version": "1.0.0",
        "status": "SUPERSEDED_BY_COVERAGE_CLOSURE",
        "closure_record": "coverage-gap-closure.json",
        "gap_field_count": len(gaps),
        "gap_counts": dict(
            sorted(
                {
                    code: sum(code in row["coverage_gap"].split("|") for row in gaps)
                    for code in {item for row in gaps for item in row["coverage_gap"].split("|")}
                }.items()
            )
        ),
        "fields": [
            {
                "field_id": row["field_id"],
                "target_path": row["target_path"],
                "data_layer": row["data_layer"],
                "criticality": row["criticality"],
                "coverage_gap": row["coverage_gap"].split("|"),
                "planned_stage": "S2-S3",
            }
            for row in gaps
        ],
    }
    return {
        OUTPUT_ROOT / "field-source-node-matrix.csv": _csv_bytes(rows),
        OUTPUT_ROOT / "node-input-contract.json": _json_bytes(node_contract),
        OUTPUT_ROOT / "qra-input-contract-fields.json": _json_bytes(contract_export),
        OUTPUT_ROOT / "coverage-gap-register.json": _json_bytes(gap_register),
        OUTPUT_ROOT / "stage1-acceptance.json": _json_bytes(acceptance),
    }


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_artifacts(artifacts: dict[Path, bytes]) -> None:
    for path, content in artifacts.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def check_artifacts(artifacts: dict[Path, bytes]) -> list[str]:
    errors = []
    for path, expected in artifacts.items():
        if not path.is_file():
            errors.append(f"缺少阶段1交付物：{path.relative_to(PROJECT_ROOT)}")
            continue
        actual = path.read_bytes()
        if actual != expected:
            errors.append(
                f"阶段1交付物过期：{path.relative_to(PROJECT_ROOT)} "
                f"expected={_sha256(expected)} actual={_sha256(actual)}"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="构建并校验M1.5阶段1完整字段覆盖合同")
    parser.add_argument("--check", action="store_true", help="只检查已提交交付物是否与当前代码一致")
    args = parser.parse_args()
    artifacts = build_artifacts()
    if args.check:
        errors = check_artifacts(artifacts)
        if errors:
            print(json.dumps({"status": "BLOCK", "errors": errors}, ensure_ascii=False, indent=2))
            return 2
    else:
        write_artifacts(artifacts)
    acceptance = json.loads(artifacts[OUTPUT_ROOT / "stage1-acceptance.json"])
    print(json.dumps(acceptance, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if acceptance["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
