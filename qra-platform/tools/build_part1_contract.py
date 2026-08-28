"""Build the immutable Part 1 v1 contract from the current engine catalogs.

Run only while preparing a new contract version.  Published contract directories
are verified at runtime and must not be regenerated in place after release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from qra_engine.dynamic import dynamic_node_catalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "resources" / "contracts" / "part1" / "v1"
INDICATOR_CATALOG = (
    PROJECT_ROOT / "src" / "qra_engine" / "model_specs" / "qra_indicator_catalog_v1.json"
)
FULL_SYNTHETIC = PROJECT_ROOT / "tests" / "fixtures" / "qra_synthetic_case_v1.json"
MATRIX_OUTPUT = (
    PROJECT_ROOT / "tests" / "fixtures" / "contracts_v1" / "expected-node-field-matrix.json"
)

CONTRACT_ID = "qra.part1-input"
VERSION = "1.0.0"
QUALITY_STATUSES = [
    "PASS",
    "INFO",
    "WARNING",
    "CONFLICT",
    "LOW_CONFIDENCE",
    "MISSING",
    "INVALID",
    "NOT_APPLICABLE",
    "PENDING_REVIEW",
]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def field_id_from_path(path: str) -> str:
    prefixes = {
        "segments.*.": "segment.",
        "weather_joint_probability.*.": "weather_case.",
        "population_cells.*.": "population_cell.",
    }
    for prefix, replacement in prefixes.items():
        if path.startswith(prefix):
            return replacement + path.removeprefix(prefix)
    return path.replace(".*.", ".item.").replace("*", "item")


def entity_for_path(path: str, scope: str | None = None) -> str:
    if path.startswith("metadata."):
        return "PROJECT"
    if path.startswith("assessment."):
        return "ASSESSMENT"
    if path.startswith("pipeline."):
        return "PIPELINE"
    if path.startswith("segments.*."):
        return "SEGMENT"
    if path.startswith("weather_joint_probability.*."):
        return "WEATHER_CASE"
    if path.startswith("population_cells.*."):
        return "POPULATION_CELL"
    if path.startswith("engineering_indicators."):
        return "ENGINEERING_INDICATOR"
    if scope and "segment" in scope:
        return "SEGMENT"
    return "PROJECT"


def constraints_for(path: str, value_type: str) -> dict[str, Any]:
    constraints: dict[str, Any] = {}
    lowered = path.casefold()
    if value_type in {"number", "integer"} and any(
        token in lowered
        for token in (
            "length",
            "pressure",
            "diameter",
            "thickness",
            "depth",
            "time_s",
            "speed",
            "population",
            "frequency",
        )
    ):
        constraints["minimum"] = 0
    if "probability" in lowered or "fraction" in lowered:
        constraints.update({"minimum": 0, "maximum": 1})
    if path.endswith("segment_id") or path.endswith("case_id"):
        constraints["pattern"] = r"^\S(?:.*\S)?$"
    return constraints


def make_field(
    *,
    path: str,
    name_zh: str,
    value_type: str = "string",
    unit: str | None = None,
    entity_type: str | None = None,
    required_by_nodes: list[str] | None = None,
    required_level: str = "OPTIONAL",
    description: str | None = None,
    field_id: str | None = None,
    indicator: dict[str, Any] | None = None,
    test_domain: bool = False,
) -> dict[str, Any]:
    nodes = sorted(set(required_by_nodes or []))
    if nodes:
        required_level = "NODE_REQUIRED"
    result = {
        "field_id": field_id or field_id_from_path(path),
        "target_path": path,
        "name_zh": name_zh,
        "aliases_zh": [name_zh],
        "entity_type": entity_type or entity_for_path(path),
        "value_type": value_type,
        "cardinality": "MANY" if "*" in path else "OPTIONAL_ONE",
        "source_unit": [unit] if unit else [],
        "canonical_unit": unit,
        "constraints": constraints_for(path, value_type),
        "required_level": required_level,
        "required_by_nodes": nodes,
        "allowed_source_types": [
            "DESIGN_DOCUMENT",
            "AS_BUILT_DRAWING",
            "OPERATING_RECORD",
            "INSPECTION_RECORD",
            "APPROVED_PARAMETER_LIBRARY",
            "MANUAL_APPROVAL",
        ],
        "evidence_policy": "SYSTEM_DERIVED" if test_domain else "DOCUMENT_REQUIRED",
        "conflict_policy": {
            "blocking": True,
            "numeric_tolerance": 0,
            "use_source_rank": True,
        },
        "sensitivity": (
            "SENSITIVE"
            if path.startswith("population_cells") or "coordinate" in path or "xy_m" in path
            else "NORMAL"
        ),
        "description": description or name_zh,
    }
    if indicator:
        result.update(indicator)
    if test_domain:
        result["extraction_scope"] = "TEST_ONLY"
        result["allowed_source_types"] = ["SYNTHETIC_TEST_FIXTURE"]
    return result


CURATED_FIELDS: tuple[tuple[str, str, str, str | None], ...] = (
    ("metadata.case_id", "案例编号", "string", None),
    ("metadata.project_name", "项目名称", "string", None),
    ("metadata.data_classification", "数据分类", "enum", None),
    ("metadata.run_profile", "运行配置", "enum", None),
    ("assessment.assessment_id", "评价编号", "string", None),
    ("assessment.as_of", "评价日期", "date", None),
    ("assessment.coordinate_system", "坐标系", "string", None),
    ("assessment.leak_point_initial_spacing_m", "泄漏点初始间距", "number", "m"),
    ("assessment.enabled_consequence_domains", "启用后果域", "array", None),
    ("assessment.criteria_set_by_domain", "分域准则集", "object", None),
    ("pipeline.pipeline_id", "管线编号", "string", None),
    ("pipeline.total_length_km", "管线总长度", "number", "km"),
    ("pipeline.outside_diameter_mm", "管线共性外径", "number", "mm"),
    ("pipeline.wall_thickness_mm", "管线共性壁厚", "number", "mm"),
    ("pipeline.design_pressure_mpa", "设计压力", "number", "MPa"),
    ("pipeline.operating_pressure_mpa", "运行压力", "number", "MPa"),
    ("pipeline.operating_temperature_k", "运行温度", "number", "K"),
    ("pipeline.gas_composition_mole_fraction", "气体摩尔组分", "object", None),
    ("segments.*.segment_id", "管段编号", "string", None),
    ("segments.*.start_km", "管段起点里程", "number", "km"),
    ("segments.*.end_km", "管段终点里程", "number", "km"),
    ("segments.*.length_km", "管段长度", "number", "km"),
    ("segments.*.start_xy_m", "管段起点坐标", "array", "m"),
    ("segments.*.end_xy_m", "管段终点坐标", "array", "m"),
    ("segments.*.outside_diameter_mm", "管段外径", "number", "mm"),
    ("segments.*.wall_thickness_mm", "管段壁厚", "number", "mm"),
    ("segments.*.steel_grade", "钢级", "string", None),
    ("segments.*.burial_depth_m", "埋深", "number", "m"),
    ("segments.*.upstream_valve_km", "上游阀里程", "number", "km"),
    ("segments.*.downstream_valve_km", "下游阀里程", "number", "km"),
    ("segments.*.leak_detection_time_s", "泄漏检测时间", "number", "s"),
    ("segments.*.valve_closure_time_s", "阀门关断时间", "number", "s"),
    ("engineering_indicators.catalog_id", "工程指标目录编号", "string", None),
    ("engineering_indicators.catalog_version", "工程指标目录版本", "string", None),
    ("frequency_library.unit", "失效频率单位", "enum", None),
    ("frequency_library.base_frequency_by_mechanism", "分机理基准频率", "object", "per_km_year"),
    ("frequency_library.loc_fraction_by_mechanism", "分机理孔径比例", "object", None),
    ("frequency_correction_model", "频率修正模型", "object", None),
    ("segment_correction_factor", "管段修正因子", "object", None),
    ("weather_joint_probability.*.weather_id", "气象工况编号", "string", None),
    ("weather_joint_probability.*.time_period", "昼夜时段", "enum", None),
    ("weather_joint_probability.*.stability_class", "大气稳定度", "enum", None),
    ("weather_joint_probability.*.wind_speed_m_s", "风速", "number", "m/s"),
    ("weather_joint_probability.*.wind_direction_from", "来风方向", "string", "deg"),
    ("weather_joint_probability.*.probability", "气象联合概率", "number", None),
    ("population_cells.*.cell_id", "人口网格编号", "string", None),
    ("population_cells.*.xy_m", "人口网格坐标", "array", "m"),
    ("population_cells.*.population_day", "昼间人口", "number", "person"),
    ("population_cells.*.population_night", "夜间人口", "number", "person"),
    ("population_cells.*.outdoor_fraction_day", "昼间室外比例", "number", None),
    ("population_cells.*.outdoor_fraction_night", "夜间室外比例", "number", None),
    ("ignition_model", "点火模型", "object", None),
    ("standard_formula_test_parameters.aqt3046_physical_chain.ambient_pressure_pa_abs", "环境绝压", "number", "Pa_abs"),
    ("standard_formula_test_parameters.aqt3046_physical_chain.molar_mass_kg_mol", "摩尔质量", "number", "kg/mol"),
    ("standard_formula_test_parameters.aqt3046_physical_chain.gamma", "绝热指数", "number", None),
    ("standard_formula_test_parameters.aqt3046_physical_chain.gas_discharge_coefficient", "气体泄放系数", "number", None),
    ("standard_formula_test_parameters.aqt3046_physical_chain.pipe_absolute_roughness_mm", "管壁绝对粗糙度", "number", "mm"),
    ("standard_formula_test_parameters.aqt3046_physical_chain.heat_of_combustion_j_kg", "燃烧热", "number", "J/kg"),
    ("standard_formula_test_parameters.aqt3046_physical_chain.radiative_fraction", "辐射分数", "number", None),
    ("standard_formula_test_parameters.gbt34346_annex_c.segments", "附录C逐段参数", "object", None),
    ("raw_data_categories", "原始资料类别", "object", None),
    ("data_category_manifest", "资料类别清单", "object", None),
)


def build_field_dictionary() -> tuple[dict[str, Any], dict[str, Any]]:
    node_catalog = dynamic_node_catalog()
    required_by_path: dict[str, list[str]] = {}
    for node in node_catalog["nodes"]:
        for requirement in node["required_inputs"]:
            required_by_path.setdefault(requirement["path"], []).append(node["node_id"])

    catalog = json.loads(INDICATOR_CATALOG.read_text(encoding="utf-8"))
    columns = catalog["field_columns"]
    fields_by_path: dict[str, dict[str, Any]] = {}
    indicator_ids: list[str] = []
    for group in catalog["groups"]:
        for raw in group["fields"]:
            values = dict(zip(columns, raw, strict=True))
            indicator_id = f"{group['group_id']}.{values['field_id']}"
            indicator_ids.append(indicator_id)
            case_path = values.get("case_path")
            if case_path:
                path = case_path
                stable_field_id = field_id_from_path(path)
            elif "segment" in group["scope"]:
                path = f"engineering_indicators.observations_by_segment.*.{indicator_id}"
                stable_field_id = f"engineering_indicator.{indicator_id}"
            else:
                path = f"engineering_indicators.observations_global.{indicator_id}"
                stable_field_id = f"engineering_indicator.{indicator_id}"
            field = make_field(
                path=path,
                field_id=stable_field_id,
                name_zh=values["name_zh"],
                value_type=values["value_type"],
                unit=values.get("unit"),
                entity_type=entity_for_path(path, group["scope"]),
                required_level=(
                    "CONDITIONAL" if values["requirement"] != "OPTIONAL" else "OPTIONAL"
                ),
                required_by_nodes=required_by_path.get(path),
                description=(
                    f"工程指标{indicator_id}；组：{group['name_zh']}；"
                    f"要求：{values['requirement']}。"
                ),
                indicator={
                    "indicator_id": indicator_id,
                    "indicator_group_id": group["group_id"],
                    "indicator_requirement": values["requirement"],
                    "source_refs": list(group["source_refs"]),
                },
            )
            if path in fields_by_path:
                raise ValueError(f"指标目录出现重复target_path：{path}")
            fields_by_path[path] = field

    for path, name, value_type, unit in CURATED_FIELDS:
        existing = fields_by_path.get(path)
        nodes = required_by_path.get(path, [])
        if existing is not None:
            existing["required_by_nodes"] = sorted(
                set(existing["required_by_nodes"]) | set(nodes)
            )
            if nodes:
                existing["required_level"] = "NODE_REQUIRED"
            continue
        fields_by_path[path] = make_field(
            path=path,
            name_zh=name,
            value_type=value_type,
            unit=unit,
            required_by_nodes=nodes,
            required_level=(
                "CONTRACT_REQUIRED"
                if path in {"metadata.case_id", "segments.*.segment_id"}
                else "OPTIONAL"
            ),
        )

    for path, nodes in required_by_path.items():
        if path not in fields_by_path:
            fields_by_path[path] = make_field(
                path=path,
                name_zh=path,
                value_type="object" if not path.rsplit(".", 1)[-1].endswith(("_km", "_m", "_k")) else "number",
                required_by_nodes=nodes,
            )

    for path, name in (
        ("damage_model", "测试损伤模型"),
        ("mock_adapter_output", "测试适配器输出"),
        ("expected_aggregation", "测试聚合黄金答案"),
        ("validation_expectations", "测试校验预期"),
    ):
        fields_by_path[path] = make_field(
            path=path,
            name_zh=name,
            value_type="object",
            test_domain=True,
            description="仅用于合成测试和模型验证，不得作为客户资料抽取目标。",
        )

    fields = sorted(fields_by_path.values(), key=lambda row: row["field_id"])
    field_ids = [row["field_id"] for row in fields]
    paths = [row["target_path"] for row in fields]
    if len(field_ids) != len(set(field_ids)):
        duplicates = sorted({item for item in field_ids if field_ids.count(item) > 1})
        raise ValueError(f"字段ID重复：{duplicates}")
    if len(paths) != len(set(paths)):
        raise ValueError("字段target_path重复")
    registered_indicator_ids = [row["indicator_id"] for row in fields if "indicator_id" in row]
    if set(registered_indicator_ids) != set(indicator_ids):
        raise ValueError("工程指标目录导入不完整")

    reviewed_implicit = {
        "failure_frequency": [
            "frequency_correction_model",
            "segment_correction_factor",
            "engineering_indicators.catalog_id",
        ],
        "gbt34346_annex_c": [
            "standard_formula_test_parameters.gbt34346_annex_c.segments"
        ],
        "human_qra": [
            "weather_joint_probability.*.probability",
            "population_cells.*.population_day",
            "population_cells.*.population_night",
            "ignition_model",
        ],
    }
    matrix_nodes = []
    for node in node_catalog["nodes"]:
        matrix_nodes.append(
            {
                "node_id": node["node_id"],
                "dependencies": node["dependencies"],
                "explicit_required_inputs": node["required_inputs"],
                "reviewed_implicit_inputs": reviewed_implicit.get(node["node_id"], []),
            }
        )
    matrix = {
        "schema_version": "1.0.0",
        "source": "qra_engine.dynamic.dynamic_node_catalog + stage-1 review",
        "nodes": matrix_nodes,
    }
    dictionary = {
        "dictionary_id": CONTRACT_ID,
        "version": VERSION,
        "indicator_catalog": {
            "catalog_id": catalog["catalog_id"],
            "version": catalog["version"],
            "group_count": len(catalog["groups"]),
            "indicator_count": len(indicator_ids),
        },
        "fields": fields,
    }
    return dictionary, matrix


def qra_input_schema() -> dict[str, Any]:
    scalar = {"type": ["string", "number", "integer", "boolean", "object", "array", "null"]}
    metadata_properties = {
        key: scalar
        for key in (
            "case_id",
            "project_name",
            "name",
            "version",
            "created_at",
            "data_classification",
            "allowed_use",
            "prohibited_use",
            "model_id",
            "run_profile",
            "converter_note",
            "formal_qra_allowed",
            "conversion_scope",
            "warning",
            "explicit_data_gaps",
        )
    }
    pipeline_properties = {
        key: scalar
        for key in (
            "pipeline_id",
            "service",
            "installation",
            "material",
            "total_length_km",
            "outside_diameter_mm",
            "wall_thickness_mm",
            "design_pressure_mpa",
            "operating_pressure_mpa",
            "operating_temperature_k",
            "gas_composition_mole_fraction",
            "source_refs",
            "operating_pressure_data_status",
            "operating_temperature_data_status",
            "wall_thickness_data_status",
            "population_spatial_data_status",
        )
    }
    segment_properties = {
        key: scalar
        for key in (
            "segment_id",
            "start_km",
            "end_km",
            "length_km",
            "start_xy_m",
            "end_xy_m",
            "outside_diameter_mm",
            "wall_thickness_mm",
            "steel_grade",
            "material_grade",
            "commissioning_year",
            "burial_depth_m",
            "area_activity",
            "population_context",
            "upstream_valve_km",
            "downstream_valve_km",
            "leak_detection_time_s",
            "valve_closure_time_s",
            "split_reason",
            "source_ref",
            "source_refs",
            "quality",
            "review_status",
        )
    }
    segment_properties.update(
        {
            "segment_id": {"type": "string", "minLength": 1},
            "start_km": {"type": "number", "minimum": 0},
            "end_km": {"type": "number", "exclusiveMinimum": 0},
            "length_km": {"type": "number", "exclusiveMinimum": 0},
            "start_xy_m": {"type": "array", "prefixItems": [{"type": "number"}, {"type": "number"}], "items": False, "minItems": 2},
            "end_xy_m": {"type": "array", "prefixItems": [{"type": "number"}, {"type": "number"}], "items": False, "minItems": 2},
        }
    )
    optional_sections = (
        "assessment",
        "engineering_indicators",
        "frequency_library",
        "frequency_correction_model",
        "segment_correction_factor",
        "weather_joint_probability",
        "population_cells",
        "ignition_model",
        "standard_formula_test_parameters",
        "raw_data_categories",
        "data_category_manifest",
        "damage_model",
        "mock_adapter_output",
        "expected_aggregation",
        "validation_expectations",
    )
    properties: dict[str, Any] = {
        "schema_version": {"type": "string"},
        "metadata": {
            "type": "object",
            "properties": metadata_properties,
            "additionalProperties": False,
            "anyOf": [{"required": ["case_id"]}, {"required": ["project_name"]}],
        },
        "pipeline": {
            "type": "object",
            "properties": pipeline_properties,
            "additionalProperties": False,
        },
        "segments": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["segment_id", "start_km", "end_km", "length_km"],
                "properties": segment_properties,
                "additionalProperties": False,
            },
        },
        "extensions": {"type": "object"},
    }
    for section in optional_sections:
        if section in {"weather_joint_probability", "population_cells"}:
            properties[section] = {"type": "array", "items": {"type": "object"}}
        else:
            properties[section] = {"type": "object"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://qra.local/contracts/part1/v1/qra-input.schema.json",
        "title": "QRA Part 1 input snapshot",
        "type": "object",
        "required": ["metadata", "pipeline", "segments"],
        "properties": properties,
        "additionalProperties": False,
    }


def candidate_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://qra.local/contracts/part1/v1/candidate-field.schema.json",
        "type": "object",
        "required": [
            "candidate_id", "field_id", "entity", "raw_value", "parsed_value",
            "normalized_value", "confidence", "extraction_method", "evidence_ids",
            "quality_status", "review_status", "model_or_rule_versions",
        ],
        "properties": {
            "candidate_id": {"type": "string", "pattern": "^CAND-[A-Za-z0-9._-]+$"},
            "field_id": {"type": "string", "minLength": 1},
            "entity": {
                "type": "object",
                "required": ["entity_type", "entity_key"],
                "properties": {"entity_type": {"type": "string"}, "entity_key": {"type": "string"}},
                "additionalProperties": False,
            },
            "raw_value": {}, "parsed_value": {}, "normalized_value": {},
            "source_unit": {"type": ["string", "null"]},
            "canonical_unit": {"type": ["string", "null"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "extraction_method": {"enum": ["STRUCTURED_TABLE", "TEXT_RULE", "OCR", "MODEL_EXTRACTION", "MANUAL", "SYSTEM_DERIVED"]},
            "evidence_ids": {"type": "array", "items": {"type": "string", "pattern": "^EVD-"}, "uniqueItems": True},
            "non_document_source": {"enum": ["APPROVAL_REQUIRED", "SYSTEM_DERIVED", "MANUAL_ALLOWED"]},
            "derivation": {
                "type": "object",
                "required": ["rule_id", "rule_version", "input_candidate_ids"],
                "properties": {
                    "rule_id": {"type": "string"}, "rule_version": {"type": "string"},
                    "input_candidate_ids": {"type": "array", "minItems": 1, "items": {"type": "string", "pattern": "^CAND-"}},
                },
                "additionalProperties": False,
            },
            "quality_status": {"enum": QUALITY_STATUSES},
            "review_status": {"enum": ["PENDING", "ACCEPTED", "REJECTED", "NOT_APPLICABLE"]},
            "model_or_rule_versions": {"type": "object", "minProperties": 1},
        },
        "allOf": [
            {"anyOf": [{"properties": {"evidence_ids": {"minItems": 1}}}, {"required": ["non_document_source"]}]},
            {"if": {"properties": {"extraction_method": {"const": "SYSTEM_DERIVED"}}, "required": ["extraction_method"]}, "then": {"required": ["derivation", "non_document_source"], "properties": {"non_document_source": {"const": "SYSTEM_DERIVED"}}}},
        ],
        "additionalProperties": False,
    }


def evidence_schema() -> dict[str, Any]:
    location_variants = []
    specs = {
        "TABLE": ({"file_id", "sheet_name", "row", "column", "cell_text", "coordinate_system"}, {"row": {"type": "integer", "minimum": 1}, "column": {"type": "integer", "minimum": 1}}),
        "PDF": ({"file_id", "page", "bbox", "page_size", "coordinate_system"}, {"page": {"type": "integer", "minimum": 1}, "bbox": {"$ref": "#/$defs/box"}, "page_size": {"$ref": "#/$defs/size"}}),
        "DOCX": ({"file_id", "paragraph_index", "ooxml_part", "coordinate_system"}, {"paragraph_index": {"type": "integer", "minimum": 0}}),
        "IMAGE": ({"file_id", "bbox", "image_size", "coordinate_system"}, {"bbox": {"$ref": "#/$defs/box"}, "image_size": {"$ref": "#/$defs/size"}}),
        "MANUAL": ({"operator", "entered_at", "reason", "coordinate_system"}, {"entered_at": {"type": "string", "format": "date-time"}}),
        "PARAMETER_LIBRARY": ({"library_id", "version", "approval_ref", "applicability", "coordinate_system"}, {}),
    }
    for kind, (required, overrides) in specs.items():
        props = {key: {"type": "string"} for key in required}
        props.update(overrides)
        props["kind"] = {"const": kind}
        if kind == "DOCX":
            props.update(
                {
                    "table_index": {"type": "integer", "minimum": 0},
                    "row": {"type": "integer", "minimum": 1},
                    "column": {"type": "integer", "minimum": 1},
                }
            )
        location_variants.append({"type": "object", "required": ["kind", *sorted(required)], "properties": props, "additionalProperties": False})
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://qra.local/contracts/part1/v1/evidence.schema.json",
        "$defs": {
            "box": {"type": "array", "prefixItems": [{"type": "number"}, {"type": "number"}, {"type": "number"}, {"type": "number"}], "items": False, "minItems": 4},
            "size": {"type": "array", "prefixItems": [{"type": "number", "exclusiveMinimum": 0}, {"type": "number", "exclusiveMinimum": 0}], "items": False, "minItems": 2},
        },
        "type": "object",
        "required": ["evidence_id", "source_type", "location"],
        "properties": {
            "evidence_id": {"type": "string", "pattern": "^EVD-"},
            "source_type": {"enum": list(specs)},
            "location": {"oneOf": location_variants},
            "excerpt": {"type": ["string", "null"]},
            "checksum_sha256": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
        },
        "additionalProperties": False,
    }


def quality_issue_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://qra.local/contracts/part1/v1/quality-issue.schema.json",
        "type": "object",
        "required": ["issue_id", "code", "quality_status", "blocking", "target", "message"],
        "properties": {
            "issue_id": {"type": "string", "pattern": "^ISS-"},
            "code": {"type": "string", "pattern": "^(INTAKE|PARSE|EXTRACT|NORMALIZE|FUSION|CONTRACT|GATE|DELIVERY)\\."},
            "quality_status": {"enum": QUALITY_STATUSES},
            "blocking": {"type": "boolean"}, "target": {"type": "string"},
            "message": {"type": "string", "minLength": 1},
            "candidate_ids": {"type": "array", "items": {"type": "string"}},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }


def review_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://qra.local/contracts/part1/v1/review-decision.schema.json",
        "type": "object",
        "required": ["review_id", "action", "target", "before_value", "after_value", "reviewer", "reviewed_at", "reason", "candidate_ids", "evidence_ids"],
        "properties": {
            "review_id": {"type": "string", "pattern": "^REV-"},
            "action": {"enum": ["ACCEPT_CANDIDATE", "REPLACE_VALUE", "REJECT_FIELD", "MARK_NOT_APPLICABLE", "REQUEST_REEXTRACTION"]},
            "target": {"type": "string", "minLength": 1}, "before_value": {}, "after_value": {},
            "reviewer": {"type": "string", "minLength": 1},
            "reviewed_at": {"type": "string", "format": "date-time", "pattern": "(Z|[+-][0-9]{2}:[0-9]{2})$"},
            "reason": {"type": "string", "minLength": 1},
            "candidate_ids": {"type": "array", "minItems": 1, "items": {"type": "string", "pattern": "^CAND-"}},
            "evidence_ids": {"type": "array", "items": {"type": "string", "pattern": "^EVD-"}},
        },
        "additionalProperties": False,
    }


def snapshot_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://qra.local/contracts/part1/v1/snapshot-manifest.schema.json",
        "type": "object",
        "required": ["snapshot_id", "contract_id", "contract_version", "contract_sha256", "payload_sha256", "created_at", "candidate_ids", "review_ids", "unresolved_issue_ids"],
        "properties": {
            "snapshot_id": {"type": "string", "pattern": "^SNAP-"},
            "contract_id": {"const": CONTRACT_ID}, "contract_version": {"const": VERSION},
            "contract_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "payload_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "created_at": {"type": "string", "format": "date-time"},
            "candidate_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "review_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "unresolved_issue_ids": {"type": "array", "maxItems": 0},
        },
        "additionalProperties": False,
    }


def registries() -> dict[str, Any]:
    units = [
        ("Pa", "pressure_absolute_or_gauge_declared_by_field", "Pa", 1), ("Pa_abs", "pressure_absolute", "Pa_abs", 1),
        ("kPa", "pressure_absolute_or_gauge_declared_by_field", "Pa", 1000), ("MPa", "pressure_absolute_or_gauge_declared_by_field", "MPa", 1),
        ("bar", "pressure_absolute_or_gauge_declared_by_field", "Pa", 100000), ("mm", "length", "mm", 1),
        ("cm", "length", "m", 0.01), ("m", "length", "m", 1), ("km", "length", "km", 1),
        ("°C", "temperature", "K", 1), ("K", "temperature", "K", 1), ("m/s", "speed", "m/s", 1),
        ("km/h", "speed", "m/s", 1 / 3.6), ("V", "electric_potential", "V", 1), ("mV", "electric_potential", "V", 0.001),
        ("Ω·m", "resistivity", "Ω·m", 1), ("ohm*m", "resistivity", "Ω·m", 1), ("per_km_year", "frequency", "per_km_year", 1),
        ("1/(km*year)", "frequency", "per_km_year", 1), ("fraction", "ratio", "fraction", 1), ("%", "ratio", "fraction", 0.01),
    ]
    for symbol in ("1/km", "1/s", "1/year", "count", "day", "deg", "g", "J/kg", "kg/mol", "m/year", "m2", "m3", "m3/d", "mg/kg", "mg/L", "mg/m3", "mm/year", "MPa√m", "person", "s", "year"):
        units.append((symbol, "registered_engineering_quantity", symbol, 1))
    return {
        "unit_registry.json": {
            "registry_id": "qra.part1-units", "version": VERSION,
            "rules": {"pressure_basis_required": True, "ratio_inference_by_value_forbidden": True},
            "units": [
                {"symbol": symbol, "dimension": dimension, "canonical_unit": canonical, "factor": factor, "offset": 273.15 if symbol == "°C" else 0, "allowed_fields": ["*"], "case_sensitive": True}
                for symbol, dimension, canonical, factor in units
            ],
        },
        "term_aliases.json": {
            "alias_set_id": "qra.part1-terms", "version": VERSION,
            "aliases": [
                {"term": "工作压力", "candidate_field_ids": ["pipeline.operating_pressure_mpa"], "auto_confirm": False},
                {"term": "运行压力", "candidate_field_ids": ["pipeline.operating_pressure_mpa"], "auto_confirm": False},
                {"term": "管径", "candidate_field_ids": ["segment.outside_diameter_mm"], "auto_confirm": False},
                {"term": "桩号", "normalization_rule": "CHAINAGE_KM_V1", "auto_confirm": False},
            ],
            "chainage_rule": {"output": "chainage_km", "accepted_examples": {"K12+300": 12.3, "12km+300m": 12.3, "12300m": 12.3}, "ambiguous_is_blocking": True},
        },
        "source_rank.json": {
            "rank_set_id": "qra.part1-source-rank", "version": VERSION,
            "ranks": [
                {"source_type": "MANUAL_APPROVAL", "rank": 100, "requires_approval_ref": True},
                {"source_type": "AS_BUILT_DRAWING", "rank": 90},
                {"source_type": "DESIGN_DOCUMENT", "rank": 80},
                {"source_type": "INSPECTION_RECORD", "rank": 75},
                {"source_type": "OPERATING_RECORD", "rank": 70},
                {"source_type": "APPROVED_PARAMETER_LIBRARY", "rank": 65, "requires_approval_ref": True},
            ],
            "rule": "来源排序只生成建议，不自动解决冲突。",
        },
        "issue_catalog.json": {
            "catalog_id": "qra.part1-issues", "version": VERSION,
            "immutable_meanings": True, "quality_statuses": QUALITY_STATUSES,
            "codes": [
                {"code": "INTAKE.FILE_INVALID", "default_status": "INVALID"},
                {"code": "PARSE.CONTENT_UNREADABLE", "default_status": "INVALID"},
                {"code": "EXTRACT.FIELD_MISSING", "default_status": "MISSING"},
                {"code": "EXTRACT.LOW_CONFIDENCE", "default_status": "LOW_CONFIDENCE"},
                {"code": "NORMALIZE.UNIT_UNSUPPORTED", "default_status": "INVALID"},
                {"code": "NORMALIZE.CHAINAGE_AMBIGUOUS", "default_status": "INVALID"},
                {"code": "FUSION.VALUE_CONFLICT", "default_status": "CONFLICT"},
                {"code": "CONTRACT.SCHEMA.INVALID", "default_status": "INVALID"},
                {"code": "CONTRACT.LEGACY_PROFILE", "default_status": "WARNING"},
                {"code": "GATE.PENDING_REVIEW", "default_status": "PENDING_REVIEW"},
                {"code": "DELIVERY.SNAPSHOT_BLOCKED", "default_status": "INVALID"},
            ],
        },
    }


def examples(output: Path) -> None:
    minimum = {
        "metadata": {"case_id": "MIN-SEG-001", "project_name": "最小管段案例", "data_classification": "SYNTHETIC_TEST_ONLY"},
        "pipeline": {"pipeline_id": "PL-001", "total_length_km": 1.0},
        "segments": [{"segment_id": "SEG-001", "start_km": 0.0, "end_km": 1.0, "length_km": 1.0}],
    }
    write_json(output / "examples" / "minimum-segments.json", minimum)
    shutil.copyfile(FULL_SYNTHETIC, output / "examples" / "full-synthetic.json")
    candidate = {
        "candidate_id": "CAND-0001", "field_id": "pipeline.operating_pressure_mpa",
        "entity": {"entity_type": "PIPELINE", "entity_key": "PL-001"},
        "raw_value": "运行压力 7.8MPa", "parsed_value": 7.8, "normalized_value": 7.8,
        "source_unit": "MPa", "canonical_unit": "MPa", "confidence": 0.98,
        "extraction_method": "STRUCTURED_TABLE", "evidence_ids": ["EVD-0001"],
        "quality_status": "PASS", "review_status": "PENDING", "model_or_rule_versions": {"mapping": "1.0.0"},
    }
    write_json(output / "examples" / "candidate-fields.json", [candidate])
    evidence = {"evidence_id": "EVD-0001", "source_type": "TABLE", "location": {"kind": "TABLE", "file_id": "FILE-001", "sheet_name": "运行工况", "row": 3, "column": 5, "cell_text": "7.8MPa", "coordinate_system": "ROW_COLUMN_1_BASED"}, "excerpt": "运行压力 7.8MPa", "checksum_sha256": "0" * 64}
    write_json(output / "examples" / "evidence.json", [evidence])
    review = {"review_id": "REV-0001", "action": "ACCEPT_CANDIDATE", "target": "pipeline.operating_pressure_mpa", "before_value": None, "after_value": 7.8, "reviewer": "reviewer@example", "reviewed_at": "2026-08-26T10:00:00+08:00", "reason": "与批准运行记录一致", "candidate_ids": ["CAND-0001"], "evidence_ids": ["EVD-0001"]}
    write_json(output / "examples" / "review-decisions.json", [review])
    issue = {"issue_id": "ISS-0001", "code": "EXTRACT.LOW_CONFIDENCE", "quality_status": "LOW_CONFIDENCE", "blocking": True, "target": "pipeline.operating_pressure_mpa", "message": "置信度低于阈值", "candidate_ids": ["CAND-0001"], "evidence_ids": ["EVD-0001"]}
    write_json(output / "examples" / "quality-issues.json", [issue])
    snapshot = {"snapshot_id": "SNAP-0001", "contract_id": CONTRACT_ID, "contract_version": VERSION, "contract_sha256": "0" * 64, "payload_sha256": "1" * 64, "created_at": "2026-08-26T10:00:00+08:00", "candidate_ids": ["CAND-0001"], "review_ids": ["REV-0001"], "unresolved_issue_ids": []}
    write_json(output / "examples" / "snapshot-manifest.json", snapshot)


def invalid_fixtures(output: Path) -> None:
    invalid = PROJECT_ROOT / "tests" / "fixtures" / "contracts_v1" / "invalid"
    minimum = json.loads((output / "examples" / "minimum-segments.json").read_text(encoding="utf-8"))
    write_json(invalid / "qra-missing-segments.json", {"metadata": minimum["metadata"], "pipeline": minimum["pipeline"]})
    duplicate = json.loads(json.dumps(minimum)); duplicate["segments"].append(dict(duplicate["segments"][0])); write_json(invalid / "qra-duplicate-segment.json", duplicate)
    reverse = json.loads(json.dumps(minimum)); reverse["segments"][0].update({"start_km": 2.0, "end_km": 1.0}); write_json(invalid / "qra-reverse-chainage.json", reverse)
    wrong_unit = json.loads(json.dumps(minimum)); wrong_unit["frequency_library"] = {"unit": "per_mile_year"}; write_json(invalid / "qra-wrong-unit.json", wrong_unit)
    probability = json.loads(json.dumps(minimum)); probability["weather_joint_probability"] = [{"weather_id": "W1", "probability": 0.8}, {"weather_id": "W2", "probability": 0.8}]; write_json(invalid / "qra-probability-not-normalized.json", probability)
    unknown = json.loads(json.dumps(minimum)); unknown["temporary_candidates"] = []; write_json(invalid / "qra-unknown-field.json", unknown)
    (invalid / "qra-non-finite.json").write_text('{"metadata":{"case_id":"BAD"},"pipeline":{},"segments":[{"segment_id":"S1","start_km":0,"end_km":1,"length_km":1e999}]}\n', encoding="utf-8")
    candidate = json.loads((output / "examples" / "candidate-fields.json").read_text(encoding="utf-8"))[0]
    candidate["evidence_ids"] = []; write_json(invalid / "candidate-missing-evidence.json", candidate)
    candidate2 = json.loads(json.dumps(candidate)); candidate2["confidence"] = 1.01; write_json(invalid / "candidate-confidence.json", candidate2)
    review = json.loads((output / "examples" / "review-decisions.json").read_text(encoding="utf-8"))[0]
    review["action"] = "EDIT_IN_PLACE"; write_json(invalid / "review-action.json", review)
    write_json(invalid / "evidence-location.json", {"evidence_id": "EVD-BAD", "source_type": "PDF", "location": {"kind": "PDF", "file_id": "F", "page": 0, "bbox": [0, 0, 1, 1], "page_size": [100, 100], "coordinate_system": "PDF_POINTS"}})
    write_json(invalid / "quality-status.json", {"issue_id": "ISS-BAD", "code": "EXTRACT.FIELD_MISSING", "quality_status": "UNKNOWN_STATUS", "blocking": True, "target": "$", "message": "bad"})
    write_json(invalid / "snapshot-unresolved.json", {"snapshot_id": "SNAP-BAD", "contract_id": CONTRACT_ID, "contract_version": VERSION, "contract_sha256": "0" * 64, "payload_sha256": "1" * 64, "created_at": "2026-08-26T10:00:00+08:00", "candidate_ids": [], "review_ids": [], "unresolved_issue_ids": ["ISS-1"]})


def build(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    dictionary, matrix = build_field_dictionary()
    write_json(output / "field_dictionary.json", dictionary)
    write_json(MATRIX_OUTPUT, matrix)
    for name, value in registries().items():
        write_json(output / name, value)
    schemas = {
        "qra-input": qra_input_schema(),
        "candidate-field": candidate_schema(),
        "evidence": evidence_schema(),
        "quality-issue": quality_issue_schema(),
        "review-decision": review_schema(),
        "snapshot-manifest": snapshot_schema(),
    }
    for name, value in schemas.items():
        write_json(output / "schemas" / f"{name}.schema.json", value)
    examples(output)
    invalid_fixtures(output)

    hashed_files = sorted(
        path.relative_to(output).as_posix()
        for path in output.rglob("*.json")
        if path.name != "manifest.json"
    )
    files_sha256 = {
        relative: hashlib.sha256((output / relative).read_bytes()).hexdigest()
        for relative in hashed_files
    }
    manifest = {
        "contract_id": CONTRACT_ID,
        "version": VERSION,
        "status": "TEST_EDITION",
        "released_at": "2026-08-26",
        "compatible_engine_contract": "qra-engine.import/v1",
        "field_dictionary": "field_dictionary.json",
        "schemas": {name: f"schemas/{name}.schema.json" for name in schemas},
        "files_sha256": files_sha256,
        "supersedes": None,
    }
    write_json(output / "manifest.json", manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成第一部分v1合同资源")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    build(args.output.resolve())
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
