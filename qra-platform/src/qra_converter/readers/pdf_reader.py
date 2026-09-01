"""Per-page native/MIXED/SCAN PDF parser with coordinate-bound OCR."""

from __future__ import annotations

import hashlib
import io
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..contracts import IssueSeverity
from ..image_processing.preprocess import preprocess_image
from ..ocr.adaptive import recognize_preprocessed_image
from ..parsing.compatibility import legacy_read
from ..parsing.contracts import (
    BoundingBox,
    CoordinateSpace,
    ExtractionCandidate,
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


def _overlap_ratio(first: BoundingBox, second: BoundingBox) -> float:
    width = max(0.0, min(first.right, second.right) - max(first.x, second.x))
    height = max(0.0, min(first.bottom, second.bottom) - max(first.y, second.y))
    intersection = width * height
    denominator = max(1e-12, min(first.width * first.height, second.width * second.height))
    return intersection / denominator


def _map_image_box_to_page(
    bbox: BoundingBox,
    *,
    image_width: int,
    image_height: int,
    page_bbox: BoundingBox,
) -> BoundingBox:
    return BoundingBox(
        page_bbox.x + bbox.x / image_width * page_bbox.width,
        page_bbox.y + bbox.y / image_height * page_bbox.height,
        bbox.width / image_width * page_bbox.width,
        bbox.height / image_height * page_bbox.height,
    )


def _parsed_ocr_table(
    table: Any,
    *,
    table_id: str,
    page_number: int,
    image_index: int,
    image_id: str,
    image_width: int,
    image_height: int,
    page_bbox: BoundingBox,
    response_hash: str,
    recognition_mode: str,
) -> ParsedTable:
    cells = []
    for cell in table.cells:
        cell_bbox = (
            _map_image_box_to_page(
                cell.bbox,
                image_width=image_width,
                image_height=image_height,
                page_bbox=page_bbox,
            )
            if cell.bbox
            else None
        )
        cells.append(
            ParsedCell(
                row_index=cell.row_index,
                column_index=cell.column_index,
                address=f"R{cell.row_index + 1}C{cell.column_index + 1}",
                raw_value=cell.text,
                display_text=cell.text,
                value_type="EMPTY_STRING" if cell.text == "" else "STRING",
                source_location=(
                    f"page:{page_number};ocr-image:{image_index};table:{table_id};"
                    f"row:{cell.row_index + 1};column:{cell.column_index + 1}"
                ),
                extraction_method="OCR_TABLE",
                confidence=cell.confidence,
                row_span=cell.row_span,
                column_span=cell.column_span,
                bbox=cell_bbox,
                coordinate_space=CoordinateSpace.PDF_POINTS_TOP_LEFT,
                source_fragment_sha256=source_fragment_sha256(
                    [
                        response_hash,
                        table_id,
                        cell.row_index,
                        cell.column_index,
                        cell.text,
                    ]
                ),
            )
        )
    table_bbox = (
        _map_image_box_to_page(
            table.bbox,
            image_width=image_width,
            image_height=image_height,
            page_bbox=page_bbox,
        )
        if table.bbox
        else None
    )
    return ParsedTable(
        table_id=table_id,
        row_count=table.row_count,
        column_count=table.column_count,
        cells=tuple(cells),
        extraction_method="OCR_TABLE",
        confidence=table.confidence,
        page_number=page_number,
        bbox=table_bbox,
        coordinate_space=CoordinateSpace.PDF_POINTS_TOP_LEFT,
        metadata={
            "legacy_name": f"Page {page_number} Table OCR {table_id}",
            "source_image_id": image_id,
            "recognition_mode": recognition_mode,
        },
    )


def _native_lines(words: list[dict[str, Any]], page_number: int, width: float, height: float):
    lines: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: (round(float(item["top"]) / 3), float(item["x0"]))):
        top = float(word["top"])
        if not lines or abs(float(lines[-1][0]["top"]) - top) > 3:
            lines.append([word])
        else:
            lines[-1].append(word)
    result: list[TextBlock] = []
    for index, line in enumerate(lines, start=1):
        ordered = sorted(line, key=lambda item: float(item["x0"]))
        text = " ".join(str(item.get("text") or "") for item in ordered).strip()
        if not text:
            continue
        x0 = min(float(item["x0"]) for item in ordered)
        x1 = max(float(item["x1"]) for item in ordered)
        top = min(float(item["top"]) for item in ordered)
        bottom = max(float(item["bottom"]) for item in ordered)
        result.append(
            TextBlock(
                block_id=f"page-{page_number}-native-{index}",
                text=text,
                normalized_text=" ".join(text.split()),
                reading_order=index,
                block_type="LINE",
                extraction_method="PDF_NATIVE_TEXT",
                source_fragment_sha256=source_fragment_sha256(
                    [page_number, x0, top, x1, bottom, text]
                ),
                page_number=page_number,
                bbox=BoundingBox(x0, top, x1 - x0, bottom - top),
                coordinate_space=CoordinateSpace.PDF_POINTS_TOP_LEFT,
                page_width=width,
                page_height=height,
                confidence=1.0,
            )
        )
    return result


class PdfReader:
    reader_id = "pdf/pdfplumber-pypdf-ocr"
    parser_version = "3.0.0"
    media_types = frozenset({"application/pdf"})

    def _failure(self, context: ParseContext, code: str, message: str) -> ReaderOutput:
        return ReaderOutput(
            ParsedDocument(
                document_id=context.source.source_id,
                source=context.source,
                media_type=context.media_type,
                document_kind="PDF_UNREADABLE",
                parser_id=self.reader_id,
                parser_version=self.parser_version,
                page_count=0,
                issues=(ParseIssue(code, message, IssueSeverity.ERROR),),
            )
        )

    def parse(self, path: Path, context: ParseContext) -> ReaderOutput:
        try:
            import pdfplumber
            from pypdf import PdfReader as PyPdfReader
            from pypdf.errors import PdfReadError
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("读取PDF需要安装pdfplumber>=0.11和pypdf>=5") from exc
        try:
            pypdf_document = PyPdfReader(str(path), strict=True)
            if pypdf_document.is_encrypted:
                return self._failure(
                    context,
                    "PARSE.ENCRYPTED_UNSUPPORTED",
                    "PDF已加密，解析层不尝试解密或处理密码",
                )
            plumber_document = pdfplumber.open(path)
        except (OSError, PdfReadError, ValueError) as exc:
            return self._failure(context, "PARSE.DOCUMENT_CORRUPT", f"PDF结构损坏：{exc}")

        issues: list[ParseIssue] = []
        pages: list[ParsedPage] = []
        parsed_tables: list[ParsedTable] = []
        images: list[ImageRegion] = []
        resources: list[PreviewResource] = []
        ocr_calls: list[dict[str, object]] = []
        try:
            if len(plumber_document.pages) != len(pypdf_document.pages):
                return self._failure(
                    context,
                    "PARSE.DOCUMENT_CORRUPT",
                    "PDF解析库对页数判断不一致",
                )
            for page_number, page in enumerate(plumber_document.pages, start=1):
                if context.cancel_check:
                    context.cancel_check()
                if context.page_progress:
                    context.page_progress(
                        page_number, len(plumber_document.pages), f"正在解析PDF第{page_number}页"
                    )
                width = float(page.width)
                height = float(page.height)
                if context.selected_pages is not None and page_number not in context.selected_pages:
                    pages.append(
                        ParsedPage(
                            page_number=page_number,
                            width=width,
                            height=height,
                            coordinate_space=CoordinateSpace.PDF_POINTS_TOP_LEFT,
                            classification="NOT_SELECTED",
                            metadata={"reextraction_scope": "PAGE_NOT_SELECTED"},
                        )
                    )
                    continue
                if width <= 0 or height <= 0 or width * height > 100_000_000:
                    issues.append(
                        ParseIssue(
                            "PARSE.LOCATION_INVALID",
                            "PDF页面尺寸无效或超过安全上限",
                            IssueSeverity.ERROR,
                            page_number=page_number,
                        )
                    )
                    pages.append(
                        ParsedPage(
                            page_number=page_number,
                            width=width,
                            height=height,
                            coordinate_space=CoordinateSpace.PDF_POINTS_TOP_LEFT,
                            classification="UNREADABLE",
                        )
                    )
                    continue
                try:
                    words = list(page.extract_words(use_text_flow=True, keep_blank_chars=False))
                except Exception as exc:
                    words = []
                    issues.append(
                        ParseIssue(
                            "PARSE.NATIVE_TEXT_EXTRACTION_FAILED",
                            f"PDF原生文本提取失败：{type(exc).__name__}",
                            IssueSeverity.WARNING,
                            page_number=page_number,
                        )
                    )
                native_blocks = _native_lines(words, page_number, width, height)
                native_characters = sum(len(block.text.replace(" ", "")) for block in native_blocks)
                valid_characters = sum(
                    character.isprintable() and character != "\ufffd"
                    for block in native_blocks
                    for character in block.text
                )
                total_characters = sum(len(block.text) for block in native_blocks)
                valid_ratio = valid_characters / total_characters if total_characters else 0.0
                page_image_boxes: list[BoundingBox] = []
                for raw_image in page.images:
                    try:
                        x0 = float(raw_image.get("x0", 0))
                        x1 = float(raw_image.get("x1", x0))
                        top = float(raw_image.get("top", 0))
                        bottom = float(raw_image.get("bottom", top))
                    except (TypeError, ValueError):
                        continue
                    page_image_boxes.append(
                        BoundingBox(x0, top, max(0, x1 - x0), max(0, bottom - top))
                    )
                image_coverage = min(
                    1.0,
                    sum(box.width * box.height for box in page_image_boxes)
                    / max(1.0, width * height),
                )
                sufficient_native = native_characters >= 20 and valid_ratio >= 0.75
                if sufficient_native and image_coverage >= 0.25:
                    classification = "MIXED"
                elif sufficient_native:
                    classification = "TEXT_NATIVE"
                elif image_coverage >= 0.25:
                    classification = "SCAN"
                else:
                    classification = "UNREADABLE"

                page_table_ids: list[str] = []
                if classification in {"TEXT_NATIVE", "MIXED"}:
                    try:
                        found_tables = list(page.find_tables())
                    except Exception:
                        found_tables = []
                    for table_number, found in enumerate(found_tables, start=1):
                        raw_rows = found.extract() or []
                        row_count = len(raw_rows)
                        column_count = max((len(row) for row in raw_rows), default=0)
                        raw_cell_boxes = list(getattr(found, "cells", []) or [])
                        cells: list[ParsedCell] = []
                        cell_position = 0
                        for row_index, row in enumerate(raw_rows):
                            for column_index in range(column_count):
                                value = row[column_index] if column_index < len(row) else None
                                box = None
                                if cell_position < len(raw_cell_boxes):
                                    raw_box = raw_cell_boxes[cell_position]
                                    if raw_box and len(raw_box) == 4:
                                        box = BoundingBox(
                                            float(raw_box[0]),
                                            float(raw_box[1]),
                                            float(raw_box[2]) - float(raw_box[0]),
                                            float(raw_box[3]) - float(raw_box[1]),
                                        )
                                cell_position += 1
                                cells.append(
                                    ParsedCell(
                                        row_index=row_index,
                                        column_index=column_index,
                                        address=f"R{row_index + 1}C{column_index + 1}",
                                        raw_value=value,
                                        display_text="" if value is None else str(value),
                                        value_type="EMPTY" if value is None else "STRING",
                                        source_location=(
                                            f"page:{page_number};native-table:{table_number};"
                                            f"row:{row_index + 1};column:{column_index + 1}"
                                        ),
                                        extraction_method="PDF_NATIVE_TABLE",
                                        confidence=0.9,
                                        bbox=box,
                                        coordinate_space=CoordinateSpace.PDF_POINTS_TOP_LEFT,
                                        source_fragment_sha256=source_fragment_sha256(
                                            [
                                                page_number,
                                                table_number,
                                                row_index,
                                                column_index,
                                                value,
                                            ]
                                        ),
                                    )
                                )
                        table_bbox = BoundingBox(
                            float(found.bbox[0]),
                            float(found.bbox[1]),
                            float(found.bbox[2]) - float(found.bbox[0]),
                            float(found.bbox[3]) - float(found.bbox[1]),
                        )
                        table_id = f"page-{page_number}-table-{table_number}"
                        parsed_tables.append(
                            ParsedTable(
                                table_id=table_id,
                                row_count=row_count,
                                column_count=column_count,
                                cells=tuple(cells),
                                extraction_method="PDF_NATIVE_TABLE",
                                confidence=0.9,
                                page_number=page_number,
                                bbox=table_bbox,
                                coordinate_space=CoordinateSpace.PDF_POINTS_TOP_LEFT,
                                metadata={
                                    "legacy_name": f"Page {page_number} Table {table_number}"
                                },
                            )
                        )
                        page_table_ids.append(table_id)

                displayed_blocks = list(native_blocks)
                page_image_ids: list[str] = []
                page_ocr_characters = 0
                page_ocr_statuses: list[str] = []
                table_region_candidates: list[tuple[Any, BoundingBox, int, str]] = []
                should_ocr = classification in {"MIXED", "SCAN"}
                extracted_images: list[tuple[bytes, str, int, int]] = []
                try:
                    for image_file in pypdf_document.pages[page_number - 1].images:
                        image_object = getattr(image_file, "image", None)
                        if image_object is not None:
                            image_width, image_height = image_object.size
                        else:
                            image_width = image_height = 0
                        extracted_images.append(
                            (
                                bytes(image_file.data),
                                str(image_file.name),
                                image_width,
                                image_height,
                            )
                        )
                except Exception:
                    extracted_images = []
                extracted_images.sort(key=lambda item: item[2] * item[3], reverse=True)
                sorted_boxes = sorted(
                    page_image_boxes, key=lambda box: box.width * box.height, reverse=True
                )
                if should_ocr and not extracted_images:
                    # Render this page only. PDF bytes and other pages never enter OcrRequest.
                    try:
                        rendered = page.to_image(resolution=150, antialias=True).original
                        buffer = io.BytesIO()
                        rendered.save(buffer, format="PNG", optimize=False)
                        extracted_images.append(
                            (buffer.getvalue(), f"rendered-page-{page_number}.png", *rendered.size)
                        )
                        sorted_boxes = [BoundingBox(0, 0, width, height)]
                    except Exception:
                        pass
                if should_ocr and not extracted_images:
                    severity = (
                        IssueSeverity.ERROR if classification == "SCAN" else IssueSeverity.WARNING
                    )
                    issues.append(
                        ParseIssue(
                            "PARSE.OCR_REQUIRED",
                            "页面需要OCR，但没有可安全解码的内嵌图像区域",
                            severity,
                            page_number=page_number,
                        )
                    )
                for image_index, image_data in enumerate(extracted_images, start=1):
                    if context.cancel_check:
                        context.cancel_check()
                    content, image_name, hinted_width, hinted_height = image_data
                    try:
                        processed = preprocess_image(content)
                    except ValueError:
                        continue
                    page_bbox = (
                        sorted_boxes[image_index - 1]
                        if image_index <= len(sorted_boxes)
                        else BoundingBox(0, 0, width, height)
                    )
                    image_id = f"page-{page_number}-image-{image_index}"
                    preview_ref = f"previews/{image_id}.png"
                    resources.append(
                        PreviewResource(preview_ref, "image/png", processed.image_bytes)
                    )
                    page_image_ids.append(image_id)
                    image_ocr_ids: list[str] = []
                    for code, flag, message in (
                        ("PARSE.IMAGE_BLURRY", processed.quality.blurry, "PDF图像区域清晰度低"),
                        (
                            "PARSE.IMAGE_OVEREXPOSED",
                            processed.quality.overexposed,
                            "PDF图像区域过曝",
                        ),
                        (
                            "PARSE.IMAGE_UNDEREXPOSED",
                            processed.quality.underexposed,
                            "PDF图像区域欠曝",
                        ),
                        (
                            "PARSE.IMAGE_SKEWED",
                            processed.quality.skewed,
                            "PDF图像区域存在明显倾斜",
                        ),
                        (
                            "PARSE.IMAGE_CONTENT_CLIPPED",
                            processed.quality.clipped_content,
                            "PDF图像内容疑似触及边界或被截切",
                        ),
                    ):
                        if flag:
                            issues.append(
                                ParseIssue(
                                    code,
                                    message,
                                    IssueSeverity.WARNING,
                                    page_number=page_number,
                                    object_id=image_id,
                                )
                            )
                    if should_ocr:
                        adaptive = recognize_preprocessed_image(
                            processed,
                            service=context.ocr_service,
                            source_id=context.source.source_id,
                            page_number=page_number,
                            request_prefix=(
                                f"{context.source.source_id}:page-{page_number}:image-{image_index}"
                            ),
                            languages=context.languages,
                            timeout_seconds=context.ocr_timeout_seconds,
                            region_kind="BODY",
                            task_type="advanced_recognition",
                            detect_tables=True,
                            progress=(
                                (
                                    lambda done, total, page=page_number: context.page_progress(
                                        page,
                                        len(plumber_document.pages),
                                        f"正在识别PDF第{page}页区域{done}/{total}",
                                    )
                                )
                                if context.page_progress
                                else None
                            ),
                        )
                        response = adaptive.merged
                        page_ocr_statuses.append(response.status)
                        for code in response.issues:
                            issues.append(
                                ParseIssue(
                                    code,
                                    {
                                        "PARSE.OCR_REQUEST_TOO_LARGE": (
                                            "PDF页内区域经有界压缩切片后仍超过外发预算"
                                        ),
                                        "PARSE.OCR_TILE_LIMIT_EXCEEDED": (
                                            "PDF页内OCR切片达到安全上限"
                                        ),
                                        "PARSE.OCR_TILE_FAILED": (
                                            "PDF页内一个或多个切片失败，已保留成功区域"
                                        ),
                                        "PARSE.OCR_PARTIAL": "PDF页内OCR部分成功，需要人工复核",
                                        "PARSE.OCR_OUTPUT_TRUNCATED": (
                                            "PDF页内OCR输出截断，已尝试缩小区域"
                                        ),
                                        "PARSE.OCR_OVERLAP_CONFLICT": (
                                            "PDF页内重叠区域文字冲突，已全部保留"
                                        ),
                                    }.get(code, "PDF页内OCR出现结构化问题"),
                                    (
                                        IssueSeverity.ERROR
                                        if response.status == "FAILED" and classification == "SCAN"
                                        else IssueSeverity.WARNING
                                    ),
                                    page_number=page_number,
                                    object_id=image_id,
                                    retryable=code
                                    in {"PARSE.OCR_RATE_LIMITED", "PARSE.OCR_TIMEOUT"},
                                )
                            )
                        if response.status != "FAILED":
                            response_hash = (
                                adaptive.responses[0].raw_response_sha256
                                if adaptive.responses
                                else adaptive.attempted_units[0].sha256
                            )
                            for ocr_index, ocr_block in enumerate(response.text_blocks, start=1):
                                pdf_bbox = _map_image_box_to_page(
                                    ocr_block.bbox,
                                    image_width=processed.original_width,
                                    image_height=processed.original_height,
                                    page_bbox=page_bbox,
                                )
                                block_id = f"{image_id}-ocr-{ocr_index}"
                                image_ocr_ids.append(block_id)
                                page_ocr_characters += len(ocr_block.text)
                                candidate = ExtractionCandidate(
                                    method="OCR_PDF_IMAGE",
                                    text=ocr_block.text,
                                    confidence=ocr_block.confidence,
                                    bbox=pdf_bbox,
                                    candidate_id=block_id,
                                    source_fragment_sha256=source_fragment_sha256(
                                        [
                                            response_hash,
                                            ocr_index,
                                            ocr_block.text,
                                        ]
                                    ),
                                )
                                overlap_index = next(
                                    (
                                        index
                                        for index, native in enumerate(displayed_blocks)
                                        if native.bbox
                                        and _overlap_ratio(native.bbox, pdf_bbox) >= 0.5
                                    ),
                                    None,
                                )
                                if overlap_index is not None:
                                    native = displayed_blocks[overlap_index]
                                    displayed_blocks[overlap_index] = replace(
                                        native,
                                        extraction_candidates=(
                                            *native.extraction_candidates,
                                            candidate,
                                        ),
                                    )
                                else:
                                    displayed_blocks.append(
                                        TextBlock(
                                            block_id=block_id,
                                            text=ocr_block.text,
                                            normalized_text=" ".join(ocr_block.text.split()),
                                            reading_order=len(displayed_blocks) + 1,
                                            block_type=ocr_block.block_type,
                                            extraction_method="OCR_PDF_IMAGE",
                                            source_fragment_sha256=source_fragment_sha256(
                                                [
                                                    response_hash,
                                                    ocr_index,
                                                    ocr_block.text,
                                                ]
                                            ),
                                            page_number=page_number,
                                            bbox=pdf_bbox,
                                            coordinate_space=CoordinateSpace.PDF_POINTS_TOP_LEFT,
                                            page_width=width,
                                            page_height=height,
                                            confidence=ocr_block.confidence,
                                            language=ocr_block.language,
                                        )
                                    )
                                if ocr_block.confidence < context.low_confidence_threshold:
                                    issues.append(
                                        ParseIssue(
                                            "PARSE.LOW_TEXT_CONFIDENCE",
                                            f"OCR文本块置信度{ocr_block.confidence:.3f}低于阈值",
                                            IssueSeverity.WARNING,
                                            page_number=page_number,
                                            object_id=block_id,
                                        )
                                    )
                            for ocr_table_index, table in enumerate(response.tables, start=1):
                                table_id = f"{image_id}-table-{ocr_table_index}"
                                parsed_tables.append(
                                    _parsed_ocr_table(
                                        table,
                                        table_id=table_id,
                                        page_number=page_number,
                                        image_index=image_index,
                                        image_id=image_id,
                                        image_width=processed.original_width,
                                        image_height=processed.original_height,
                                        page_bbox=page_bbox,
                                        response_hash=response_hash,
                                        recognition_mode="advanced_recognition",
                                    )
                                )
                                page_table_ids.append(table_id)
                            if response.text_blocks and not response.tables:
                                table_region_candidates.append(
                                    (processed, page_bbox, image_index, image_id)
                                )
                            for completed_response in adaptive.responses:
                                ocr_calls.append(
                                    {
                                        "page_number": page_number,
                                        "image_id": image_id,
                                        "region_kind": "BODY",
                                        "status": response.status,
                                        "provider_id": completed_response.provider_id,
                                        "model_version": completed_response.model_version,
                                        "raw_response_sha256": (
                                            completed_response.raw_response_sha256
                                        ),
                                        "provider_request_id": (
                                            completed_response.provider_request_id
                                        ),
                                        "payload_policy_version": (
                                            adaptive.plan.units[0].payload_policy_version
                                            if adaptive.plan.units
                                            else None
                                        ),
                                        "tile_count": len(adaptive.plan.units),
                                        "adapted": adaptive.adapted,
                                    }
                                )
                    images.append(
                        ImageRegion(
                            image_id=image_id,
                            source_part=f"page:{page_number};object:{image_name}",
                            pixel_width=processed.original_width or hinted_width,
                            pixel_height=processed.original_height or hinted_height,
                            content_sha256=hashlib.sha256(content).hexdigest(),
                            page_number=page_number,
                            bbox=page_bbox,
                            coordinate_space=CoordinateSpace.PDF_POINTS_TOP_LEFT,
                            preview_ref=preview_ref,
                            ocr_block_ids=tuple(image_ocr_ids),
                            original_to_processed=processed.original_to_processed.values(),
                            processing_steps=(
                                tuple(
                                    step
                                    for unit in adaptive.plan.units
                                    for step in unit.processing_steps
                                )
                                if should_ocr
                                else processed.processing_steps
                            ),
                        )
                    )
                if classification == "SCAN" and not page_table_ids:
                    inferred = infer_table_from_blocks(
                        displayed_blocks,
                        table_id=f"page-{page_number}-table-inferred-1",
                        page_number=page_number,
                        source_prefix=f"page:{page_number};ocr",
                    )
                    if inferred is not None:
                        table_task_succeeded = False
                        for (
                            table_processed,
                            table_page_bbox,
                            table_image_index,
                            table_image_id,
                        ) in table_region_candidates:
                            table_adaptive = recognize_preprocessed_image(
                                table_processed,
                                service=context.ocr_service,
                                source_id=context.source.source_id,
                                page_number=page_number,
                                request_prefix=(
                                    f"{context.source.source_id}:page-{page_number}:"
                                    f"image-{table_image_index}:table"
                                ),
                                languages=context.languages,
                                timeout_seconds=context.ocr_timeout_seconds,
                                region_kind="TABLE",
                                task_type="table_parsing",
                                detect_tables=True,
                            )
                            for table_index, table in enumerate(
                                table_adaptive.merged.tables, start=1
                            ):
                                table_id = (
                                    f"{table_image_id}-table-region-{table_index}"
                                )
                                response_hash = (
                                    table_adaptive.responses[0].raw_response_sha256
                                    if table_adaptive.responses
                                    else table_adaptive.attempted_units[0].sha256
                                )
                                parsed_tables.append(
                                    _parsed_ocr_table(
                                        table,
                                        table_id=table_id,
                                        page_number=page_number,
                                        image_index=table_image_index,
                                        image_id=table_image_id,
                                        image_width=table_processed.original_width,
                                        image_height=table_processed.original_height,
                                        page_bbox=table_page_bbox,
                                        response_hash=response_hash,
                                        recognition_mode="table_parsing",
                                    )
                                )
                                page_table_ids.append(table_id)
                                table_task_succeeded = True
                        if not table_task_succeeded:
                            parsed_tables.append(inferred)
                            page_table_ids.append(inferred.table_id)
                            issues.append(
                                ParseIssue(
                                    "PARSE.OCR_TABLE_FALLBACK",
                                    "表格专用任务未返回结构，已使用坐标聚类推测并限制置信度",
                                    IssueSeverity.WARNING,
                                    page_number=page_number,
                                    object_id=inferred.table_id,
                                )
                            )
                displayed_blocks.sort(
                    key=lambda block: (
                        block.bbox.y if block.bbox else float("inf"),
                        block.bbox.x if block.bbox else float("inf"),
                        block.block_id,
                    )
                )
                displayed_blocks = [
                    replace(block, reading_order=index)
                    for index, block in enumerate(displayed_blocks, start=1)
                ]
                if classification == "UNREADABLE":
                    issues.append(
                        ParseIssue(
                            "PARSE.NO_CONTENT",
                            "PDF页面没有足够原生文本或可识别扫描区域",
                            IssueSeverity.ERROR,
                            page_number=page_number,
                        )
                    )
                elif classification == "SCAN" and not displayed_blocks and not page_table_ids:
                    issues.append(
                        ParseIssue(
                            "PARSE.NO_CONTENT",
                            "扫描PDF页面未获得OCR内容",
                            IssueSeverity.ERROR,
                            page_number=page_number,
                        )
                    )
                pages.append(
                    ParsedPage(
                        page_number=page_number,
                        width=width,
                        height=height,
                        coordinate_space=CoordinateSpace.PDF_POINTS_TOP_LEFT,
                        classification=classification,
                        text_blocks=tuple(displayed_blocks),
                        table_ids=tuple(page_table_ids),
                        image_ids=tuple(page_image_ids),
                        native_character_count=native_characters,
                        ocr_character_count=page_ocr_characters,
                        image_coverage_ratio=image_coverage,
                        metadata={
                            "valid_character_ratio": valid_ratio,
                            "rotation": int(getattr(page, "rotation", 0) or 0),
                            "ocr_status": (
                                "PARTIAL"
                                if "PARTIAL" in page_ocr_statuses
                                or (
                                    "FAILED" in page_ocr_statuses
                                    and "COMPLETE" in page_ocr_statuses
                                )
                                else (
                                    page_ocr_statuses[0]
                                    if page_ocr_statuses
                                    else "NOT_REQUIRED"
                                )
                            ),
                        },
                    )
                )
        finally:
            plumber_document.close()
        if not pages:
            issues.append(ParseIssue("PARSE.NO_CONTENT", "PDF没有页面", IssueSeverity.ERROR))
        return ReaderOutput(
            ParsedDocument(
                document_id=context.source.source_id,
                source=context.source,
                media_type=context.media_type,
                document_kind="PDF",
                parser_id=self.reader_id,
                parser_version=self.parser_version,
                page_count=len(pages),
                pages=tuple(pages),
                tables=tuple(parsed_tables),
                images=tuple(images),
                metadata={
                    "ocr_calls": ocr_calls,
                    "page_classifications": [page.classification for page in pages],
                },
                issues=tuple(issues),
            ),
            tuple(resources),
        )

    def supports(self, path: Path) -> bool:
        return path.suffix.casefold() == ".pdf"

    def read(self, path: Path):
        return legacy_read(self, path, "application/pdf")


__all__ = ["PdfReader"]
