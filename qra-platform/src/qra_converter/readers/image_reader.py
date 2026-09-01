"""PNG/JPEG parser with bounded preprocessing, quality flags and OCR."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..contracts import IssueSeverity
from ..image_processing.preprocess import preprocess_image
from ..ocr.adaptive import recognize_preprocessed_image
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
    parser_version = "2.0.0"
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
        blocks: list[TextBlock] = []
        tables: list[ParsedTable] = []
        ocr_metadata: dict[str, object] = {}
        adaptive = recognize_preprocessed_image(
            processed,
            service=context.ocr_service,
            source_id=context.source.source_id,
            page_number=1,
            request_prefix=f"{context.source.source_id}:page-1",
            languages=context.languages,
            timeout_seconds=context.ocr_timeout_seconds,
            region_kind="BODY",
            task_type="advanced_recognition",
            detect_tables=True,
            progress=(
                (lambda done, total: context.page_progress(done, total, "正在识别图片切片"))
                if context.page_progress
                else None
            ),
        )
        response = adaptive.merged
        issue_messages = {
            "PARSE.OCR_REQUEST_TOO_LARGE": "OCR区域在有界压缩和切片后仍超过外发预算",
            "PARSE.OCR_TILE_LIMIT_EXCEEDED": "OCR切片达到单页安全上限",
            "PARSE.OCR_TILE_FAILED": "一个或多个OCR切片识别失败，已保留成功区域",
            "PARSE.OCR_PARTIAL": "OCR仅部分成功，需要人工复核",
            "PARSE.OCR_OUTPUT_TRUNCATED": "OCR输出达到长度上限，已尝试缩小区域",
            "PARSE.OCR_OVERLAP_CONFLICT": "OCR重叠区域返回冲突文字，已全部保留",
            "PARSE.OCR_PROVIDER_NOT_CONFIGURED": "未配置OCR提供方",
            "PARSE.OCR_AUTHENTICATION_FAILED": "OCR提供方认证失败",
            "PARSE.OCR_RATE_LIMITED": "OCR提供方限流且重试耗尽",
            "PARSE.OCR_TIMEOUT": "OCR调用超时且重试耗尽",
            "PARSE.OCR_OUTPUT_INVALID": "OCR输出不符合合同",
            "PARSE.OCR_UNREADABLE": "OCR提供方无法读取图像",
        }
        if response.status == "FAILED":
            issues.append(
                ParseIssue(
                    "PARSE.OCR_REQUIRED",
                    "图片没有原生文本，需要OCR才能形成机器可读内容",
                    IssueSeverity.ERROR,
                    page_number=1,
                )
            )
        for code in response.issues:
            issues.append(
                ParseIssue(
                    code,
                    issue_messages.get(code, "OCR区域处理出现结构化问题"),
                    (
                        IssueSeverity.ERROR
                        if response.status == "FAILED"
                        else IssueSeverity.WARNING
                    ),
                    page_number=1,
                    retryable=code in {"PARSE.OCR_RATE_LIMITED", "PARSE.OCR_TIMEOUT"},
                )
            )
        if response.status != "FAILED":
            for index, item in enumerate(response.text_blocks, start=1):
                bbox = item.bbox
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
                            [
                                (
                                    adaptive.attempted_units[0].sha256
                                    if adaptive.attempted_units
                                    else ""
                                ),
                                index,
                                item.text,
                            ]
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
                        bbox=cell.bbox,
                        coordinate_space=CoordinateSpace.IMAGE_PIXELS_TOP_LEFT,
                        source_fragment_sha256=source_fragment_sha256(
                            [
                                (
                                    adaptive.attempted_units[0].sha256
                                    if adaptive.attempted_units
                                    else ""
                                ),
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
                        bbox=table.bbox,
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
                    table_adaptive = recognize_preprocessed_image(
                        processed,
                        service=context.ocr_service,
                        source_id=context.source.source_id,
                        page_number=1,
                        request_prefix=f"{context.source.source_id}:page-1:table-1",
                        languages=context.languages,
                        timeout_seconds=context.ocr_timeout_seconds,
                        region_kind="TABLE",
                        task_type="table_parsing",
                        detect_tables=True,
                    )
                    for table_index, table in enumerate(
                        table_adaptive.merged.tables, start=1
                    ):
                        cells = tuple(
                            ParsedCell(
                                row_index=cell.row_index,
                                column_index=cell.column_index,
                                address=f"R{cell.row_index + 1}C{cell.column_index + 1}",
                                raw_value=cell.text,
                                display_text=cell.text,
                                value_type="EMPTY_STRING" if cell.text == "" else "STRING",
                                source_location=(
                                    f"page:1;ocr-table-region:1;table:{table_index};"
                                    f"row:{cell.row_index + 1};column:{cell.column_index + 1}"
                                ),
                                extraction_method="OCR_TABLE",
                                confidence=cell.confidence,
                                row_span=cell.row_span,
                                column_span=cell.column_span,
                                bbox=cell.bbox,
                                coordinate_space=CoordinateSpace.IMAGE_PIXELS_TOP_LEFT,
                                source_fragment_sha256=source_fragment_sha256(
                                    [table_index, cell.row_index, cell.column_index, cell.text]
                                ),
                            )
                            for cell in table.cells
                        )
                        tables.append(
                            ParsedTable(
                                table_id=f"page-1-table-region-{table_index}",
                                row_count=table.row_count,
                                column_count=table.column_count,
                                cells=cells,
                                extraction_method="OCR_TABLE",
                                confidence=table.confidence,
                                page_number=1,
                                bbox=table.bbox,
                                coordinate_space=CoordinateSpace.IMAGE_PIXELS_TOP_LEFT,
                                metadata={"recognition_mode": "table_parsing"},
                            )
                        )
                    if not tables:
                        tables.append(inferred)
                        issues.append(
                            ParseIssue(
                                "PARSE.OCR_TABLE_FALLBACK",
                                "表格专用任务失败，已使用坐标聚类推测表格并限制置信度",
                                IssueSeverity.WARNING,
                                page_number=1,
                                object_id=inferred.table_id,
                            )
                        )
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
                "provider_id": context.ocr_service.provider.provider_id,
                "model_version": context.ocr_service.provider.model_version,
                "payload_policy_version": (
                    adaptive.plan.units[0].payload_policy_version
                    if adaptive.plan.units
                    else None
                ),
                "status": response.status,
                "adapted": adaptive.adapted,
                "tile_count": len(adaptive.plan.units),
                "attempted_unit_count": len(adaptive.attempted_units),
                "units": [
                    {
                        "unit_id": unit.unit_id,
                        "tile_index": unit.tile_index,
                        "tile_count": unit.tile_count,
                        "region_kind": unit.region_kind,
                        "sha256": unit.sha256,
                        "content_type": unit.content_type,
                        "width": unit.width,
                        "height": unit.height,
                        "encoded_byte_count": unit.encoded_byte_count,
                        "bbox_in_processed_image": [
                            unit.bbox_in_processed_image.x,
                            unit.bbox_in_processed_image.y,
                            unit.bbox_in_processed_image.width,
                            unit.bbox_in_processed_image.height,
                        ],
                        "tile_to_processed_transform": unit.tile_to_processed_transform.values(),
                        "processed_to_original_transform": (
                            unit.processed_to_original_transform.values()
                        ),
                    }
                    for unit in adaptive.plan.units
                ],
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
            processing_steps=tuple(
                step
                for unit in adaptive.plan.units
                for step in unit.processing_steps
            ) or processed.processing_steps,
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
            metadata={
                "quality": processed.quality.to_dict(),
                "ocr_status": response.status,
                "ocr_tile_count": len(adaptive.plan.units),
            },
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
