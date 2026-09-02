from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import jsonschema
from openpyxl import load_workbook

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE2_ROOT = PROJECT_ROOT / "resources" / "synthetic" / "full-chain-v1" / "stage2"
GENERATED_ROOT = STAGE2_ROOT / "generated"
SCRIPT_PATH = PROJECT_ROOT / "tools" / "build_extended_synthetic_source_packs.py"
SCENARIOS = {
    "S10_CORROSION_DEGRADATION": (
        "c05361b1d1dc865e432c94c1137f6baa8cf64a45dfa3f080ec7c2c4769abc679"
    ),
    "S20_THIRD_PARTY_SURGE": (
        "4a6411d80f33e0ed145a576515d812e42d2201193affd1d528f937560c9703e2"
    ),
    "S30_HIGH_PRESSURE_POPULATION_PEAK": (
        "06ed2cbd5388e8262fd965e83fd2dfad67b79fc309b68b4257c4b7b1f2cc69af"
    ),
    "S40_MITIGATION_PACKAGE": (
        "24fe1b913fa592457823f6806a24459cb3dff17772a89a926307904163649928"
    ),
}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_builder():
    spec = importlib.util.spec_from_file_location("extended_source_pack_builder", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载扩展场景资料包生成器")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extended_scenario_packs_are_current_and_accepted() -> None:
    builder = _load_builder()
    assert builder.check_existing(STAGE2_ROOT) == []
    acceptance = _read_json(STAGE2_ROOT / "extended-scenarios-acceptance.json")
    assert acceptance["status"] == "PASS"
    assert acceptance["scenario_count"] == 4
    assert acceptance["source_document_count"] == 40
    assert acceptance["formal_report_allowed"] is False


def test_each_extended_pack_is_complete_traceable_and_schema_valid() -> None:
    schema = _read_json(STAGE2_ROOT / "schemas" / "source-pack-manifest.schema.json")
    for scenario_id, expected_hash in SCENARIOS.items():
        root = GENERATED_ROOT / f"{scenario_id}_D00_CLEAN"
        manifest = _read_json(root / "source-pack-manifest.json")
        jsonschema.Draft202012Validator(schema).validate(manifest)
        assert manifest["scenario_id"] == scenario_id
        assert manifest["variants"] == []
        sources = [row for row in manifest["files"] if row["role"] == "SOURCE_DOCUMENT"]
        assert len(sources) == 10
        assert all(row["synthetic_marker_verified"] for row in sources)
        for row in manifest["files"]:
            path = root / row["path"]
            assert path.is_file()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]

        expected = _read_json(root / "golden" / "expected-result.json")
        snapshot = _read_json(root / "golden" / "expected-snapshot.json")
        evidence = _read_json(root / "golden" / "evidence-manifest.json")
        assert expected["completed_node_count"] == 11
        assert expected["failed_node_count"] == 0
        assert expected["skipped_node_count"] == 0
        assert expected["numerical_result_sha256"] == expected_hash
        assert expected["formal_report_allowed"] is False
        assert snapshot["scenario_id"] == scenario_id
        assert snapshot["qra_input"]["synthetic_test_edition"]["changes"]
        assert evidence["entry_count"] == 256


def test_workbook_about_sheets_identify_the_correct_scenario() -> None:
    for scenario_id in SCENARIOS:
        path = (
            GENERATED_ROOT
            / f"{scenario_id}_D00_CLEAN"
            / "source-documents"
            / "01_管线与管段台账.xlsx"
        )
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            assert workbook["说明"]["B5"].value == f"{scenario_id} × D00_CLEAN"
        finally:
            workbook.close()
