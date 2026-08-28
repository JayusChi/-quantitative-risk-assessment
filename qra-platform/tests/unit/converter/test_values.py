from __future__ import annotations

import unittest

from qra_converter.mapping.values import ValueConversionError, convert_value


class ValueConversionTests(unittest.TestCase):
    def test_supported_unit_conversions(self) -> None:
        self.assertEqual(
            convert_value(
                1500,
                {"type": "number", "source_unit": "m", "target_unit": "km"},
            ),
            1.5,
        )
        self.assertAlmostEqual(
            convert_value(
                15.5,
                {"type": "number", "source_unit": "℃", "target_unit": "K"},
            ),
            288.65,
        )
        self.assertEqual(
            convert_value(
                "8%",
                {
                    "type": "number",
                    "source_unit": "%",
                    "target_unit": "fraction",
                    "minimum": 0,
                    "maximum": 1,
                },
            ),
            0.08,
        )

    def test_ambiguous_number_is_rejected(self) -> None:
        with self.assertRaises(ValueConversionError):
            convert_value(
                "3.0-3.8",
                {"type": "number", "source_unit": "MPa", "target_unit": "MPa"},
            )

    def test_explicit_source_unit_suffix_is_stripped(self) -> None:
        self.assertEqual(
            convert_value(
                "273mm",
                {
                    "type": "number",
                    "source_unit": "mm",
                    "target_unit": "mm",
                    "source_unit_aliases": ["mm", "毫米"],
                    "strip_source_unit_suffix": True,
                },
            ),
            273.0,
        )
        with self.assertRaises(ValueConversionError):
            convert_value(
                "273cm",
                {
                    "type": "number",
                    "source_unit": "mm",
                    "target_unit": "mm",
                    "source_unit_aliases": ["mm", "毫米"],
                    "strip_source_unit_suffix": True,
                },
            )

    def test_blank_is_not_silently_converted_to_zero(self) -> None:
        with self.assertRaises(ValueConversionError):
            convert_value("", {"type": "number"})

    def test_unknown_unit_is_rejected(self) -> None:
        with self.assertRaises(ValueConversionError):
            convert_value(
                100,
                {"type": "number", "source_unit": "psi", "target_unit": "MPa"},
            )

    def test_chainage_notation_is_parsed_deterministically(self) -> None:
        self.assertEqual(
            convert_value(
                "JJ041G（10+938）",
                {
                    "type": "chainage",
                    "source_unit": "km",
                    "target_unit": "km",
                },
            ),
            10.938,
        )


if __name__ == "__main__":
    unittest.main()
