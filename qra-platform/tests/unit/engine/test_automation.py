from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from qra_engine import QRAEngine
from qra_engine.automation import AUTOMATION_SCHEMA_VERSION, run_automation
from qra_engine.reporting import build_risk_matrix, render_charts, write_risk_matrix_files

from tests.unit.engine.helpers import SYNTHETIC_CASE, load_case


class ReportingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = QRAEngine().run(load_case(), profile="synthetic-chain")
        cls.matrix = build_risk_matrix(cls.result)

    def test_matrix_preserves_quantitative_frequency_consequence_relation(self) -> None:
        self.assertEqual(self.matrix["summary"]["segment_count"], 20)
        self.assertEqual(len(self.matrix["cells"]), 25)
        self.assertEqual(
            self.matrix["criteria"]["status"],
            "DISPLAY_ONLY_NOT_ACCEPTANCE_CRITERION",
        )
        for row in self.matrix["segments"]:
            self.assertAlmostEqual(
                row["initiating_failure_frequency_per_year"]
                * row["equivalent_fatalities_per_initiating_failure"],
                row["pll_per_year"],
                places=14,
            )

    def test_matrix_and_selected_charts_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix_paths = write_risk_matrix_files(self.matrix, root / "derived")
            chart_paths = render_charts(
                self.result,
                self.matrix,
                root / "charts",
                ("risk_matrix", "fn_curve"),
            )
            self.assertTrue(all(path.is_file() for path in matrix_paths + chart_paths))
            self.assertIn("频率 x N_q", chart_paths[0].read_text(encoding="utf-8"))


class AutomationTests(unittest.TestCase):
    def test_validation_only_job(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            manifest = run_automation(
                {
                    "schema_version": AUTOMATION_SCHEMA_VERSION,
                    "job_id": "VALIDATE-ONLY",
                    "input": str(SYNTHETIC_CASE),
                    "calculations": ["validate-input"],
                    "outputs": {
                        "directory": str(output),
                        "risk_matrix": False,
                        "charts": [],
                        "html_dashboard": False,
                    },
                }
            )
            self.assertEqual(manifest["job_id"], "VALIDATE-ONLY")
            self.assertIn(manifest["status"], ("PASS", "PASS_WITH_WARNING"))
            self.assertTrue((output / "results" / "validation.json").is_file())
            saved = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["requested_calculations"], ["validate-input"])

    def test_unknown_calculation_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "未知计算项"):
                run_automation(
                    {
                        "schema_version": AUTOMATION_SCHEMA_VERSION,
                        "input": str(SYNTHETIC_CASE),
                        "calculations": ["unknown"],
                        "outputs": {
                            "directory": temporary,
                            "risk_matrix": False,
                            "charts": [],
                            "html_dashboard": False,
                        },
                    }
                )


if __name__ == "__main__":
    unittest.main()
