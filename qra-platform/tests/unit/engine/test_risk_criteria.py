from __future__ import annotations

import math
import unittest

from qra_engine.risk import build_segment_risk_table
from qra_engine.risk_criteria import classify_individual_risk


class IndividualRiskCriterionTests(unittest.TestCase):
    def test_gbt34346_annex_c_boundaries(self) -> None:
        self.assertEqual(classify_individual_risk(0.0)["level"], "LOW_ACCEPTABLE")
        self.assertEqual(classify_individual_risk(1.0e-5)["level"], "LOW_ACCEPTABLE")
        self.assertEqual(classify_individual_risk(1.000001e-5)["level"], "MEDIUM_ALARP")
        self.assertEqual(classify_individual_risk(1.0e-3)["level"], "MEDIUM_ALARP")
        self.assertEqual(
            classify_individual_risk(1.000001e-3)["level"],
            "HIGH_UNACCEPTABLE",
        )

    def test_invalid_individual_risk_is_rejected(self) -> None:
        for value in (-1.0, math.nan, math.inf):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    classify_individual_risk(value)


class SegmentRiskTableTests(unittest.TestCase):
    def test_risk_value_and_per_km_density_have_independent_rankings(self) -> None:
        table = build_segment_risk_table(
            segments=[
                {"segment_id": "A", "start_km": 0.0, "end_km": 1.0, "length_km": 1.0},
                {"segment_id": "B", "start_km": 1.0, "end_km": 11.0, "length_km": 10.0},
            ],
            pll_by_segment={"A": 2.0e-4, "B": 3.0e-4},
            ir_by_segment_and_receptor={
                "A": {"R1": 2.0e-3},
                "B": {"R1": 1.0e-6},
            },
            maximum_consequence_by_segment={
                "A": {"expected_fatalities": 2.0},
                "B": {"expected_fatalities": 5.0},
            },
            dominant_risk_scenario_by_segment={
                "A": {"pll_contribution_per_year": 2.0e-4},
                "B": {"pll_contribution_per_year": 3.0e-4},
            },
            scenario_frequency_and_fatalities_by_segment={
                "A": [(1.0e-4, 2.0)],
                "B": [(6.0e-5, 5.0)],
            },
            initiating_frequency_by_segment={"A": 1.0e-4, "B": 6.0e-5},
        )
        by_id = {row["segment_id"]: row for row in table["ranking"]}
        self.assertEqual(by_id["B"]["risk_value_rank"], 1)
        self.assertEqual(by_id["A"]["risk_density_rank"], 1)
        self.assertEqual(by_id["A"]["risk_level"]["level"], "HIGH_UNACCEPTABLE")
        self.assertEqual(by_id["B"]["risk_level"]["level"], "LOW_ACCEPTABLE")
        self.assertEqual(table["high_risk_segment_ids"], ["A"])


if __name__ == "__main__":
    unittest.main()
