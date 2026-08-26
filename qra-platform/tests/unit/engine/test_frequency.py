from __future__ import annotations

import math
import unittest

from qra_engine.frequency import calculate_loc_frequencies, discretize_segment

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


if __name__ == "__main__":
    unittest.main()
