from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from qra_engine.dynamic import run_dynamic_flow


PROJECT_ROOT = Path(__file__).resolve().parents[2]
JIUJIANG_CASE = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "converter_jiujiang_stage1"
    / "expected_business_case.json"
)


class Stage2JiujiangTrialRegressionTest(unittest.TestCase):
    def test_real_shape_trial_is_repeatable_and_preserves_missing_risk_semantics(self) -> None:
        case = json.loads(JIUJIANG_CASE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = run_dynamic_flow(
                case,
                root / "run-a",
                generate_charts=True,
                job_id="STAGE2-JIUJIANG-A",
            )
            second = run_dynamic_flow(
                case,
                root / "run-b",
                generate_charts=True,
                job_id="STAGE2-JIUJIANG-B",
            )

            self.assertEqual(first["status"], "PARTIAL")
            self.assertEqual(
                first["numerical_result_sha256"],
                second["numerical_result_sha256"],
            )
            nodes = {row["node_id"]: row for row in first["nodes"]}
            self.assertEqual(
                {
                    node_id
                    for node_id, row in nodes.items()
                    if row["status"] == "COMPLETED"
                },
                {
                    "data_inventory",
                    "indicator_coverage",
                    "segment_geometry",
                    "adaptive_evidence_qra",
                    "risk_matrix",
                },
            )
            self.assertFalse(
                any(row["status"] == "FAILED_ISOLATED" for row in nodes.values())
            )
            human_missing = {
                item["path"] for item in nodes["human_qra"]["missing_inputs"]
            }
            self.assertTrue(
                {
                    "assessment",
                    "frequency_library",
                    "weather_joint_probability",
                    "population_cells",
                    "ignition_model",
                }.issubset(human_missing)
            )

            adaptive = json.loads(
                (root / "run-a" / "nodes" / "adaptive_evidence_qra.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                adaptive["result_tier"],
                "EVIDENCE_CONDITIONED_SCREENING_ESTIMATE",
            )
            self.assertGreater(
                adaptive["human_risk"]["societal_risk"]["pipeline_pll_per_year"],
                0.0,
            )
            self.assertFalse(adaptive["human_risk"]["individual_risk"]["available"])
            self.assertIsNone(
                adaptive["human_risk"]["individual_risk"]["maximum"][
                    "value_per_year"
                ]
            )
            self.assertFalse(
                adaptive["human_risk"]["societal_risk"]["fn_curve_available"]
            )
            self.assertEqual(adaptive["human_risk"]["societal_risk"]["fn_curve"], [])

            capability = json.loads(
                (root / "run-a" / "capability_report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(capability["risk_result"]["pll_available"])
            self.assertFalse(capability["risk_result"]["individual_risk_available"])
            self.assertFalse(capability["risk_result"]["fn_curve_available"])
            dashboard = (root / "run-a" / "report_dashboard.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("不可算（缺空间受体）", dashboard)
            chart_root = root / "run-a" / "charts" / "adaptive_evidence_qra"
            self.assertFalse((chart_root / "05_个人风险.svg").exists())
            self.assertFalse((chart_root / "06_FN曲线.svg").exists())
            self.assertFalse((chart_root / "07_融合优先级.svg").exists())


if __name__ == "__main__":
    unittest.main()
