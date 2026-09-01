from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = PROJECT_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from evaluate_stage3_robustness import evaluate_files  # noqa: E402
from run_stage3_standard_golden import _standard_identifier  # noqa: E402

FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "roadmap_stage3_golden"


class Stage3RobustnessEvaluatorTests(unittest.TestCase):
    def test_standard_identifier_is_normalized_without_retaining_file_name(self) -> None:
        cases = {
            "24 GBT 30582-2014 基于风险的标准": "GB/T 30582-2014",
            "DB34／T 4280-2022 实施导则": "DB34/T 4280-2022",
            "SYT 6891.1-2012 风险评价": "SY/T 6891.1-2012",
            "Q／SY 05676.1-2020 技术规范": "Q/SY 05676.1-2020",
        }
        for file_stem, expected in cases.items():
            with self.subTest(file_stem=file_stem):
                self.assertEqual(_standard_identifier(file_stem), expected)
        self.assertIsNone(_standard_identifier("脱敏风险评价报告"))

    def test_synthetic_contract_metrics_pass_but_never_count_as_real(self) -> None:
        report = evaluate_files(
            FIXTURES / "manifest.jsonl",
            FIXTURES / "annotations.jsonl",
            FIXTURES / "results.jsonl",
        )
        self.assertTrue(report["passed"])
        self.assertEqual(report["document_counts"]["real_business"], 0)
        self.assertEqual(report["metrics"]["evidence_binding_rate"], 1.0)
        self.assertEqual(report["metrics"]["precision"], 1.0)
        self.assertEqual(report["metrics"]["recall"], 1.0)
        self.assertEqual(report["metrics"]["conflict_detection_rate"], 1.0)

    def test_real_document_gate_fails_and_reports_only_aggregate_gap(self) -> None:
        report = evaluate_files(
            FIXTURES / "manifest.jsonl",
            FIXTURES / "annotations.jsonl",
            FIXTURES / "results.jsonl",
            require_min_documents=20,
        )
        self.assertFalse(report["passed"])
        self.assertEqual(report["document_counts"]["missing_real_documents"], 20)
        self.assertIn("GOLDEN.REAL_DOCUMENT_COUNT_INSUFFICIENT", report["problem_codes"])
        serialized = str(report)
        self.assertNotIn("SEG-A", serialized)
        self.assertNotIn("SYNTH-STAGE3", serialized)


if __name__ == "__main__":
    unittest.main()
