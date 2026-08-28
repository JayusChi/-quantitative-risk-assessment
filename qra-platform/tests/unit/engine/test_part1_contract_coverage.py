from __future__ import annotations

import json
import unittest
from pathlib import Path

from qra_engine.dynamic import dynamic_node_catalog, plan_dynamic_flow
from qra_engine.validation import validate_import_contract


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = PROJECT_ROOT / "resources" / "contracts" / "part1" / "v1"
MATRIX_PATH = (
    PROJECT_ROOT / "tests" / "fixtures" / "contracts_v1" / "expected-node-field-matrix.json"
)


class Part1ContractCoverageTests(unittest.TestCase):
    def test_dynamic_explicit_requirements_match_reviewed_matrix_and_dictionary(self) -> None:
        matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        expected = {
            row["node_id"]: row["explicit_required_inputs"] for row in matrix["nodes"]
        }
        current = {
            row["node_id"]: row["required_inputs"]
            for row in dynamic_node_catalog()["nodes"]
        }
        self.assertEqual(current, expected)
        dictionary = json.loads(
            (CONTRACT_ROOT / "field_dictionary.json").read_text(encoding="utf-8")
        )
        paths = {row["target_path"] for row in dictionary["fields"]}
        missing = {
            requirement["path"]
            for requirements in current.values()
            for requirement in requirements
            if requirement["path"] not in paths
        }
        self.assertEqual(missing, set())

    def test_full_synthetic_passes_engine_import_contract(self) -> None:
        case = json.loads(
            (CONTRACT_ROOT / "examples" / "full-synthetic.json").read_text(
                encoding="utf-8"
            )
        )
        report = validate_import_contract(case)
        self.assertFalse(report.errors)

    def test_minimum_example_opens_only_reviewed_base_nodes(self) -> None:
        case = json.loads(
            (CONTRACT_ROOT / "examples" / "minimum-segments.json").read_text(
                encoding="utf-8"
            )
        )
        report = validate_import_contract(case)
        self.assertFalse(report.errors)
        plan = plan_dynamic_flow(case)
        self.assertEqual(
            set(plan["runnable_node_ids"]),
            {
                "data_inventory",
                "indicator_coverage",
                "segment_geometry",
                "adaptive_evidence_qra",
                "risk_matrix",
            },
        )


if __name__ == "__main__":
    unittest.main()
