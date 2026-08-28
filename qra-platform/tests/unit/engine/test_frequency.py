from __future__ import annotations

import math
import unittest

from qra_engine.frequency import (
    calculate_loc_frequencies,
    discretize_segment,
    failure_probability_horizon_years,
    poisson_at_least_one_failure_probability,
)

from tests.unit.engine.helpers import load_case


class FrequencyTests(unittest.TestCase):
    def test_loc_frequency_conserves_mechanism_frequency(self) -> None:
        case = load_case()
        rows = calculate_loc_frequencies(case)
        segment = case["segments"][0]
        segment_id = segment["segment_id"]
        actual = sum(row.annual_frequency for row in rows if row.segment_id == segment_id)
        library = case["frequency_library"]
        expected = sum(
            base
            * case["segment_correction_factor"][segment_id][mechanism]
            * segment["length_km"]
            for mechanism, base in library["base_frequency_by_mechanism"].items()
        )
        self.assertTrue(math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-15))

    def test_every_segment_has_four_loc_rows(self) -> None:
        rows = calculate_loc_frequencies(load_case())
        self.assertEqual(len(rows), 20 * 4)

    def test_leak_point_frequency_shares_sum_to_one(self) -> None:
        segment = load_case()["segments"][1]
        points = discretize_segment(segment, 50.0)
        self.assertEqual(len(points), 10)
        self.assertTrue(math.isclose(sum(point.frequency_share for point in points), 1.0, abs_tol=1e-15))
        self.assertGreater(points[0].chainage_km, segment["start_km"])
        self.assertLess(points[-1].chainage_km, segment["end_km"])

    def test_poisson_probability_converts_frequency_without_rare_event_rounding_loss(self) -> None:
        frequency = 1.0e-9
        probability = poisson_at_least_one_failure_probability(frequency, 1.0)
        self.assertTrue(math.isclose(probability, frequency, rel_tol=1.0e-9))
        self.assertTrue(
            math.isclose(
                poisson_at_least_one_failure_probability(0.2, 3.0),
                1.0 - math.exp(-0.6),
                rel_tol=0.0,
                abs_tol=1.0e-15,
            )
        )

    def test_probability_horizon_defaults_to_one_year_and_rejects_invalid_values(self) -> None:
        self.assertEqual(failure_probability_horizon_years({}), 1.0)
        self.assertEqual(
            failure_probability_horizon_years(
                {"assessment": {"failure_probability_horizon_years": 10}}
            ),
            10.0,
        )
        for invalid in (0, -1, "not-a-number"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    failure_probability_horizon_years(
                        {"assessment": {"failure_probability_horizon_years": invalid}}
                    )


if __name__ == "__main__":
    unittest.main()
