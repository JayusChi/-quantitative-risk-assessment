"""PNG/JPEG parser with bounded preprocessing, quality flags and OCR."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..contracts import IssueSeverity
from ..image_processing.preprocess import preprocess_image
from ..ocr.ports import OcrProviderError, OcrRequest
from ..parsing.compatibility import legacy_read
from ..parsing.contracts import (
    BoundingBox,
    CoordinateSpace,
    ImageRegion,
    ParsedCell,
    ParsedDocument,
    ParsedPage,
    ParsedTable,
    ParseIssue,
    PreviewResource,
    ReaderOutput,
    TextBlock,
    source_fragment_sha256,
)
from ..parsing.layout import infer_table_from_blocks
from ..parsing.registry import ParseContext


class ImageReader:
    reader_id = "image/pillow-ocr"
    parser_version = "1.0.0"
    media_types = frozenset({"image/png", "image/jpeg"})

    def parse(self, path: Path, context: ParseContext) -> ReaderOutput:
        if context.page_progress:
            context.page_progress(1, 1, "正在预处理并识别图片")
        content = path.read_bytes()
        processed = preprocess_image(content)
        issues: list[ParseIssue] = []
        for code, flag, message in (
            ("PARSE.IMAGE_BLURRY", processed.quality.blurry, "图片清晰度低"),
            ("PARSE.IMAGE_OVEREXPOSED", processed.quality.overexposed, "图片过曝"),
            ("PARSE.IMAGE_UNDEREXPOSED", processed.quality.underexposed, "图片欠曝"),
            ("PARSE.IMAGE_SKEWED", processed.quality.skewed, "图片存在明显倾斜"),
            (
                "PARSE.IMAGE_CONTENT_CLIPPED",
                processed.quality.clipped_content,
                "图片内容疑似触及边界或被截切",
            ),
        ):
            if flag:
                issues.append(ParseIssue(code, message, IssueSeverity.WARNING, page_number=1))
        if processed.quality.low_resolution:
            issues.append(
                ParseIssue(
                    "PARSE.IMAGE_LOW_RESOLUTION",
                    "图片短边小于600像素",
                    IssueSeverity.WARNING,
                    page_number=1,
                )
            )
        request = OcrRequest(
            image_bytes=processed.image_bytes,
            width=processed.width,
            height=processed.height,
            languages=context.languages,
            detect_tables=True,
            request_id=f"{context.source.source_id}:page-1",
            timeout_seconds=context.ocr_timeout_seconds,
        )
        blocks: list[TextBlock] = []
        tables: list[ParsedTable] = []
        ocr_metadata: dict[str, object] = {}
        inverse = processed.original_to_processed.inverse()
        try:
            response, _cache_hit = context.ocr_service.recognize(request)
        except OcrProviderError as exc:
            issues.extend(
                (
                    ParseIssue(
                        "PARSE.OCR_REQUIRED",
                        "图片没有原生文本，需要OCR才能形成机器可读内容",
                        IssueSeverity.ERROR,
                        page_number=1,
                    ),
                    ParseIssue(
                        exc.code,
                        str(exc),
                        IssueSeverity.ERROR,
                        page_number=1,
                        retryable=exc.retryable,
                    ),
                )
            )
        else:
            for index, item in enumerate(response.text_blocks, start=1):
                bbox = inverse.box(item.bbox)
                block_id = f"page-1-ocr-{index}"
                blocks.append(
                    TextBlock(
                        block_id=block_id,
                        text=item.text,
                        normalized_text=" ".join(item.text.split()),
                        reading_order=index,
                        block_type=item.block_type,
                        extraction_method="OCR_IMAGE",
                        source_fragment_sha256=source_fragment_sha256(
                            [response.raw_response_sha256, index, item.text]
                        ),
                        page_number=1,
                        bbox=bbox,
                        coordinate_space=CoordinateSpace.IMAGE_PIXELS_TOP_LEFT,
                        page_width=float(processed.original_width),
                        page_height=float(processed.original_height),
                        confidence=item.confidence,
                        language=item.language,
                    )
                )
                if item.confidence < context.low_confidence_threshold:
                    issues.append(
                        ParseIssue(
                            "PARSE.LOW_TEXT_CONFIDENCE",
                            f"OCR文本块置信度{item.confidence:.3f}低于阈值",
                            IssueSeverity.WARNING,
                            page_number=1,
                            object_id=block_id,
                        )
                    )
            for table_index, table in enumerate(response.tables, start=1):
                cells = tuple(
                    ParsedCell(
                        row_index=cell.row_index,
                        column_index=cell.column_index,
                        address=f"R{cell.row_index + 1}C{cell.column_index + 1}",
                        raw_value=cell.text,
                        display_text=cell.text,
                        value_type="EMPTY_STRING" if cell.text == "" else "STRING",
                        source_location=(
                            f"page:1;ocr-table:{table_index};row:{cell.row_index + 1};"
                            f"column:{cell.column_index + 1}"
                        ),
                        extraction_method="OCR_TABLE",
                        confidence=cell.confidence,
                        row_span=cell.row_span,
                        column_span=cell.column_span,
                        bbox=inverse.box(cell.bbox) if cell.bbox else None,
                        coordinate_space=CoordinateSpace.IMAGE_PIXELS_TOP_LEFT,
                        source_fragment_sha256=source_fragment_sha256(
                            [
                                response.raw_response_sha256,
                                table_index,
                                cell.row_index,
                                cell.column_index,
                                cell.text,
                            ]
                        ),
                    )
                    for cell in table.cells
                )
                tables.append(
                    ParsedTable(
                        table_id=f"page-1-table-{table_index}",
                        row_count=table.row_count,
                        column_count=table.column_count,
                        cells=cells,
                        extraction_method="OCR_TABLE",
                        confidence=table.confidence,
                        page_number=1,
                        bbox=inverse.box(table.bbox) if table.bbox else None,
                        coordinate_space=CoordinateSpace.IMAGE_PIXELS_TOP_LEFT,
                        metadata={"legacy_name": f"Page 1 Table {table_index}"},
                    )
                )
            if not tables:
                inferred = infer_table_from_blocks(
                    blocks,
                    table_id="page-1-table-inferred-1",
                    page_number=1,
                    source_prefix="page:1;ocr-image:1",
                )
                if inferred is not None:
                    tables.append(inferred)
            if not blocks and not tables:
                issues.append(
                    ParseIssue(
                        "PARSE.NO_CONTENT",
                        "OCR提供方没有返回文本块或表格",
                        IssueSeverity.ERROR,
                        page_number=1,
                    )
                )
            ocr_metadata = {
                "provider_id": response.provider_id,
                "model_version": response.model_version,
                "raw_response_sha256": response.raw_response_sha256,
                "provider_request_id": response.provider_request_id,
                "warnings": list(response.warnings),
            }
        image = ImageRegion(
            image_id="image-1",
            source_part=path.name,
            pixel_width=processed.original_width,
            pixel_height=processed.original_height,
            content_sha256=hashlib.sha256(content).hexdigest(),
            page_number=1,
            bbox=BoundingBox(0, 0, processed.original_width, processed.original_height),
            coordinate_space=CoordinateSpace.IMAGE_PIXELS_TOP_LEFT,
            preview_ref="previews/page-1.png",
            ocr_block_ids=tuple(block.block_id for block in blocks),
            original_to_processed=processed.original_to_processed.values(),
            processing_steps=processed.processing_steps,
        )
        page = ParsedPage(
            page_number=1,
            width=float(processed.original_width),
            height=float(processed.original_height),
            coordinate_space=CoordinateSpace.IMAGE_PIXELS_TOP_LEFT,
            classification="IMAGE",
            text_blocks=tuple(blocks),
            table_ids=tuple(table.table_id for table in tables),
            image_ids=(image.image_id,),
            ocr_character_count=sum(len(block.text) for block in blocks),
            image_coverage_ratio=1.0,
            metadata={"quality": processed.quality.to_dict()},
        )
        document = ParsedDocument(
            document_id=context.source.source_id,
            source=context.source,
            media_type=context.media_type,
            document_kind="IMAGE",
            parser_id=self.reader_id,
            parser_version=self.parser_version,
            page_count=1,
            pages=(page,),
            tables=tuple(tables),
            images=(image,),
            metadata={
                "original_format": path.suffix.casefold().lstrip("."),
                "preprocessing_version": "qra.image-preprocess/1.0.0",
                "exif_orientation": processed.exif_orientation,
                "quality": processed.quality.to_dict(),
                "ocr": ocr_metadata,
            },
            issues=tuple(issues),
        )
        return ReaderOutput(
            document,
            (PreviewResource("previews/page-1.png", "image/png", processed.image_bytes),),
        )

    def supports(self, path: Path) -> bool:
        return path.suffix.casefold() in {".png", ".jpg", ".jpeg"}

    def read(self, path: Path):
        media_type = "image/png" if path.suffix.casefold() == ".png" else "image/jpeg"
        return legacy_read(self, path, media_type)


__all__ = ["ImageReader"]
