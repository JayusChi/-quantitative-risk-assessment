from __future__ import annotations

import unittest

from qra_converter.matching import attach_segments


class SegmentMatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.segments = [
            {"segment_id": "S1", "start_km": 0.0, "end_km": 1.0},
            {"segment_id": "S2", "start_km": 1.0, "end_km": 2.0},
        ]

    def test_boundary_uses_start_inclusive_end_exclusive(self) -> None:
        records, issues = attach_segments(
            [{"record_id": "R1", "chainage_km": 1.0}],
            self.segments,
            target_path="raw.records",
        )
        self.assertFalse(issues)
        self.assertEqual(records[0]["segment_id"], "S2")

    def test_last_segment_includes_pipeline_endpoint(self) -> None:
        records, issues = attach_segments(
            [{"record_id": "R1", "chainage_km": 2.0}],
            self.segments,
            target_path="raw.records",
        )
        self.assertFalse(issues)
        self.assertEqual(records[0]["segment_id"], "S2")

    def test_out_of_range_chainage_is_an_error(self) -> None:
        _, issues = attach_segments(
            [{"record_id": "R1", "chainage_km": 2.1}],
            self.segments,
            target_path="raw.records",
        )
        self.assertEqual(issues[0].code, "CHAINAGE_OUT_OF_RANGE")


if __name__ == "__main__":
    unittest.main()
