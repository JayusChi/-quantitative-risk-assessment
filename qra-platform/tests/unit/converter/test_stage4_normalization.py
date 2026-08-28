from __future__ import annotations

import unittest
from decimal import Decimal

from qra_converter.normalization.chainage import in_half_open_range, parse_chainage_km
from qra_converter.normalization.coordinates import normalize_coordinate
from qra_converter.normalization.dates import normalize_date
from qra_converter.normalization.numbers import parse_number
from qra_converter.normalization.service import normalize_candidates
from qra_converter.normalization.units import convert_unit


class Stage4NormalizationTests(unittest.TestCase):
    def test_pressure_units_share_one_deterministic_value(self) -> None:
        self.assertEqual(convert_unit(Decimal("5"), "MPa", "MPa"), Decimal("5"))
        self.assertEqual(convert_unit(Decimal("5000"), "kPa", "MPa"), Decimal("5"))
        self.assertEqual(convert_unit(Decimal("50"), "bar", "MPa"), Decimal("5"))

    def test_chainage_examples_and_half_open_terminal_rule(self) -> None:
        self.assertEqual(parse_chainage_km("K12+300"), Decimal("12.3"))
        self.assertEqual(parse_chainage_km("12km+300m"), Decimal("12.3"))
        self.assertEqual(parse_chainage_km("12300m"), Decimal("12.3"))
        self.assertFalse(in_half_open_range(Decimal("2"), Decimal("1"), Decimal("2")))
        self.assertTrue(
            in_half_open_range(Decimal("2"), Decimal("1"), Decimal("2"), is_pipeline_terminal=True)
        )

    def test_ranges_and_partial_dates_are_not_invented(self) -> None:
        value = parse_number("约 3.2")
        self.assertEqual(value.value, Decimal("3.2"))
        self.assertEqual(value.qualifier, "APPROXIMATE")
        interval = parse_number("3.2～4.1").json_value()
        self.assertEqual(interval, {"lower": 3.2, "upper": 4.1, "qualifier": "RANGE"})
        self.assertEqual(normalize_date("2026"), {"value": "2026", "precision": "YEAR"})
        self.assertEqual(normalize_date("2026年08月"), {"value": "2026-08", "precision": "MONTH"})

    def test_pressure_basis_and_missing_coordinate_system_are_blocked(self) -> None:
        with self.assertRaisesRegex(ValueError, "绝压"):
            convert_unit(Decimal("1"), "MPa_abs", "MPa")
        with self.assertRaisesRegex(ValueError, "坐标系"):
            normalize_coordinate([115.1, 29.7], None)

    def test_percent_without_percent_or_column_unit_is_not_divided(self) -> None:
        rows, issues = normalize_candidates(
            [
                {
                    "candidate_id": "CAND-RATIO",
                    "field_id": "ratio.field",
                    "entity": {"entity_type": "SEGMENT", "entity_key": "SEG-1"},
                    "raw_value": "8",
                    "parsed_value": None,
                    "normalized_value": None,
                    "source_unit": None,
                    "canonical_unit": "fraction",
                    "confidence": 0.9,
                    "extraction_method": "MODEL_EXTRACTION",
                    "evidence_ids": ["EVD-1"],
                    "quality_status": "PENDING_REVIEW",
                    "review_status": "PENDING",
                    "model_or_rule_versions": {"model": "fixture"},
                }
            ],
            fields={
                "ratio.field": {
                    "value_type": "number",
                    "canonical_unit": "fraction",
                    "required_level": "OPTIONAL",
                }
            },
            unit_registry={"units": [{"symbol": "%", "allowed_fields": ["*"]}]},
        )
        self.assertEqual(rows[0]["quality_status"], "INVALID")
        self.assertIn("百分数缺少", issues[0]["message"])


if __name__ == "__main__":
    unittest.main()
