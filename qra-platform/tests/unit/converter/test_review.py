from __future__ import annotations

import unittest

from qra_converter.mapping.mapper import MappedTable, MappingOutcome
from qra_converter.review import ReviewBundle, ReviewDecision, merge_mapped_tables


def _record(
    value: float,
    *,
    file_name: str,
    checksum: str,
    priority: int,
    confidence: float = 1.0,
) -> dict:
    return {
        "record_id": "R-001",
        "chainage_km": 0.5,
        "off_potential_v": value,
        "source_ref": {
            "file_sha256": checksum,
            "file_name": file_name,
            "sheet_name": "CIPS",
            "row_number": 2,
            "mapping_profile": "test.v1",
        },
        "quality": "A",
        "review_status": "AUTO_MAPPED",
        "_source_priority": priority,
        "_mapping_confidence": confidence,
        "_requires_review": confidence < 0.8,
        "_extraction_method": "STRUCTURED_TABLE",
    }


DEFINITION = {
    "id": "cips",
    "target": "raw_data_categories.cips",
    "record_key": ["record_id"],
    "fields": [
        {"target": "record_id", "aliases": ["编号"], "type": "string"},
        {"target": "chainage_km", "aliases": ["里程"], "type": "number"},
        {"target": "off_potential_v", "aliases": ["电位"], "type": "number"},
    ],
}


def _outcome(*records: dict) -> MappingOutcome:
    return MappingOutcome(
        defaults={},
        tables=(MappedTable(DEFINITION, tuple(records)),),
        issues=(),
        lineage=(),
        matched_table_keys=(),
    )


class MultiSourceReviewTests(unittest.TestCase):
    def test_exact_duplicate_records_are_merged_with_all_sources(self) -> None:
        result = merge_mapped_tables(
            _outcome(
                _record(-0.95, file_name="A.csv", checksum="a" * 64, priority=100),
                _record(-0.95, file_name="B.csv", checksum="b" * 64, priority=50),
            ),
            ReviewBundle({}),
        )
        records = result.outcome.tables[0].records
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0]["source_refs"]), 2)
        self.assertFalse(result.review_items)

    def test_conflicting_value_uses_priority_only_as_review_proposal(self) -> None:
        pending = merge_mapped_tables(
            _outcome(
                _record(-0.95, file_name="主数据.csv", checksum="a" * 64, priority=100),
                _record(-0.85, file_name="补充.csv", checksum="b" * 64, priority=50),
            ),
            ReviewBundle({}),
        )
        self.assertEqual(len(pending.review_items), 1)
        item = pending.review_items[0]
        self.assertEqual(item.kind, "SOURCE_VALUE_CONFLICT")
        self.assertEqual(item.proposed_value, -0.95)
        self.assertTrue(item.blocking)

        decision = ReviewDecision(
            review_id=item.review_id,
            action="REPLACE_VALUE",
            reviewer="测试复核人",
            reviewed_at="2026-08-25T16:00:00+08:00",
            reason="以现场复测值为准",
            value=-0.9,
        )
        resolved = merge_mapped_tables(
            _outcome(
                _record(-0.95, file_name="主数据.csv", checksum="a" * 64, priority=100),
                _record(-0.85, file_name="补充.csv", checksum="b" * 64, priority=50),
            ),
            ReviewBundle({item.review_id: decision}),
        )
        self.assertFalse(resolved.review_items)
        self.assertEqual(resolved.outcome.tables[0].records[0]["off_potential_v"], -0.9)
        self.assertEqual(resolved.audit[0].before_value, -0.95)
        self.assertEqual(resolved.audit[0].after_value, -0.9)

    def test_low_confidence_record_stays_pending_until_confirmed(self) -> None:
        source = _record(
            -0.95,
            file_name="提取表格.docx",
            checksum="c" * 64,
            priority=0,
            confidence=0.7,
        )
        pending = merge_mapped_tables(_outcome(source), ReviewBundle({}))
        item = pending.review_items[0]
        self.assertEqual(item.kind, "LOW_CONFIDENCE_MAPPING")
        decision = ReviewDecision(
            review_id=item.review_id,
            action="CONFIRM_RECORD",
            reviewer="测试复核人",
            reviewed_at="2026-08-25T16:00:00+08:00",
            reason="已对照原文逐项确认",
        )
        resolved = merge_mapped_tables(_outcome(source), ReviewBundle({item.review_id: decision}))
        self.assertFalse(resolved.review_items)
        self.assertEqual(
            resolved.outcome.tables[0].records[0]["review_status"],
            "HUMAN_CONFIRMED",
        )


if __name__ == "__main__":
    unittest.main()
