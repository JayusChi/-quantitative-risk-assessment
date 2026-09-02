from __future__ import annotations

import hashlib
import importlib.util
import json
import unittest
import zipfile
from pathlib import Path

import jsonschema

from db_qra.file_intake import intake_files

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "tools" / "build_synthetic_source_pack.py"
STAGE2_ROOT = PROJECT_ROOT / "resources" / "synthetic" / "full-chain-v1" / "stage2"
D00_ROOT = STAGE2_ROOT / "generated" / "S00_BASELINE_D00_CLEAN"
VARIANT_ROOT = STAGE2_ROOT / "generated" / "variants"
EXPECTED_NUMERICAL_HASH = "2d351acfc98cb73df38e4221e8baf427edd5fdd4b6788992d8c5480246d25286"
VARIANTS = {
    "D10_CONFLICT",
    "D20_MISSING",
    "D30_LOW_QUALITY_SCAN",
    "D40_OVERSIZED_IMAGE",
    "D50_PROMPT_INJECTION",
    "D60_DUPLICATE_VERSION",
    "D70_UNIT_ANOMALY",
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_builder():
    spec = importlib.util.spec_from_file_location("full_chain_stage2_builder", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载阶段2资料包生成器")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FullChainStage2SourcePackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.builder = load_builder()
        cls.acceptance = read_json(STAGE2_ROOT / "stage2-acceptance.json")
        cls.manifest = read_json(D00_ROOT / "source-pack-manifest.json")
        cls.ground_truth = read_json(D00_ROOT / "golden" / "ground-truth.json")
        cls.evidence = read_json(D00_ROOT / "golden" / "evidence-manifest.json")

    def test_committed_stage2_artifacts_are_current(self) -> None:
        self.assertEqual(self.builder.check_existing(STAGE2_ROOT), [])
        self.assertTrue(self.acceptance["passed"])
        self.assertEqual(self.acceptance["status"], "S2_SYNTHETIC_SOURCE_PACK_ACCEPTED")
        determinism = read_json(STAGE2_ROOT / "determinism-verification.json")
        self.assertTrue(determinism["business_hash_equal"])
        self.assertTrue(determinism["numerical_hash_equal"])
        self.assertEqual(
            determinism["business_content_sha256"],
            self.acceptance["business_content_sha256"],
        )
        self.assertEqual(
            determinism["numerical_result_sha256"],
            self.acceptance["numerical_result_sha256"],
        )

    def test_manifest_and_parameter_packs_validate_against_schemas(self) -> None:
        manifest_schema = read_json(STAGE2_ROOT / "schemas" / "source-pack-manifest.schema.json")
        parameter_schema = read_json(STAGE2_ROOT / "schemas" / "parameter-pack.schema.json")
        jsonschema.Draft202012Validator(manifest_schema).validate(self.manifest)
        for path in sorted((D00_ROOT / "parameter-packs").glob("*.json")):
            with self.subTest(path=path.name):
                jsonschema.Draft202012Validator(parameter_schema).validate(read_json(path))

    def test_all_planned_source_files_are_marked_and_byte_hashes_match(self) -> None:
        source_entries = [
            entry for entry in self.manifest["files"] if entry["role"] == "SOURCE_DOCUMENT"
        ]
        self.assertEqual(len(source_entries), 10)
        self.assertEqual(self.acceptance["counts"]["source_document_count"], 10)
        for entry in self.manifest["files"]:
            with self.subTest(path=entry["path"]):
                path = D00_ROOT / entry["path"]
                self.assertTrue(path.is_file())
                self.assertEqual(sha256_file(path), entry["sha256"])
                self.assertTrue(entry["synthetic_marker_verified"])

    def test_critical_project_facts_have_matching_evidence(self) -> None:
        critical = {
            row["target_path"]: row
            for row in self.ground_truth["project_facts"]
            if row["criticality"] == "BLOCKING"
        }
        evidence = {row["target_path"]: row for row in self.evidence["entries"]}
        self.assertEqual(len(critical), 25)
        self.assertTrue(set(critical) <= set(evidence))
        for target_path, fact in critical.items():
            with self.subTest(target_path=target_path):
                self.assertEqual(fact["status"], "PRESENT")
                self.assertEqual(evidence[target_path]["value_sha256"], fact["value_sha256"])
                self.assertTrue(evidence[target_path]["location"])

    def test_all_model_parameters_are_in_six_versioned_packs(self) -> None:
        packs = [read_json(path) for path in sorted((D00_ROOT / "parameter-packs").glob("*.json"))]
        self.assertEqual(len(packs), 6)
        self.assertEqual(sum(len(pack["parameters"]) for pack in packs), 68)
        self.assertTrue(all(pack["version"] == "1.0.0" for pack in packs))
        self.assertTrue(all(pack["data_classification"] == "SYNTHETIC_TEST_ONLY" for pack in packs))

    def test_expected_result_matches_full_contract_baseline_and_has_all_nodes(self) -> None:
        expected = read_json(D00_ROOT / "golden" / "expected-result.json")
        node_dir = D00_ROOT / "golden" / "expected-results" / "nodes"
        self.assertEqual(expected["status"], "PASS")
        self.assertEqual(expected["completed_node_count"], 11)
        self.assertEqual(len(list(node_dir.glob("*.json"))), 11)
        self.assertEqual(expected["numerical_result_sha256"], EXPECTED_NUMERICAL_HASH)

    def test_all_project_facts_and_run_assumptions_have_explicit_paths(self) -> None:
        statuses = {
            status: sum(
                row["status"] == status for row in self.ground_truth["project_facts"]
            )
            for status in {"PRESENT", "COLLECTION_DERIVED", "MISSING"}
        }
        self.assertEqual(statuses["PRESENT"], 256)
        self.assertEqual(statuses["COLLECTION_DERIVED"], 4)
        self.assertEqual(statuses["MISSING"], 0)
        self.assertEqual(len(self.ground_truth["run_assumptions"]), 33)
        self.assertTrue(
            all(row["value"] is not None for row in self.ground_truth["run_assumptions"])
        )
        snapshot = read_json(D00_ROOT / "golden" / "expected-snapshot.json")
        self.assertIn("risk_matrix_criteria", snapshot["qra_input"])
        self.assertEqual(len(snapshot["qra_input"]["raw_data_categories"]), 10)

    def test_source_pack_zip_contains_the_complete_d00_tree(self) -> None:
        archive_path = STAGE2_ROOT / self.acceptance["source_pack_archive"]
        with zipfile.ZipFile(archive_path) as archive:
            members = set(archive.namelist())
            self.assertTrue(
                all(
                    not Path(member).is_absolute() and ".." not in Path(member).parts
                    for member in members
                )
            )
            for entry in self.manifest["files"]:
                self.assertIn(entry["path"], members)
            self.assertIn("source-pack-manifest.json", members)

    def test_all_required_condition_variants_are_explicit(self) -> None:
        manifests = {
            path.parent.name: read_json(path)
            for path in VARIANT_ROOT.glob("*/variant-manifest.json")
        }
        self.assertEqual(set(manifests), VARIANTS)
        self.assertEqual(
            manifests["D10_CONFLICT"]["injected_conditions"][0]["target_path"],
            "pipeline.operating_pressure_mpa",
        )
        self.assertIn(
            "source-documents/07_人口和敏感受体.xlsx",
            manifests["D20_MISSING"]["remove_files"],
        )
        self.assertEqual(
            manifests["D50_PROMPT_INJECTION"]["injected_conditions"][0][
                "ground_truth_binding_count"
            ],
            0,
        )
        self.assertEqual(
            manifests["D60_DUPLICATE_VERSION"]["injected_conditions"][0]["relationship"],
            "EXACT_CONTENT_DUPLICATE_NEW_VERSION",
        )
        self.assertEqual(len(manifests["D70_UNIT_ANOMALY"]["injected_conditions"]), 3)

    def test_low_quality_pdf_and_oversized_image_are_safely_received(self) -> None:
        paths = [
            VARIANT_ROOT
            / "D30_LOW_QUALITY_SCAN"
            / "overlay"
            / "09_现场检查扫描件.pdf",
            VARIANT_ROOT
            / "D40_OVERSIZED_IMAGE"
            / "overlay"
            / "10_现场照片说明.png",
        ]
        for path in paths:
            with self.subTest(path=path.name):
                batch = intake_files([{"file_name": path.name, "content": path.read_bytes()}])
                self.assertEqual(batch.ready_count, 1)
                self.assertEqual(batch.quarantined_count, 0)
                self.assertEqual(batch.sources[0]["security_status"], "READY_FOR_PARSE")


if __name__ == "__main__":
    unittest.main()
