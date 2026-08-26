from __future__ import annotations

import math
import unittest

from qra_engine import QRAEngine
from qra_engine.gbt34346 import (
    consequence_area_m2,
    damage_correction_factor,
    gas_leak_scenarios,
    gas_pipeline_average_failure_frequency_per_km_year,
    management_correction_factor,
    pipeline_failure_probability_per_km_year,
    pipeline_human_risk_fatalities_per_km_year,
    weighted_average_consequence_area_m2,
    weighted_average_leak_rate_kg_s,
)

from tests.unit.engine.helpers import load_case


class AnnexCFormulaTests(unittest.TestCase):
    def test_table_c1_gas_wall_thickness_boundaries(self) -> None:
        self.assertEqual(gas_pipeline_average_failure_frequency_per_km_year(5.0), 4.0e-4)
        self.assertEqual(gas_pipeline_average_failure_frequency_per_km_year(10.0), 1.7e-4)
        self.assertEqual(gas_pipeline_average_failure_frequency_per_km_year(15.0), 8.1e-5)
        self.assertEqual(gas_pipeline_average_failure_frequency_per_km_year(15.1), 4.1e-5)

    def test_formula_c3_management_factor(self) -> None:
        self.assertAlmostEqual(management_correction_factor([100.0] * 6), 0.1, places=15)
        self.assertAlmostEqual(management_correction_factor([0.0] * 6), 10.0, places=15)

    def test_formula_c2_damage_factor_and_c1_probability(self) -> None:
        factors = {
            "corrosion": 1.0,
            "body_defect": 2.0,
            "third_party": 3.0,
            "manufacturing": 4.0,
            "fatigue": 5.0,
        }
        weights = {key: 0.2 for key in factors}
        damage = damage_correction_factor(factors, weights)
        self.assertAlmostEqual(damage, 3.0, places=15)
        self.assertAlmostEqual(
            pipeline_failure_probability_per_km_year(4.1e-5, 0.5, damage),
            6.15e-5,
            places=15,
        )

    def test_table_c24_gas_scenarios(self) -> None:
        scenarios = gas_leak_scenarios(1016.0)
        self.assertEqual([row.hole_diameter_mm for row in scenarios], [5.0, 25.0, 150.0, 400.0])
        self.assertAlmostEqual(sum(row.probability_fraction for row in scenarios), 1.0)

    def test_formulas_c40_to_c43(self) -> None:
        self.assertAlmostEqual(consequence_area_m2(10.0), 400.0 * math.pi)
        frequencies = [0.5, 0.5]
        self.assertAlmostEqual(
            weighted_average_consequence_area_m2(frequencies, [100.0, 300.0]),
            200.0,
        )
        self.assertAlmostEqual(
            weighted_average_leak_rate_kg_s(frequencies, [10.0, 30.0]),
            20.0,
        )
        self.assertAlmostEqual(
            pipeline_human_risk_fatalities_per_km_year(1.0e-4, 0.1, 1000.0, 0.001),
            1.0e-5,
        )


class AnnexCProfileTests(unittest.TestCase):
    def test_standard_formula_profile_returns_segment_ranking(self) -> None:
        result = QRAEngine().run(load_case(), profile="gbt34346-annex-c")
        assessment = result["pipeline_secondary_assessment"]
        self.assertFalse(result["run"]["formal_report_allowed"])
        self.assertEqual(
            assessment["method_type"],
            "QUANTITATIVE_SECONDARY_ASSESSMENT_NOT_FULL_SPATIAL_QRA",
        )
        self.assertEqual(len(assessment["segment_ranking"]), 20)
        self.assertGreater(assessment["pipeline_risk_fatalities_per_year"], 0.0)
        self.assertEqual(
            [row["risk_rank"] for row in assessment["segment_ranking"]],
            list(range(1, 21)),
        )
        for row in assessment["segment_ranking"]:
            self.assertEqual(len(row["leak_scenarios"]), 4)
            self.assertGreater(row["weighted_human_consequence_area_m2"], 0.0)


if __name__ == "__main__":
    unittest.main()
