from __future__ import annotations

import json
import unittest
from pathlib import Path

from qra_converter.contract_catalog import load_contract_catalog
from qra_converter.schema_validation import validate_qra_input, validate_schema_document


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = PROJECT_ROOT / "resources" / "contracts" / "part1" / "v1"
INVALID_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "contracts_v1" / "invalid"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class SchemaValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = load_contract_catalog(CONTRACT_ROOT)

    def test_minimum_and_full_examples_pass_qra_schema(self) -> None:
        for name in ("minimum-segments.json", "full-synthetic.json"):
            with self.subTest(name=name):
                value = read_json(CONTRACT_ROOT / "examples" / name)
                self.assertEqual(validate_qra_input(value, catalog=self.catalog), ())

    def test_each_non_qra_schema_has_a_valid_example(self) -> None:
        cases = {
            "candidate-field": "candidate-fields.json",
            "evidence": "evidence.json",
            "quality-issue": "quality-issues.json",
            "review-decision": "review-decisions.json",
        }
        for schema, name in cases.items():
            with self.subTest(schema=schema):
                for value in read_json(CONTRACT_ROOT / "examples" / name):
                    self.assertEqual(
                        validate_schema_document(
                            value, catalog=self.catalog, schema_name=schema
                        ),
                        (),
                    )
        snapshot = read_json(CONTRACT_ROOT / "examples" / "snapshot-manifest.json")
        self.assertEqual(
            validate_schema_document(
                snapshot, catalog=self.catalog, schema_name="snapshot-manifest"
            ),
            (),
        )

    def test_candidate_requires_evidence_or_declared_non_document_source(self) -> None:
        value = read_json(INVALID_ROOT / "candidate-missing-evidence.json")
        codes = {
            issue.code
            for issue in validate_schema_document(
                value, catalog=self.catalog, schema_name="candidate-field"
            )
        }
        self.assertIn("CONTRACT.SCHEMA.ANY_OF", codes)

    def test_confidence_and_review_action_are_bounded_enums(self) -> None:
        candidate = read_json(INVALID_ROOT / "candidate-confidence.json")
        self.assertIn(
            "CONTRACT.SCHEMA.RANGE",
            {
                issue.code
                for issue in validate_schema_document(
                    candidate, catalog=self.catalog, schema_name="candidate-field"
                )
            },
        )
        decision = read_json(INVALID_ROOT / "review-action.json")
        self.assertIn(
            "CONTRACT.SCHEMA.ENUM",
            {
                issue.code
                for issue in validate_schema_document(
                    decision, catalog=self.catalog, schema_name="review-decision"
                )
            },
        )

    def test_qra_invalid_cases_produce_stable_codes(self) -> None:
        cases = {
            "qra-missing-segments.json": "CONTRACT.SCHEMA.REQUIRED",
            "qra-duplicate-segment.json": "CONTRACT.SEGMENT_ID_DUPLICATE",
            "qra-reverse-chainage.json": "NORMALIZE.CHAINAGE_INVALID",
            "qra-wrong-unit.json": "NORMALIZE.UNIT_UNSUPPORTED",
            "qra-probability-not-normalized.json": "CONTRACT.PROBABILITY_NOT_NORMALIZED",
            "qra-unknown-field.json": "CONTRACT.SCHEMA.UNKNOWN_FIELD",
            "qra-non-finite.json": "CONTRACT.NON_FINITE_NUMBER",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                codes = {
                    issue.code
                    for issue in validate_qra_input(
                        read_json(INVALID_ROOT / name), catalog=self.catalog
                    )
                }
                self.assertIn(expected, codes)

    def test_evidence_issue_and_snapshot_invalid_examples_are_rejected(self) -> None:
        cases = {
            "evidence-location.json": "evidence",
            "quality-status.json": "quality-issue",
            "snapshot-unresolved.json": "snapshot-manifest",
        }
        for name, schema in cases.items():
            with self.subTest(name=name):
                self.assertTrue(
                    validate_schema_document(
                        read_json(INVALID_ROOT / name),
                        catalog=self.catalog,
                        schema_name=schema,
                    )
                )


if __name__ == "__main__":
    unittest.main()
