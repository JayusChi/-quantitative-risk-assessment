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
SOURCE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "converter_jiujiang_stage1"
PROFILE_PATH = (
    PROJECT_ROOT
    / "resources"
    / "mappings"
    / "jiangxi-natural-gas"
    / "jiangxi-natural-gas.jiujiang.v1.json"
)
EXPECTED_PATH = SOURCE_DIR / "expected_business_case.json"
TRACE_FIELDS = {"source_ref", "source_refs", "quality", "review_status"}


def _without_trace(value: Any) -> Any:
    if isinstance(value, list):
        return [_without_trace(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _without_trace(item)
            for key, item in value.items()
            if key not in TRACE_FIELDS
        }
    return value


class JiujiangStage1ConversionTests(unittest.TestCase):
    def _convert(self, output: Path) -> dict[str, Any]:
        return convert_sources(
            source_dir=SOURCE_DIR,
            profile_path=PROFILE_PATH,
            output_dir=output,
            case_id="JXNG-JIUJIANG-GDBZYQ-JJ-1-STAGE1",
            project_name="九江支线脱敏真实资料回归案例",
            contract_validator=validate_import_contract,
            capability_planner=plan_dynamic_flow,
        )

    def test_customer_profile_filters_other_line_and_maps_shared_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            summary = self._convert(Path(temporary_directory))
            case = json.loads(Path(summary["paths"]["case"]).read_text(encoding="utf-8"))
            report = json.loads(
                Path(summary["paths"]["conversion_report"]).read_text(encoding="utf-8")
            )
            preview = json.loads(
                Path(summary["paths"]["conversion_preview"]).read_text(encoding="utf-8")
            )

        self.assertEqual(summary["status"], "READY_FOR_REVIEW")
        self.assertEqual(report["contract_status"], "PASS")
        self.assertEqual(len(case["segments"]), 1)
        self.assertEqual(case["segments"][0]["segment_id"], "GDBZYQ-JJ-1")
        self.assertEqual(case["segments"][0]["start_km"], 0.0)
        self.assertEqual(case["segments"][0]["end_km"], 10.938)
        self.assertEqual(case["segments"][0]["length_km"], 10.938)
        self.assertEqual(case["segments"][0]["outside_diameter_mm"], 273.0)
        self.assertEqual(case["pipeline"]["operating_pressure_source_range_mpa"], "3.0-3.8")
        self.assertNotIn("operating_pressure_mpa", case["pipeline"])
        self.assertNotIn("population_cells", case)
        self.assertEqual(case["data_category_manifest"]["category_count"], 6)
        self.assertEqual(len(preview["recognized_tables"]), 6)
        filtered = [
            issue for issue in report["issues"] if issue["code"] == "ROW_FILTERED_OUT"
        ]
        self.assertEqual(len(filtered), 6)
        self.assertTrue(all(row["row_number"] == 4 for row in report["field_lineage"]))
        self.assertNotIn("OTHER-LINE-1", json.dumps(case, ensure_ascii=False))

    def test_customer_profile_matches_sanitized_golden_case_and_is_repeatable(self) -> None:
        expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_summary = self._convert(Path(first))
            second_summary = self._convert(Path(second))
            case = json.loads(
                Path(first_summary["paths"]["case"]).read_text(encoding="utf-8")
            )
        self.assertEqual(first_summary["case_sha256"], second_summary["case_sha256"])
        self.assertEqual(_without_trace(case), expected)


if __name__ == "__main__":
    unittest.main()
