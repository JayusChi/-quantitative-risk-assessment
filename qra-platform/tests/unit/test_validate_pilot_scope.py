from __future__ import annotations

import csv
import importlib.util
import json
import shutil
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_PILOT_DIR = (
    REPO_ROOT / "resources" / "pilots" / "jiujiang-qra-screening-pilot-v1"
)
MODULE_PATH = REPO_ROOT / "tools" / "validate_pilot_scope.py"
SPEC = importlib.util.spec_from_file_location("validate_pilot_scope", MODULE_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover
    raise RuntimeError(f"无法加载{MODULE_PATH}")
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


class ValidatePilotScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.pilot_dir = Path(self._temp.name) / "pilot"
        shutil.copytree(CANONICAL_PILOT_DIR, self.pilot_dir)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _validate(self) -> dict[str, object]:
        return VALIDATOR.validate_pilot_scope(
            self.pilot_dir,
            repo_root=REPO_ROOT,
            verify_source_files=False,
        )

    def _mutate_manifest(self, mutate: Callable[[dict[str, object]], None]) -> None:
        path = self.pilot_dir / "pilot-manifest.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        mutate(value)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _mutate_csv(
        self,
        name: str,
        mutate: Callable[[list[dict[str, str]]], None],
    ) -> None:
        path = self.pilot_dir / name
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = list(reader.fieldnames or [])
            rows = [dict(row) for row in reader]
        mutate(rows)
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _codes(report: dict[str, object]) -> set[str]:
        errors = report.get("validation_errors", [])
        return {str(row["code"]) for row in errors}  # type: ignore[index]

    def test_valid_internal_stage1_pilot_is_ready(self) -> None:
        report = self._validate()
        self.assertEqual("PASS", report["validation_status"])
        self.assertEqual("READY", report["status"])
        self.assertFalse(report["source_file_verification_performed"])
        self.assertEqual([], report["validation_errors"])

    def test_unknown_field_id_fails(self) -> None:
        self._mutate_csv(
            "field-node-matrix.csv",
            lambda rows: rows[0].update(field_id="invented.unknown_field"),
        )
        self.assertIn("UNKNOWN_FIELD_ID", self._codes(self._validate()))

    def test_unknown_node_id_fails(self) -> None:
        self._mutate_manifest(
            lambda value: value["target_node_ids"].append("invented_unknown_node")  # type: ignore[union-attr]
        )
        self.assertIn("UNKNOWN_NODE_ID", self._codes(self._validate()))

    def test_nonexistent_source_id_fails(self) -> None:
        self._mutate_csv(
            "field-node-matrix.csv",
            lambda rows: rows[0].update(preferred_source_id="SRC-NOT-REGISTERED"),
        )
        self.assertIn("UNKNOWN_SOURCE_ID", self._codes(self._validate()))

    def test_critical_field_without_evidence_requirement_fails(self) -> None:
        self._mutate_csv(
            "field-node-matrix.csv",
            lambda rows: rows[0].update(evidence_requirement=""),
        )
        self.assertIn("CRITICAL_FIELD_EVIDENCE_REQUIRED", self._codes(self._validate()))

    def test_conflict_sensitive_field_without_manual_review_fails(self) -> None:
        self._mutate_csv(
            "field-node-matrix.csv",
            lambda rows: rows[0].update(
                review_required="false", conflict_policy="SOURCE_PRIORITY"
            ),
        )
        self.assertIn("CONFLICT_FIELD_REVIEW_REQUIRED", self._codes(self._validate()))

    def test_silent_default_for_missing_field_fails(self) -> None:
        self._mutate_csv(
            "field-node-matrix.csv",
            lambda rows: rows[0].update(
                current_availability="MISSING",
                default_allowed="true",
                missing_policy="DEFAULT_VALUE",
            ),
        )
        codes = self._codes(self._validate())
        self.assertIn("DEFAULT_NOT_ALLOWED", codes)
        self.assertIn("SILENT_MISSING_VALUE_POLICY", codes)

    def test_full_qra_report_tier_fails(self) -> None:
        self._mutate_manifest(
            lambda value: value.update(target_report_tier="FULL_SPATIAL_HUMAN_QRA")
        )
        self.assertIn("FORMAL_REPORT_TIER_FORBIDDEN", self._codes(self._validate()))

    def test_target_node_required_input_without_source_or_gap_fails(self) -> None:
        def remove_accounting(rows: list[dict[str, str]]) -> None:
            row = next(item for item in rows if item["field_id"] == "segment.segment_id")
            row.update(
                preferred_source_id="",
                alternative_source_ids="",
                current_availability="MISSING",
                gap_id="",
            )

        self._mutate_csv("field-node-matrix.csv", remove_accounting)
        self.assertIn("TARGET_NODE_INPUT_UNACCOUNTED", self._codes(self._validate()))

    def test_synthetic_source_mislabeled_as_real_engineering_data_fails(self) -> None:
        self._mutate_manifest(
            lambda value: value.update(real_data_status="REAL_PROJECT_DATA")
        )
        self._mutate_csv(
            "source-inventory.csv",
            lambda rows: rows[0].update(data_classification="SYNTHETIC_TEST_ONLY"),
        )
        self.assertIn("SYNTHETIC_MISLABELED_REAL", self._codes(self._validate()))


if __name__ == "__main__":
    unittest.main()
