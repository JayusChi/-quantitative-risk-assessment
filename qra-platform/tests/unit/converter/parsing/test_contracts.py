from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qra_converter.contracts import SourceReference
from qra_converter.parsing.cache import ParsingCache, parsing_cache_key
from qra_converter.parsing.contracts import (
    BoundingBox,
    CoordinateSpace,
    ParsedCell,
    ParsedDocument,
    ParsedPage,
    ParsedTable,
    TextBlock,
)
from qra_converter.parsing.geometry import (
    AffineTransform,
    pdf_bottom_left_to_top_left,
    validate_bbox,
)
from qra_converter.parsing.layout import infer_table_from_blocks
from qra_converter.parsing.quality import build_quality_report, link_table_continuations


class ParsingContractTests(unittest.TestCase):
    @staticmethod
    def document() -> ParsedDocument:
        source = SourceReference("SOURCE-1", "source.csv", "test", "a" * 64)
        return ParsedDocument(
            document_id="SOURCE-1",
            source=source,
            media_type="text/csv",
            document_kind="CSV",
            parser_id="test",
            parser_version="1",
            page_count=1,
            pages=(
                ParsedPage(
                    1,
                    1,
                    1,
                    CoordinateSpace.WORKSHEET_GRID,
                    "STRUCTURED",
                    table_ids=("table-1",),
                ),
            ),
            tables=(
                ParsedTable(
                    "table-1",
                    1,
                    1,
                    (
                        ParsedCell(
                            0,
                            0,
                            "A1",
                            "001",
                            "001",
                            "STRING",
                            "sheet:S;cell:A1",
                            "CSV_NATIVE",
                            bbox=BoundingBox(0, 0, 1, 1),
                            coordinate_space=CoordinateSpace.WORKSHEET_GRID,
                        ),
                    ),
                    "CSV_NATIVE",
                    1.0,
                    page_number=1,
                ),
            ),
        )

    def test_canonical_hash_excludes_processing_runtime(self) -> None:
        first = self.document().finalized()
        second = self.document().finalized()
        self.assertEqual(first.parse_sha256, second.parse_sha256)
        self.assertNotEqual(
            build_quality_report(first, 1)["summary"]["processing_ms"],
            build_quality_report(second, 999)["summary"]["processing_ms"],
        )
        self.assertEqual(first.parse_sha256, second.parse_sha256)

    def test_coordinate_conversion_validation_and_inverse(self) -> None:
        converted = pdf_bottom_left_to_top_left(x0=10, y0=20, x1=30, y1=60, page_height=100)
        self.assertEqual(converted, BoundingBox(10, 40, 20, 40))
        transform = AffineTransform(0, 1, -1, 0, 100, 0)
        original = BoundingBox(10, 20, 30, 40)
        restored = transform.inverse().box(transform.box(original))
        self.assertAlmostEqual(restored.x, original.x)
        self.assertAlmostEqual(restored.y, original.y)
        self.assertTrue(validate_bbox(converted, width=100, height=100))
        self.assertFalse(validate_bbox(BoundingBox(-1, 0, 1, 1), width=100, height=100))

    def test_cache_key_binds_every_behavior_version_and_parameters(self) -> None:
        arguments = {
            "source_sha256": "a" * 64,
            "parser_id": "csv",
            "parser_version": "1",
            "ocr_provider_id": "fixture",
            "ocr_model_version": "1",
            "preprocessing_profile": "1",
            "contract_version": "1",
            "ocr_parameters": {"languages": ["zh-Hans"], "detect_tables": True},
        }
        first = parsing_cache_key(**arguments)
        second = parsing_cache_key(**{**arguments, "parser_version": "2"})
        third = parsing_cache_key(
            **{**arguments, "ocr_parameters": {"languages": ["en"], "detect_tables": True}}
        )
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, third)

    def test_cache_restore_requires_manifest_and_core_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = ParsingCache(root / "cache")
            source = root / "source"
            source.mkdir()
            for name in (
                "parsed_document.json",
                "quality_report.json",
                "preview_manifest.json",
            ):
                (source / name).write_text("{}", encoding="utf-8")
            key = "b" * 64
            cache.store(key, source)
            self.assertTrue(cache.restore(key, root / "restored"))

    def test_cross_page_relation_preserves_both_tables(self) -> None:
        base = self.document()
        first = base.tables[0]
        second = ParsedTable(
            "table-2",
            1,
            1,
            first.cells,
            "PDF_NATIVE_TABLE",
            0.9,
            page_number=2,
        )
        linked = link_table_continuations(
            ParsedDocument(
                **{
                    **base.__dict__,
                    "document_kind": "PDF",
                    "page_count": 2,
                    "tables": (first, second),
                }
            )
        )
        self.assertEqual(len(linked.tables), 2)
        self.assertEqual(linked.tables[1].continuation_of, "table-1")

    def test_coordinate_table_inference_is_conservative_and_confidence_capped(self) -> None:
        blocks = tuple(
            TextBlock(
                block_id=f"b{row}{column}",
                text=f"{row}-{column}",
                normalized_text=None,
                reading_order=row * 2 + column,
                block_type="WORD",
                extraction_method="OCR_IMAGE",
                source_fragment_sha256="f" * 64,
                page_number=1,
                bbox=BoundingBox(column * 100, row * 40, 50, 20),
                coordinate_space=CoordinateSpace.IMAGE_PIXELS_TOP_LEFT,
                confidence=0.95,
            )
            for row in range(2)
            for column in range(2)
        )
        inferred = infer_table_from_blocks(
            blocks,
            table_id="inferred",
            page_number=1,
            source_prefix="page:1",
        )
        self.assertIsNotNone(inferred)
        self.assertLessEqual(inferred.confidence, 0.6)
        self.assertEqual(inferred.rule_version, "coordinate-grid-v1")


if __name__ == "__main__":
    unittest.main()
