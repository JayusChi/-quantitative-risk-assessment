"""Deterministic parsing quality summaries and evidence validation."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from typing import Any

from ..contracts import IssueSeverity
from .contracts import ParsedDocument, ParseIssue
from .geometry import validate_bbox

LOW_CONFIDENCE_THRESHOLD = 0.75


def validate_locations(document: ParsedDocument) -> ParsedDocument:
    issues = list(document.issues)
    page_sizes = {page.page_number: (page.width, page.height) for page in document.pages}
    for block in document.text_blocks:
        if not validate_bbox(block.bbox, width=block.page_width, height=block.page_height):
            issues.append(
                ParseIssue(
                    "PARSE.LOCATION_INVALID",
                    f"文本块{block.block_id}坐标超出结构对象边界",
                    IssueSeverity.ERROR,
                    object_id=block.block_id,
                )
            )
    for page in document.pages:
        for block in page.text_blocks:
            if not validate_bbox(block.bbox, width=page.width, height=page.height):
                issues.append(
                    ParseIssue(
                        "PARSE.LOCATION_INVALID",
                        f"文本块{block.block_id}坐标超出页面边界",
                        IssueSeverity.ERROR,
                        page_number=page.page_number,
                        object_id=block.block_id,
                    )
                )
    for table in document.tables:
        width, height = page_sizes.get(table.page_number, (None, None))
        if not validate_bbox(table.bbox, width=width, height=height):
            issues.append(
                ParseIssue(
                    "PARSE.LOCATION_INVALID",
                    f"表格{table.table_id}坐标超出页面边界",
                    IssueSeverity.ERROR,
                    page_number=table.page_number,
                    object_id=table.table_id,
                )
            )
        for cell in table.cells:
            if not validate_bbox(cell.bbox, width=width, height=height):
                issues.append(
                    ParseIssue(
                        "PARSE.LOCATION_INVALID",
                        f"单元格{table.table_id}/{cell.address}坐标超出页面边界",
                        IssueSeverity.ERROR,
                        page_number=table.page_number,
                        object_id=f"{table.table_id}/{cell.address}",
                    )
                )
        if table.confidence < LOW_CONFIDENCE_THRESHOLD:
            issues.append(
                ParseIssue(
                    "PARSE.TABLE_STRUCTURE_UNCERTAIN",
                    f"表格{table.table_id}结构置信度{table.confidence:.3f}低于阈值",
                    IssueSeverity.WARNING,
                    page_number=table.page_number,
                    object_id=table.table_id,
                )
            )
        last_row = [cell for cell in table.cells if cell.row_index == table.row_count - 1]
        last_row_text = [
            cell.display_text.strip() for cell in last_row if cell.display_text.strip()
        ]
        if (
            table.row_count > 1
            and len(last_row_text) == 1
            and last_row_text[0].casefold().startswith(("注", "note", "*"))
        ):
            issues.append(
                ParseIssue(
                    "PARSE.TABLE_FOOTNOTE_AMBIGUOUS",
                    f"表格{table.table_id}末行疑似脚注，解析层未将其作为业务记录合并",
                    IssueSeverity.WARNING,
                    page_number=table.page_number,
                    object_id=table.table_id,
                )
            )
    for image in document.images:
        width, height = page_sizes.get(image.page_number, (None, None))
        if not validate_bbox(image.bbox, width=width, height=height):
            issues.append(
                ParseIssue(
                    "PARSE.LOCATION_INVALID",
                    f"图像区域{image.image_id}坐标超出页面边界",
                    IssueSeverity.ERROR,
                    page_number=image.page_number,
                    object_id=image.image_id,
                )
            )
    return replace(document, issues=tuple(issues))


def link_table_continuations(document: ParsedDocument) -> ParsedDocument:
    """Add conservative cross-page candidates without merging records or headers."""

    def header(table: Any) -> tuple[str, ...]:
        return tuple(
            cell.display_text.strip()
            for cell in sorted(
                (cell for cell in table.cells if cell.row_index == 0),
                key=lambda cell: cell.column_index,
            )
        )

    updated = list(document.tables)
    for index, table in enumerate(updated):
        if table.page_number is None or table.continuation_of:
            continue
        candidates = [
            previous
            for previous in updated[:index]
            if previous.page_number == table.page_number - 1
            and previous.column_count == table.column_count
            and previous.column_count > 0
        ]
        current_header = header(table)
        previous = next(
            (
                item
                for item in reversed(candidates)
                if current_header and current_header == header(item)
            ),
            None,
        )
        if previous is not None:
            updated[index] = replace(
                table,
                continuation_of=previous.table_id,
                continuation_confidence=0.9,
                rule_version="cross-page-header-v1",
            )
    return replace(document, tables=tuple(updated))


def build_quality_report(document: ParsedDocument, processing_ms: int) -> dict[str, Any]:
    confidences: list[float] = []
    low_confidence = 0
    native_characters = 0
    ocr_characters = 0
    empty_pages = 0
    page_rows: list[dict[str, Any]] = []
    all_blocks = [
        *document.text_blocks,
        *(block for page in document.pages for block in page.text_blocks),
    ]
    reliable_block_ids = [
        block.block_id
        for block in all_blocks
        if block.confidence is not None and block.confidence >= LOW_CONFIDENCE_THRESHOLD
    ]
    low_confidence_block_ids = [
        block.block_id
        for block in all_blocks
        if block.confidence is None or block.confidence < LOW_CONFIDENCE_THRESHOLD
    ]
    document_confidences = [
        float(block.confidence) for block in document.text_blocks if block.confidence is not None
    ]
    confidences.extend(document_confidences)
    low_confidence += sum(value < LOW_CONFIDENCE_THRESHOLD for value in document_confidences)
    for block in document.text_blocks:
        if block.extraction_method.startswith("OCR"):
            ocr_characters += len(block.text)
        else:
            native_characters += len(block.text)
    for page in document.pages:
        page_confidences = [
            float(block.confidence) for block in page.text_blocks if block.confidence is not None
        ]
        confidences.extend(page_confidences)
        low = sum(value < LOW_CONFIDENCE_THRESHOLD for value in page_confidences)
        low_confidence += low
        native_characters += page.native_character_count
        ocr_characters += page.ocr_character_count
        if not page.text_blocks and not page.table_ids:
            empty_pages += 1
        page_rows.append(
            {
                "page_number": page.page_number,
                "classification": page.classification,
                "native_character_count": page.native_character_count,
                "ocr_character_count": page.ocr_character_count,
                "table_count": len(page.table_ids),
                "image_count": len(page.image_ids),
                "average_confidence": (
                    sum(page_confidences) / len(page_confidences) if page_confidences else None
                ),
                "minimum_confidence": min(page_confidences) if page_confidences else None,
                "low_confidence_block_count": low,
            }
        )
    issue_counts = Counter(issue.code for issue in document.issues)
    return {
        "contract_version": document.contract_version,
        "document_id": document.document_id,
        "parse_sha256": document.parse_sha256,
        "source_sha256": document.source.checksum_sha256,
        "summary": {
            "native_character_count": native_characters,
            "ocr_character_count": ocr_characters,
            "average_confidence": (sum(confidences) / len(confidences) if confidences else None),
            "minimum_confidence": min(confidences) if confidences else None,
            "table_count": len(document.tables),
            "image_count": len(document.images),
            "low_confidence_block_count": low_confidence,
            "reliable_text_block_count": len(reliable_block_ids),
            "empty_page_count": empty_pages,
            "processing_ms": max(0, int(processing_ms)),
            "issue_counts": dict(sorted(issue_counts.items())),
        },
        "pages": page_rows,
        "reliable_text_block_ids": reliable_block_ids,
        "low_confidence_text_block_ids": low_confidence_block_ids,
    }


__all__ = [
    "LOW_CONFIDENCE_THRESHOLD",
    "build_quality_report",
    "link_table_continuations",
    "validate_locations",
]
