from __future__ import annotations

import unittest

from qra_converter.validation import validate_conversion_quality


class ConversionQualityTests(unittest.TestCase):
    def test_duplicate_segment_and_gap_are_detected(self) -> None:
        case = {
            "pipeline": {},
            "segments": [
                {"segment_id": "S1", "start_km": 0.0, "end_km": 1.0, "length_km": 1.0},
                {"segment_id": "S1", "start_km": 1.2, "end_km": 2.0, "length_km": 0.8},
            ],
        }
        codes = {issue.code for issue in validate_conversion_quality(case)}
        self.assertIn("SEGMENT_ID_DUPLICATE", codes)
        self.assertIn("SEGMENT_GAP", codes)

    def test_empty_record_key_is_detected(self) -> None:
        case = {
            "pipeline": {},
            "segments": [{"segment_id": "S1", "start_km": 0.0, "end_km": 1.0, "length_km": 1.0}],
            "raw_data_categories": {"cips": {"records": [{"segment_id": "S1"}]}},
        }
        codes = {issue.code for issue in validate_conversion_quality(case)}
        self.assertIn("RECORD_ID_MISSING", codes)

    def test_overlapping_segments_are_detected(self) -> None:
        case = {
            "pipeline": {},
            "segments": [
                {"segment_id": "S1", "start_km": 0.0, "end_km": 1.2, "length_km": 1.2},
                {"segment_id": "S2", "start_km": 1.0, "end_km": 2.0, "length_km": 1.0},
            ],
        }
        codes = {issue.code for issue in validate_conversion_quality(case)}
        self.assertIn("SEGMENT_OVERLAP", codes)


if __name__ == "__main__":
    unittest.main()
