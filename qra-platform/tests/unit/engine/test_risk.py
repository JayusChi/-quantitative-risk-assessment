from __future__ import annotations

import math
import unittest

from qra_engine.risk import aggregate_precomputed_human

from tests.unit.engine.helpers import load_case


class RiskAggregationTests(unittest.TestCase):
    def test_existing_human_golden_result_is_reproduced(self) -> None:
        case = load_case()
        actual = aggregate_precomputed_human(case)
        expected = case["expected_aggregation"]
        societal = actual["societal_risk"]

        self.assertTrue(
            math.isclose(
                societal["pipeline_pll_per_year"],
                expected["pipeline_pll_per_year"],
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        )
        self.assertEqual(
            [row["segment_id"] for row in societal["segment_ranking"]],
            expected["human_pll_ranking"],
        )
        self.assertEqual(
            actual["individual_risk"]["maximum"]["receptor_id"],
            expected["max_ir_receptor"],
        )
        for actual_point, expected_point in zip(societal["fn_curve"], expected["fn_curve"], strict=True):
            self.assertEqual(actual_point["fatalities_at_least"], expected_point["fatalities_at_least"])
            self.assertTrue(
                math.isclose(
                    actual_point["cumulative_frequency_per_year"],
                    expected_point["cumulative_frequency_per_year"],
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
            )


if __name__ == "__main__":
    unittest.main()
