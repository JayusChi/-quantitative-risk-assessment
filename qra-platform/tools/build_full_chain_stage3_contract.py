"""Build the versioned synthetic stage-3 extraction and replay contracts."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

STAGE1_ROOT = PROJECT_ROOT / "resources" / "synthetic" / "full-chain-v1"
STAGE2_ROOT = STAGE1_ROOT / "stage2" / "generated"
D00_ROOT = STAGE2_ROOT / "S00_BASELINE_D00_CLEAN"
DEFAULT_OUTPUT_ROOT = STAGE1_ROOT / "stage3"
CONTRACT_VERSION = "1.0.0"
CONTRACT_ID = "qra.synthetic-full-chain.stage3"


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def read_matrix() -> list[dict[str, str]]:
    path = STAGE1_ROOT / "field-source-node-matrix.csv"
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _pdf_crop_hash(path: Path, location: dict[str, Any]) -> str:
    from pypdf import PdfReader

    page = PdfReader(path).pages[int(location["page"]) - 1]
    images = list(page.images)
    if len(images) != 1:
        raise ValueError(f"确定性PDF重放要求每页只有一个扫描图像：{path.name}")
    image = images[0].image.convert("RGB")
    bbox = tuple(int(value) for value in location["bbox_pixels"])
    crop = image.crop(bbox)
    payload = b"|".join(
        (
            str(crop.width).encode(),
            str(crop.height).encode(),
            crop.mode.encode(),
            crop.tobytes(),
        )
    )
    return hashlib.sha256(payload).hexdigest()


def _image_crop_hash(path: Path, location: dict[str, Any]) -> str:
    from PIL import Image

    with Image.open(path) as source:
        image = source.convert("RGB")
        crop = image.crop(tuple(int(value) for value in location["bbox_pixels"]))
    payload = b"|".join(
        (
            str(crop.width).encode(),
            str(crop.height).encode(),
            crop.mode.encode(),
            crop.tobytes(),
        )
    )
    return hashlib.sha256(payload).hexdigest()


def _enum_aliases(field_id: str) -> dict[str, Any]:
    aliases: dict[str, dict[str, str]] = {
        "assessment.coordinate_system": {
            "local cartesian metre": "LOCAL_CARTESIAN_METRE",
            "本地笛卡尔米制": "LOCAL_CARTESIAN_METRE",
        },
        "pipeline.service": {
            "natural gas": "NATURAL_GAS",
            "天然气": "NATURAL_GAS",
        },
        "engineering_indicator.external_corrosion.coating_type": {
            "三层聚乙烯": "3LPE",
            "3pe": "3LPE",
        },
        "weather_case.time_period": {"昼间": "day", "夜间": "night"},
    }
    return aliases.get(field_id, {})


def _unit_rule(source_unit: str | None, target_unit: str | None) -> dict[str, Any]:
    source = source_unit or target_unit
    target = target_unit or source_unit
    conversions = {
        ("bar", "MPa"): {"scale": 0.1, "offset": 0.0},
        ("kPa", "MPa"): {"scale": 0.001, "offset": 0.0},
        ("degC", "K"): {"scale": 1.0, "offset": 273.15},
        ("m", "km"): {"scale": 0.001, "offset": 0.0},
    }
    rule = conversions.get((source, target), {"scale": 1.0, "offset": 0.0})
    return {"source_unit": source, "target_unit": target, **rule}


def build_contract(output_root: Path) -> dict[str, Any]:
    ground_truth = read_json(D00_ROOT / "golden" / "ground-truth.json")
    evidence_manifest = read_json(D00_ROOT / "golden" / "evidence-manifest.json")
    expected_snapshot = read_json(D00_ROOT / "golden" / "expected-snapshot.json")
    source_manifest = read_json(D00_ROOT / "source-pack-manifest.json")
    matrix = read_matrix()
    matrix_by_field = {row["field_id"]: row for row in matrix}
    facts_by_path = {
        row["target_path"]: row
        for row in ground_truth["project_facts"]
        if row["status"] == "PRESENT"
    }

    fields = []
    for entry in evidence_manifest["entries"]:
        matrix_row = matrix_by_field[entry["field_id"]]
        fields.append(
            {
                "field_id": entry["field_id"],
                "business_name": entry["business_name"],
                "target_path": entry["target_path"],
                "aliases": sorted(
                    {
                        entry["field_id"],
                        entry["business_name"],
                        entry["target_path"],
                    }
                ),
                "criticality": entry["criticality"],
                "value_type": matrix_row["value_type"],
                "cardinality": matrix_row["cardinality"],
                "evidence_required": True,
                "missing_policy": matrix_row["missing_policy"],
                "conflict_policy": matrix_row["conflict_policy"],
                "normalization": {
                    "unit": _unit_rule(entry.get("source_unit"), entry.get("target_unit")),
                    "enum_aliases": _enum_aliases(entry["field_id"]),
                    "blank_policy": "MISSING_NEVER_ZERO",
                },
                "affected_nodes": [
                    value for value in matrix_row["target_nodes"].split("|") if value
                ],
                "review_group": entry["target_path"].split(".", 1)[0],
                "expected_value_sha256": entry["value_sha256"],
                "source_document": entry["source_document"],
                "location": entry["location"],
            }
        )
    fields.sort(key=lambda row: (row["source_document"], row["target_path"]))

    mapping = {
        "schema_version": "1.0.0",
        "contract_id": CONTRACT_ID,
        "contract_version": CONTRACT_VERSION,
        "mapping_id": "synthetic-full-chain-s00-v1",
        "mapping_version": CONTRACT_VERSION,
        "data_classification": "SYNTHETIC_TEST_ONLY",
        "source_pack_business_sha256": source_manifest["business_content_sha256"],
        "field_count": len(fields),
        "fields": fields,
        "assembly_boundary": {
            "PROJECT_FACT": "candidate-plus-evidence",
            "MODEL_PARAMETER": "versioned-parameter-pack-without-file-evidence",
            "RUN_ASSUMPTION": "versioned-run-assumption-binding",
            "undeclared_default_policy": "FORBIDDEN",
        },
        "review_workbench": {
            "groups": [
                {"id": "assessment", "label": "评价设置"},
                {"id": "pipeline", "label": "管线与工况"},
                {"id": "segments", "label": "管段对齐"},
                {"id": "population_cells", "label": "人口与敏感受体"},
                {"id": "weather_joint_probability", "label": "气象联合概率"},
                {"id": "engineering_indicators", "label": "工程指标与治理"},
            ],
            "show_affected_nodes": True,
            "show_original_and_normalized_value": True,
            "manual_edit_audit_fields": [
                "original_value",
                "new_value",
                "reason",
                "reviewer",
                "reviewed_at",
            ],
        },
        "security_policy": {
            "document_content_trust": "UNTRUSTED",
            "model_may_change_workflow": False,
            "model_may_change_contract": False,
            "model_may_change_gate": False,
            "model_tools_allowed": [],
        },
    }
    mapping["business_content_sha256"] = sha256_value(mapping)

    replay_items = []
    for field in fields:
        location_type = field["location"]["location_type"]
        if location_type not in {"pdf_page_bbox", "image_bbox"}:
            continue
        source_path = D00_ROOT / "source-documents" / field["source_document"]
        fact = facts_by_path[field["target_path"]]
        crop_hash = (
            _pdf_crop_hash(source_path, field["location"])
            if location_type == "pdf_page_bbox"
            else _image_crop_hash(source_path, field["location"])
        )
        replay_items.append(
            {
                "field_id": field["field_id"],
                "target_path": field["target_path"],
                "source_document": field["source_document"],
                "source_file_sha256": sha256_file(source_path),
                "location": copy.deepcopy(field["location"]),
                "crop_pixel_sha256": crop_hash,
                "value": fact["value"],
                "value_sha256": fact["value_sha256"],
                "confidence": 1.0,
            }
        )
    replay = {
        "schema_version": "1.0.0",
        "provider_id": "synthetic-deterministic-replay",
        "provider_version": CONTRACT_VERSION,
        "binding_policy": "source-file-sha256+page-or-image+bbox+crop-pixel-sha256",
        "items": replay_items,
    }
    replay["business_content_sha256"] = sha256_value(replay)

    run_assumptions = {
        "schema_version": "1.0.0",
        "binding_id": "run-assumption:S00_BASELINE-v1",
        "version": CONTRACT_VERSION,
        "values": [item for item in ground_truth["run_assumptions"] if item["value"] is not None]
        + [
            {
                "field_id": "synthetic_test_edition",
                "target_path": "synthetic_test_edition",
                "source_document": "system-metadata:SYNTHETIC_TEST_ONLY-v1",
                "value": expected_snapshot["qra_input"]["synthetic_test_edition"],
            }
        ],
    }
    run_assumptions["business_content_sha256"] = sha256_value(run_assumptions)

    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://qra.local/synthetic/full-chain-v1/stage3/extraction-contract.schema.json",
        "title": "Stage 3 evidence-bound candidate",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "candidate_id",
            "field_id",
            "target_path",
            "raw_value",
            "normalized_value",
            "confidence",
            "evidence_ids",
            "source_kind",
        ],
        "properties": {
            "candidate_id": {"type": "string", "pattern": "^CAND-"},
            "field_id": {"enum": sorted({field["field_id"] for field in fields})},
            "target_path": {"type": "string"},
            "raw_value": {},
            "normalized_value": {},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_ids": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "pattern": "^EVID-"},
            },
            "source_kind": {"const": "PROJECT_FACT"},
        },
    }

    providers = {
        "schema_version": "1.0.0",
        "deterministic": {
            "provider_id": replay["provider_id"],
            "provider_version": replay["provider_version"],
            "network_allowed": False,
            "temperature": 0,
            "replay_contract": "deterministic-replay.json",
        },
        "online_demo": {
            "provider_id": "aliyun-bailian-openai",
            "model_env": "QRA_EXTRACTION_MODEL_VERSION",
            "api_key_env": "QRA_ALIYUN_API_KEY",
            "base_url_env": "QRA_ALIYUN_OPENAI_BASE_URL",
            "external_sharing_requires_explicit_opt_in": True,
            "structured_output_required": True,
            "tools_allowed": [],
            "temperature": 0,
            "failure_policy": "PRESERVE_PARSED_ARTIFACTS_AND_ROUTE_TO_HUMAN",
        },
    }

    expected_binding = {
        "schema_version": "1.0.0",
        "expected_snapshot_path": (
            "../stage2/generated/S00_BASELINE_D00_CLEAN/"
            "golden/expected-snapshot.json"
        ),
        "expected_snapshot_sha256": sha256_file(D00_ROOT / "golden" / "expected-snapshot.json"),
        "expected_business_content_sha256": expected_snapshot["business_content_sha256"],
        "expected_qra_input_sha256": expected_snapshot["qra_input_sha256"],
        "critical_project_fact_count": sum(
            1 for field in fields if field["criticality"] == "BLOCKING"
        ),
        "project_fact_count": len(fields),
        "snapshot_materialized_parameter_paths": sorted(
            {
                item["target_path"]
                for pack_path in (D00_ROOT / "parameter-packs").glob("*.json")
                for item in read_json(pack_path)["parameters"]
                if (
                    "." not in item["target_path"]
                    or item["target_path"] == "system_parameters.adaptive_evidence_qra"
                )
                and item["target_path"].split(".", 1)[0]
                in expected_snapshot["qra_input"]
            }
        ),
    }

    structural_assembly = {
        "schema_version": "1.0.0",
        "contract_id": "qra.synthetic-full-chain.stage3.structural-assembly",
        "contract_version": CONTRACT_VERSION,
        "fields": [
            {
                "field_id": "engineering_indicators",
                "target_path": "engineering_indicators",
                "assembly_method": "DERIVE_OBJECT_FROM_CONSUMED_CHILDREN",
                "child_path_prefixes": ["engineering_indicators."],
            },
            {
                "field_id": "engineering_indicators.observations_by_archetype",
                "target_path": "engineering_indicators.observations_by_archetype",
                "assembly_method": "DERIVE_ARCHETYPE_OBJECT_FROM_AGGREGATE_EVIDENCE",
                "child_path_prefixes": [
                    "engineering_indicators.observations_by_segment.*."
                ],
            },
            {
                "field_id": "engineering_indicators.observations_by_segment",
                "target_path": "engineering_indicators.observations_by_segment",
                "assembly_method": "DERIVE_SEGMENT_OVERRIDE_OBJECT_FROM_AGGREGATE_EVIDENCE",
                "child_path_prefixes": [
                    "engineering_indicators.observations_by_segment.*."
                ],
            },
            {
                "field_id": "raw_data_categories",
                "target_path": "raw_data_categories",
                "assembly_method": "DERIVE_FROM_ACCEPTED_SOURCE_DOCUMENT_INVENTORY",
                "source_documents": sorted(
                    item["path"].removeprefix("source-documents/")
                    for item in source_manifest["files"]
                    if item["role"] == "SOURCE_DOCUMENT"
                ),
                "materialized_value": copy.deepcopy(
                    expected_snapshot["qra_input"]["raw_data_categories"]
                ),
            },
        ],
    }
    structural_assembly["business_content_sha256"] = sha256_value(structural_assembly)

    write_json(output_root / "synthetic-mapping.v1.json", mapping)
    write_json(output_root / "extraction-contract.schema.json", schema)
    write_json(output_root / "deterministic-replay.json", replay)
    write_json(output_root / "run-assumptions.v1.json", run_assumptions)
    write_json(output_root / "provider-configs.v1.json", providers)
    write_json(output_root / "expected-binding.v1.json", expected_binding)
    write_json(output_root / "structural-assembly.v1.json", structural_assembly)
    manifest = {
        "schema_version": "1.0.0",
        "stage": 3,
        "contract_id": CONTRACT_ID,
        "contract_version": CONTRACT_VERSION,
        "files": {
            path.name: sha256_file(path)
            for path in sorted(output_root.glob("*.json"))
            if path.name not in {"stage3-contract-manifest.json", "stage3-acceptance.json"}
        },
    }
    manifest["business_content_sha256"] = sha256_value(manifest)
    write_json(output_root / "stage3-contract-manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    manifest = build_contract(args.output_root.resolve())
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
