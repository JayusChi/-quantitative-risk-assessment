from __future__ import annotations

import copy
import math
import unittest

from qra_engine import QRAEngine
from qra_engine.audit import sha256_json, sha256_numerical_result

from tests.unit.engine.helpers import load_case


class EngineIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = QRAEngine()
        cls.case = load_case()
        cls.result = cls.engine.run(cls.case, profile="synthetic-chain")

    def test_full_synthetic_chain_runs(self) -> None:
        result = self.result
        self.assertFalse(result["run"]["formal_report_allowed"])
        self.assertEqual(result["human_risk"]["consequence_model"]["status"], "SYNTHETIC_TEST_ONLY")
        self.assertEqual(result["calculation_diagnostics"]["leak_point_count"], 160)
        self.assertEqual(result["calculation_diagnostics"]["expanded_scenario_count"], 20480)
        self.assertLess(abs(result["calculation_diagnostics"]["frequency_balance_error"]), 1e-12)
        self.assertGreater(result["human_risk"]["societal_risk"]["pipeline_pll_per_year"], 0.0)
        self.assertEqual(len(result["human_risk"]["societal_risk"]["segment_ranking"]), 20)
        segment_risk = result["human_risk"]["segment_risk"]
        self.assertEqual(len(segment_risk["ranking"]), 20)
        self.assertEqual(
            [row["risk_value_rank"] for row in segment_risk["ranking"]],
            list(range(1, 21)),
        )
        self.assertEqual(
            segment_risk["risk_value_definition"]["formula"],
            "R_segment = PLL_segment = sum_s(f_s * N_s)",
        )
        self.assertAlmostEqual(
            sum(row["risk_value_fatalities_per_year"] for row in segment_risk["ranking"]),
            result["human_risk"]["societal_risk"]["pipeline_pll_per_year"],
            places=14,
        )
        classified_ids = (
            segment_risk["high_risk_segment_ids"]
            + segment_risk["alarp_segment_ids"]
            + segment_risk["acceptable_segment_ids"]
        )
        self.assertEqual(set(classified_ids), {f"SEG-{index:03d}" for index in range(1, 21)})
        self.assertEqual(
            result["human_risk"]["individual_risk"]["acceptability"]["level"],
            "MEDIUM_ALARP",
        )
        self.assertTrue(result["audit"]["result_sha256"])

    def test_run_is_deterministic(self) -> None:
        second = self.engine.run(self.case, profile="synthetic-chain")
        self.assertEqual(self.result["audit"]["input_sha256"], second["audit"]["input_sha256"])
        self.assertEqual(self.result["audit"]["result_sha256"], second["audit"]["result_sha256"])

    def test_result_hash_can_be_reproduced(self) -> None:
        unhashed = copy.deepcopy(self.result)
        expected_hash = unhashed["audit"]["result_sha256"]
        unhashed["audit"]["result_sha256"] = None
        self.assertEqual(sha256_json(unhashed), expected_hash)

    def test_zero_population_clears_pll_but_not_ir(self) -> None:
        zero_population = copy.deepcopy(self.case)
        for cell in zero_population["population_cells"]:
            cell["population_day"] = 0
            cell["population_night"] = 0
        result = self.engine.run(zero_population, profile="synthetic-chain")
        self.assertEqual(result["human_risk"]["societal_risk"]["pipeline_pll_per_year"], 0.0)
        self.assertTrue(
            all(point["cumulative_frequency_per_year"] == 0.0 for point in result["human_risk"]["societal_risk"]["fn_curve"])
        )
        baseline_ir = self.result["human_risk"]["individual_risk"]["by_population_cell"]
        zero_population_ir = result["human_risk"]["individual_risk"]["by_population_cell"]
        self.assertEqual(baseline_ir, zero_population_ir)

    def test_branch_frequency_equals_initiating_frequency(self) -> None:
        diagnostics = self.result["calculation_diagnostics"]
        self.assertTrue(
            math.isclose(
                diagnostics["total_initiating_frequency_per_year"],
                diagnostics["total_expanded_branch_frequency_per_year"],
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        )

    def test_numerical_hash_normalizes_record_order_and_float_tail_only(self) -> None:
        first = {
            "rows": [{"id": "B", "value": 0.1 + 0.2}, {"id": "A", "value": 1.0}],
            "xy_m": [10.0, 20.0],
        }
        equivalent = {
            "xy_m": [10.0, 20.0],
            "rows": [{"value": 1.0, "id": "A"}, {"value": 0.30000000000000004, "id": "B"}],
        }
        swapped_coordinates = copy.deepcopy(equivalent)
        swapped_coordinates["xy_m"] = [20.0, 10.0]
        self.assertEqual(
            sha256_numerical_result(first), sha256_numerical_result(equivalent)
        )
        self.assertNotEqual(
            sha256_numerical_result(first),
            sha256_numerical_result(swapped_coordinates),
        )


if __name__ == "__main__":
    unittest.main()
