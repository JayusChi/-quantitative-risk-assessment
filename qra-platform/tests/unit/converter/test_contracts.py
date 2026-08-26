from __future__ import annotations

import unittest

from qra_converter import ConversionIssue, ConversionResult, IssueSeverity


class ConversionContractTests(unittest.TestCase):
    def test_error_blocks_draft_from_review(self) -> None:
        result = ConversionResult(
            payload={"schema_version": "1.0.0"},
            mapping_version="customer-a/v1",
            issues=(
                ConversionIssue(
                    severity=IssueSeverity.ERROR,
                    code="REQUIRED_FIELD_MISSING",
                    message="缺少管段编号",
                    target_path="segments[*].segment_id",
                ),
            ),
        )

        self.assertTrue(result.has_errors)
        self.assertEqual(result.to_dict()["status"], "BLOCKED")

    def test_warning_keeps_draft_reviewable(self) -> None:
        result = ConversionResult(
            payload={"schema_version": "1.0.0"},
            mapping_version="customer-a/v1",
            issues=(
                ConversionIssue(
                    severity=IssueSeverity.WARNING,
                    code="UNIT_INFERRED",
                    message="根据模板版本推断压力单位",
                ),
            ),
        )

        self.assertFalse(result.has_errors)
        self.assertEqual(result.to_dict()["status"], "READY_FOR_REVIEW")


if __name__ == "__main__":
    unittest.main()
