from __future__ import annotations

import csv
import importlib.util
import io
import json
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "tools" / "build_full_chain_stage1_contract.py"
OUTPUT_ROOT = PROJECT_ROOT / "resources" / "synthetic" / "full-chain-v1"


def load_builder():
    spec = importlib.util.spec_from_file_location("full_chain_stage1_builder", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载阶段1合同生成器")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FullChainStage1ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.builder = load_builder()
        cls.artifacts = cls.builder.build_artifacts()
        matrix_bytes = cls.artifacts[OUTPUT_ROOT / "field-source-node-matrix.csv"]
        cls.rows = list(csv.DictReader(io.StringIO(matrix_bytes.decode("utf-8-sig"))))
        cls.acceptance = json.loads(cls.artifacts[OUTPUT_ROOT / "stage1-acceptance.json"])
        cls.node_contract = json.loads(cls.artifacts[OUTPUT_ROOT / "node-input-contract.json"])

    def test_committed_stage1_artifacts_are_current(self) -> None:
        self.assertEqual(self.builder.check_artifacts(self.artifacts), [])

    def test_matrix_has_required_columns_unique_paths_and_fields(self) -> None:
        self.assertEqual(tuple(self.rows[0]), self.builder.MATRIX_COLUMNS)
        self.assertEqual(len({row["target_path"] for row in self.rows}), len(self.rows))
        self.assertEqual(len({row["field_id"] for row in self.rows}), len(self.rows))

    def test_all_node_required_inputs_have_registered_sources(self) -> None:
        by_path = {row["target_path"]: row for row in self.rows}
        self.assertEqual(len(self.node_contract["nodes"]), 11)
        for node in self.node_contract["nodes"]:
            for item in node["required_inputs"]:
                with self.subTest(node=node["node_id"], path=item["path"]):
                    row = by_path[item["path"]]
                    self.assertTrue(row["source_document"])
                    self.assertEqual(row["criticality"], "BLOCKING")

    def test_three_data_layers_have_controlled_sources(self) -> None:
        for row in self.rows:
            with self.subTest(path=row["target_path"]):
                if row["data_layer"] == "PROJECT_FACT":
                    self.assertEqual(row["evidence_required"], "true")
                    documents = set(row["source_document"].split("|"))
                    self.assertTrue(documents <= set(self.builder.SOURCE_DOCUMENTS.values()))
                    self.assertTrue(
                        all(
                            Path(item).suffix.casefold() in self.builder.RAW_DOCUMENT_SUFFIXES
                            for item in documents
                        )
                    )
                elif row["data_layer"] == "MODEL_PARAMETER":
                    self.assertTrue(row["source_document"].startswith("parameter-pack:"))
                    parameter_pack = row["source_document"].removeprefix("parameter-pack:")
                    self.assertIn(parameter_pack, self.builder.VERSIONED_PARAMETER_PACKS)
                    self.assertIn("-v", parameter_pack)
                    self.assertEqual(row["evidence_required"], "false")
                else:
                    self.assertTrue(
                        row["source_document"].startswith(
                            ("run-assumption:", "system-generated:", "project-wizard:")
                        )
                    )

    def test_no_silent_zero_and_stage1_freeze_gaps_are_explicit(self) -> None:
        self.assertTrue(self.acceptance["passed"])
        self.assertEqual(self.acceptance["status"], "S1_FULL_CONTRACT_MAPPED")
        self.assertTrue(self.acceptance["checks"]["no_unregistered_silent_zero_default"])
        self.assertTrue(
            self.acceptance["checks"]["stage1_freeze_implementation_gaps_are_classified"]
        )
        self.assertNotIn("DEFAULT_ZERO", "\n".join(row["missing_policy"] for row in self.rows))


if __name__ == "__main__":
    unittest.main()
