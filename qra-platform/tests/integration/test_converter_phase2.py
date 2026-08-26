from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from qra_converter.service import convert_sources
from qra_engine.dynamic import plan_dynamic_flow
from qra_engine.validation import validate_import_contract

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "converter_phase2"
PROFILE_PATH = (
    PROJECT_ROOT / "resources" / "mappings" / "generic" / "generic.multisource-review.v2.json"
)
DECISIONS_PATH = SOURCE_DIR / "review_decisions.json"
EXPECTED_PATH = SOURCE_DIR / "expected_business_case.json"
TRACE_FIELDS = {"source_ref", "source_refs", "quality", "review_status"}


def _without_trace(value: Any) -> Any:
    if isinstance(value, list):
        return [_without_trace(item) for item in value]
    if isinstance(value, dict):
        return {key: _without_trace(item) for key, item in value.items() if key not in TRACE_FIELDS}
    return value


class ConverterPhase2IntegrationTests(unittest.TestCase):
    def _convert(self, output: Path, decisions: Path | None = None) -> dict[str, Any]:
        return convert_sources(
            source_dir=SOURCE_DIR,
            profile_path=PROFILE_PATH,
            output_dir=output,
            case_id="CONVERTER-PHASE2-001",
            project_name="多来源合并黄金案例",
            contract_validator=validate_import_contract,
            capability_planner=plan_dynamic_flow,
            review_decisions_path=decisions,
        )

    def test_conflict_blocks_calculation_until_review_is_applied(self) -> None:
        with tempfile.TemporaryDirectory() as pending_directory:
            pending_summary = self._convert(Path(pending_directory))
            preview = json.loads(
                Path(pending_summary["paths"]["conversion_preview"]).read_text(encoding="utf-8")
            )
            pending_case = json.loads(
                Path(pending_summary["paths"]["case"]).read_text(encoding="utf-8")
            )
        self.assertEqual(pending_summary["status"], "BLOCKED")
        self.assertEqual(pending_summary["pending_review_count"], 1)
        self.assertFalse(preview["capability"]["calculation_eligible"])
        self.assertIn("segment_geometry", preview["capability"]["runnable_node_ids"])
        records = pending_case["raw_data_categories"]["cips_dense_interval_potential"]["records"]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["review_status"], "NEEDS_CONFIRMATION")

        with tempfile.TemporaryDirectory() as resolved_directory:
            resolved_summary = self._convert(Path(resolved_directory), decisions=DECISIONS_PATH)
            report = json.loads(
                Path(resolved_summary["paths"]["conversion_report"]).read_text(encoding="utf-8")
            )
            case = json.loads(Path(resolved_summary["paths"]["case"]).read_text(encoding="utf-8"))
        self.assertEqual(resolved_summary["status"], "READY_FOR_REVIEW")
        self.assertEqual(report["contract_status"], "PASS")
        self.assertEqual(report["summary"]["review_audit_count"], 1)
        self.assertEqual(report["summary"]["pending_review_count"], 0)
        self.assertEqual(
            case["raw_data_categories"]["cips_dense_interval_potential"]["records"][0][
                "review_status"
            ],
            "HUMAN_CONFIRMED",
        )

    def test_confirmed_multisource_case_matches_golden_business_json(self) -> None:
        expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as output_directory:
            summary = self._convert(Path(output_directory), decisions=DECISIONS_PATH)
            case = json.loads(Path(summary["paths"]["case"]).read_text(encoding="utf-8"))
            report = json.loads(
                Path(summary["paths"]["conversion_report"]).read_text(encoding="utf-8")
            )
            manifest = json.loads(
                Path(summary["paths"]["source_manifest"]).read_text(encoding="utf-8")
            )
        self.assertEqual(_without_trace(case), expected)
        self.assertEqual(len(case["segments"]), 2)
        self.assertEqual(
            len(case["raw_data_categories"]["cips_dense_interval_potential"]["records"]),
            2,
        )
        lineage_paths = {row["target_path"] for row in report["field_lineage"]}
        self.assertIn("segments[*].segment_id", lineage_paths)
        self.assertIn(
            "raw_data_categories.cips_dense_interval_potential[*].off_potential_v",
            lineage_paths,
        )
        self.assertEqual(manifest["source_count"], 4)
        self.assertTrue(any(source["duplicate_content_of"] for source in manifest["sources"]))


if __name__ == "__main__":
    unittest.main()
