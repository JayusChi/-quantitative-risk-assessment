"""Build deterministic multi-format source packs for synthetic scenarios S10-S40."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
TOOLS_ROOT = PROJECT_ROOT / "tools"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import build_synthetic_source_pack as base  # noqa: E402
from run_synthetic_test_edition import SCENARIOS, build_scenario_case  # noqa: E402

TARGET_SCENARIOS = tuple(spec for spec in SCENARIOS if spec.scenario_id != "S00_BASELINE")
EXPECTED_HASH_RECORD = base.DEFAULT_OUTPUT_ROOT / "full-contract-result-hashes.json"
DEFAULT_RECORD = base.DEFAULT_OUTPUT_ROOT / "extended-scenarios-acceptance.json"


def _read_expected_hashes() -> dict[str, str]:
    payload = base.read_json(EXPECTED_HASH_RECORD)
    return {
        str(item["scenario_id"]): str(item["numerical_result_sha256"])
        for item in payload["scenarios"]
    }


def _pack_token(scenario_id: str) -> str:
    return scenario_id.split("_", 1)[0]


def _pack_name(scenario_id: str) -> str:
    return f"{scenario_id}_{base.BASE_CONDITION_ID}"


def _pack_id(scenario_id: str) -> str:
    return f"SYNTHETIC-SOURCE-PACK-{_pack_token(scenario_id)}-D00-v1"


def _parameter_bindings(parameter_packs: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "pack_id": pack_id,
            "business_content_sha256": pack["business_content_sha256"],
        }
        for pack_id, pack in sorted(parameter_packs.items())
    ]


def _write_scenario_documents(
    *,
    scenario_id: str,
    case: dict[str, Any],
    by_document: dict[str, list[dict[str, Any]]],
    artifact_source_root: Path,
    qa_output_root: Path,
    node_executable: Path,
    workbook_launcher: Path,
) -> dict[tuple[str, str], dict[str, Any]]:
    artifact_source_root.mkdir(parents=True, exist_ok=True)
    workbook_spec = base.build_workbook_spec(
        case,
        by_document,
        artifact_source_root,
        qa_output_root,
    )
    base.run_workbook_builder(workbook_spec, node_executable, workbook_launcher)
    locations: dict[tuple[str, str], dict[str, Any]] = {}
    for file_name in base.WORKBOOK_DOCUMENTS:
        for row_index, record in enumerate(by_document[file_name], start=5):
            locations[(file_name, record["target_path"])] = {
                "location_type": "xlsx_cell",
                "sheet": "字段证据",
                "cell": f"F{row_index}",
            }
    for file_name in base.CSV_DOCUMENTS:
        csv_locations = base.write_csv_source(
            artifact_source_root / file_name,
            by_document[file_name],
        )
        for target_path, location in csv_locations.items():
            locations[(file_name, target_path)] = location
    document_locations = base.build_docx_source(
        artifact_source_root / "05_缺陷与维修记录.docx",
        by_document["05_缺陷与维修记录.docx"],
        scenario_id=scenario_id,
    )
    for target_path, location in document_locations.items():
        locations[("05_缺陷与维修记录.docx", target_path)] = location
    pdf_locations = base.build_scan_pdf(
        artifact_source_root / "09_现场检查扫描件.pdf",
        by_document["09_现场检查扫描件.pdf"],
        scenario_id=scenario_id,
    )
    for target_path, location in pdf_locations.items():
        locations[("09_现场检查扫描件.pdf", target_path)] = location
    image_locations = base.build_site_image(
        artifact_source_root / "10_现场照片说明.png",
        by_document["10_现场照片说明.png"],
        scenario_id=scenario_id,
    )
    for target_path, location in image_locations.items():
        locations[("10_现场照片说明.png", target_path)] = location
    return locations


def _ground_truth(
    *,
    scenario_id: str,
    case: dict[str, Any],
    matrix: list[dict[str, str]],
    project_fact_records: list[dict[str, Any]],
    parameter_bindings: list[dict[str, str]],
) -> dict[str, Any]:
    payload = {
        "schema_version": base.SCHEMA_VERSION,
        "scenario_id": scenario_id,
        "data_condition_id": base.BASE_CONDITION_ID,
        "data_classification": base.DATA_CLASSIFICATION,
        "source_case": (
            "tests/fixtures/qra_synthetic_case_v1.json + deterministic "
            f"{scenario_id} profile"
        ),
        "qra_input_sha256": base.sha256_value(case),
        "project_facts": project_fact_records,
        "model_parameter_pack_bindings": parameter_bindings,
        "run_assumptions": [
            {
                "field_id": row["field_id"],
                "target_path": row["target_path"],
                "source_document": row["source_document"],
                "value": (
                    None
                    if base.resolve_path(case, row["target_path"]) is base._MISSING
                    else base.resolve_path(case, row["target_path"])
                ),
            }
            for row in matrix
            if row["data_layer"] == "RUN_ASSUMPTION"
        ],
        "expected_snapshot": "expected-snapshot.json",
        "expected_result": "expected-result.json",
    }
    payload["business_content_sha256"] = base.sha256_value(payload)
    return payload


def build_scenario_pack(
    *,
    spec: Any,
    template: dict[str, Any],
    matrix: list[dict[str, str]],
    output_root: Path,
    artifact_output_root: Path,
    node_executable: Path,
    workbook_launcher: Path,
    expected_hash: str,
) -> dict[str, Any]:
    scenario_id = str(spec.scenario_id)
    pack_name = _pack_name(scenario_id)
    pack_id = _pack_id(scenario_id)
    case = build_scenario_case(template, spec)
    base.apply_full_contract_defaults(case, matrix, scenario_id=scenario_id)
    parameter_packs = base.build_parameter_packs(
        matrix,
        case,
        template,
        scenario_id=scenario_id,
    )
    project_fact_records = base.build_project_fact_records(matrix, case)
    by_document = base.records_by_document(project_fact_records)
    artifact_source_root = artifact_output_root / pack_name / "source-documents"
    locations = _write_scenario_documents(
        scenario_id=scenario_id,
        case=case,
        by_document=by_document,
        artifact_source_root=artifact_source_root,
        qa_output_root=artifact_output_root / ".qa" / scenario_id / "workbooks",
        node_executable=node_executable,
        workbook_launcher=workbook_launcher,
    )

    pack_root = output_root / "generated" / pack_name
    source_root = pack_root / "source-documents"
    base.copy_artifacts_to_pack(artifact_source_root, source_root)
    golden_root = pack_root / "golden"
    parameter_root = pack_root / "parameter-packs"
    for parameter_id, parameter_pack in parameter_packs.items():
        base.write_json(parameter_root / f"{parameter_id}.json", parameter_pack)

    evidence_manifest = base.build_evidence_manifest(
        project_fact_records,
        locations,
        scenario_id=scenario_id,
    )
    base.write_json(golden_root / "evidence-manifest.json", evidence_manifest)
    bindings = _parameter_bindings(parameter_packs)
    snapshot_case = copy.deepcopy(case)
    base.materialize_snapshot_parameters(snapshot_case, parameter_packs)
    expected_result = base.build_expected_results(
        snapshot_case,
        golden_root,
        scenario_id=scenario_id,
    )
    qra_input_sha256 = base.sha256_value(snapshot_case)
    snapshot = {
        "schema_version": base.SCHEMA_VERSION,
        "snapshot_id": f"SNAP-SYNTHETIC-{_pack_token(scenario_id)}-D00-v1",
        "data_classification": base.DATA_CLASSIFICATION,
        "scenario_id": scenario_id,
        "data_condition_id": base.BASE_CONDITION_ID,
        "qra_input": snapshot_case,
        "qra_input_sha256": qra_input_sha256,
        "parameter_pack_bindings": bindings,
        "run_assumption_binding": f"run-assumption:{scenario_id}-v1",
        "formal_report_allowed": False,
    }
    snapshot["business_content_sha256"] = base.sha256_value(snapshot)
    base.write_json(golden_root / "expected-snapshot.json", snapshot)
    snapshot_manifest = {
        "snapshot_id": snapshot["snapshot_id"],
        "contract_id": "qra.part1-input",
        "contract_version": "1.0.0",
        "contract_sha256": base.sha256_bytes(
            (
                PROJECT_ROOT
                / "resources"
                / "contracts"
                / "part1"
                / "v1"
                / "manifest.json"
            ).read_bytes()
        ),
        "payload_sha256": qra_input_sha256,
        "created_at": "2026-08-26T10:00:00+08:00",
        "candidate_ids": [],
        "review_ids": [],
        "unresolved_issue_ids": [],
    }
    base.write_json(golden_root / "expected-snapshot-manifest.json", snapshot_manifest)
    ground_truth = _ground_truth(
        scenario_id=scenario_id,
        case=snapshot_case,
        matrix=matrix,
        project_fact_records=project_fact_records,
        parameter_bindings=bindings,
    )
    base.write_json(golden_root / "ground-truth.json", ground_truth)

    source_hashes = {
        file_name: base.sha256_value(
            [
                {
                    "field_id": record["field_id"],
                    "target_path": record["target_path"],
                    "value": record["value"],
                    "unit": record["target_unit"] or record["source_unit"],
                }
                for record in by_document[file_name]
            ]
        )
        for file_name in base.SOURCE_DOCUMENTS
    }
    components = {
        "source_documents": source_hashes,
        "parameter_packs": {
            parameter_id: parameter_pack["business_content_sha256"]
            for parameter_id, parameter_pack in sorted(parameter_packs.items())
        },
        "ground_truth": ground_truth["business_content_sha256"],
        "evidence_manifest": evidence_manifest["business_content_sha256"],
        "expected_snapshot": snapshot["business_content_sha256"],
        "expected_result": expected_result["business_content_sha256"],
        "variants": {},
    }
    business_hash = base.sha256_value(components)
    file_entries = [
        base.file_entry(
            source_root / file_name,
            pack_root,
            "SOURCE_DOCUMENT",
            source_hashes[file_name],
        )
        for file_name in base.SOURCE_DOCUMENTS
    ]
    for parameter_id, parameter_pack in sorted(parameter_packs.items()):
        file_entries.append(
            base.file_entry(
                parameter_root / f"{parameter_id}.json",
                pack_root,
                "PARAMETER_PACK",
                parameter_pack["business_content_sha256"],
            )
        )
    for file_name, logical_hash in (
        ("ground-truth.json", ground_truth["business_content_sha256"]),
        ("evidence-manifest.json", evidence_manifest["business_content_sha256"]),
        ("expected-snapshot.json", snapshot["business_content_sha256"]),
        ("expected-result.json", expected_result["business_content_sha256"]),
    ):
        file_entries.append(
            base.file_entry(golden_root / file_name, pack_root, "GOLDEN_ANSWER", logical_hash)
        )
    byte_manifest_sha256 = base.sha256_value(
        [
            {"path": row["path"], "sha256": row["sha256"], "size_bytes": row["size_bytes"]}
            for row in file_entries
        ]
    )
    manifest = {
        "schema_version": base.SCHEMA_VERSION,
        "pack_id": pack_id,
        "scenario_id": scenario_id,
        "data_condition_id": base.BASE_CONDITION_ID,
        "data_classification": base.DATA_CLASSIFICATION,
        "generator": {
            "id": "build_synthetic_source_pack.py",
            "version": base.GENERATOR_VERSION,
            "deterministic": True,
            "random_seed": None,
        },
        "files": file_entries,
        "parameter_packs": sorted(parameter_packs),
        "golden": {
            "ground_truth": "golden/ground-truth.json",
            "evidence_manifest": "golden/evidence-manifest.json",
            "expected_snapshot": "golden/expected-snapshot.json",
            "expected_result": "golden/expected-result.json",
            "numerical_result_sha256": expected_result["numerical_result_sha256"],
        },
        "variants": [],
        "business_content_sha256": business_hash,
        "byte_manifest_sha256": byte_manifest_sha256,
    }
    base.write_json(pack_root / "source-pack-manifest.json", manifest)
    archive = output_root / "generated" / f"{pack_name}.zip"
    base.write_deterministic_zip(pack_root, archive)
    shutil.copyfile(archive, artifact_output_root / archive.name)

    critical = [row for row in project_fact_records if row["criticality"] == "BLOCKING"]
    evidence_paths = {row["target_path"] for row in evidence_manifest["entries"]}
    checks = {
        "ten_source_documents": len(
            [row for row in file_entries if row["role"] == "SOURCE_DOCUMENT"]
        )
        == 10,
        "all_sources_marked_synthetic": all(
            row["synthetic_marker_verified"]
            for row in file_entries
            if row["role"] == "SOURCE_DOCUMENT"
        ),
        "all_critical_facts_have_evidence": all(
            row["target_path"] in evidence_paths for row in critical
        ),
        "eleven_nodes_completed": expected_result["status"] == "PASS"
        and expected_result["completed_node_count"] == 11,
        "numerical_hash_matches_baseline": expected_result["numerical_result_sha256"]
        == expected_hash,
        "formal_report_blocked": expected_result["formal_report_allowed"] is False,
    }
    return {
        "scenario_id": scenario_id,
        "pack_name": pack_name,
        "pack_id": pack_id,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "source_document_count": 10,
        "evidence_entry_count": evidence_manifest["entry_count"],
        "completed_node_count": expected_result["completed_node_count"],
        "numerical_result_sha256": expected_result["numerical_result_sha256"],
        "business_content_sha256": business_hash,
        "archive": archive.relative_to(output_root).as_posix(),
    }


def check_existing(output_root: Path) -> list[str]:
    expected_hashes = _read_expected_hashes()
    errors = []
    for spec in TARGET_SCENARIOS:
        pack_name = _pack_name(spec.scenario_id)
        root = output_root / "generated" / pack_name
        manifest_path = root / "source-pack-manifest.json"
        if not manifest_path.is_file():
            errors.append(f"缺少场景资料包：{spec.scenario_id}")
            continue
        manifest = base.read_json(manifest_path)
        if manifest.get("scenario_id") != spec.scenario_id:
            errors.append(f"场景编号错误：{spec.scenario_id}")
        if manifest.get("golden", {}).get("numerical_result_sha256") != expected_hashes[
            spec.scenario_id
        ]:
            errors.append(f"数值哈希漂移：{spec.scenario_id}")
        source_entries = [
            row for row in manifest.get("files", []) if row.get("role") == "SOURCE_DOCUMENT"
        ]
        if len(source_entries) != 10:
            errors.append(f"原始资料数量不是10：{spec.scenario_id}")
        for row in manifest.get("files", []):
            path = root / str(row.get("path") or "")
            if not path.is_file() or base.sha256_bytes(path.read_bytes()) != row.get("sha256"):
                errors.append(f"文件哈希无效：{spec.scenario_id}/{row.get('path')}")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=base.DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--artifact-output-root",
        type=Path,
        default=base.DEFAULT_ARTIFACT_OUTPUT_ROOT,
    )
    parser.add_argument("--record", type=Path, default=DEFAULT_RECORD)
    parser.add_argument("--node-executable", type=Path)
    parser.add_argument(
        "--workbook-launcher",
        type=Path,
        default=base.DEFAULT_WORKBOOK_LAUNCHER,
    )
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_root = args.output_root.resolve()
    if args.check:
        errors = check_existing(output_root)
        print(
            json.dumps(
                {"status": "PASS" if not errors else "FAIL", "errors": errors},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if not errors else 1
    if args.node_executable is None:
        raise SystemExit("完整生成必须提供 --node-executable")
    template = base.read_json(base.TEMPLATE_CASE)
    matrix = base.read_matrix()
    expected_hashes = _read_expected_hashes()
    results = [
        build_scenario_pack(
            spec=spec,
            template=template,
            matrix=matrix,
            output_root=output_root,
            artifact_output_root=args.artifact_output_root.resolve(),
            node_executable=args.node_executable.resolve(),
            workbook_launcher=args.workbook_launcher.resolve(),
            expected_hash=expected_hashes[spec.scenario_id],
        )
        for spec in TARGET_SCENARIOS
    ]
    record = {
        "schema_version": "1.0.0",
        "status": "PASS" if all(row["status"] == "PASS" for row in results) else "FAIL",
        "scenario_count": len(results),
        "source_document_count": sum(row["source_document_count"] for row in results),
        "scenarios": results,
        "formal_report_allowed": False,
    }
    base.write_json(args.record.resolve(), record)
    print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
