"""Temporary ParsedDocument to RawTable bridge retained through stage four."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..contracts import RawRow, RawTable
from .contracts import ParsedDocument
from .registry import DocumentReader, default_context


def _matrix(table: Any) -> list[list[Any]]:
    rows: list[list[Any]] = [
        [None for _ in range(table.column_count)] for _ in range(table.row_count)
    ]
    for cell in table.cells:
        value = (
            cell.cached_value
            if cell.formula_text and cell.cached_value is not None
            else cell.parsed_value
            if cell.parsed_value is not None
            else cell.raw_value
        )
        for row_index in range(
            cell.row_index, min(table.row_count, cell.row_index + cell.row_span)
        ):
            for column_index in range(
                cell.column_index,
                min(table.column_count, cell.column_index + cell.column_span),
            ):
                rows[row_index][column_index] = value
    return rows


def _trim_legacy_rows(
    rows: list[list[Any]], row_numbers: list[int] | None = None
) -> tuple[RawRow, ...]:
    result: list[RawRow] = []
    for fallback_row_number, row in enumerate(rows, start=1):
        cells = list(row)
        while cells and cells[-1] in (None, ""):
            cells.pop()
        if cells:
            row_number = (
                row_numbers[fallback_row_number - 1]
                if row_numbers and fallback_row_number <= len(row_numbers)
                else fallback_row_number
            )
            result.append(RawRow(row_number, tuple(cells)))
    return tuple(result)


def document_to_raw_tables(document: ParsedDocument) -> tuple[RawTable, ...]:
    tables: list[RawTable] = []
    for index, table in enumerate(document.tables, start=1):
        physical_ranges = table.metadata.get("record_physical_line_ranges")
        row_numbers = (
            [int(item[0]) for item in physical_ranges]
            if isinstance(physical_ranges, list)
            else None
        )
        rows = _trim_legacy_rows(_matrix(table), row_numbers)
        if not rows:
            continue
        name = str(
            table.metadata.get("legacy_name")
            or table.sheet_name
            or f"Page {table.page_number} Table {index}"
        )
        deterministic = table.extraction_method in {"CSV_NATIVE", "XLS_NATIVE", "XLSX_NATIVE"}
        merged_expansions = [
            {
                "row_index": row_index,
                "column_index": column_index,
                "master_address": cell.address,
            }
            for cell in table.cells
            if cell.row_span > 1 or cell.column_span > 1
            for row_index in range(cell.row_index, cell.row_index + cell.row_span)
            for column_index in range(cell.column_index, cell.column_index + cell.column_span)
            if (row_index, column_index) != (cell.row_index, cell.column_index)
        ]
        tables.append(
            RawTable(
                document.source,
                name,
                rows,
                extraction_method=(
                    "STRUCTURED_TABLE" if deterministic else table.extraction_method
                ),
                confidence=table.confidence,
                requires_review=not deterministic or table.confidence < 1.0,
                metadata={"merged_expansions": merged_expansions},
            )
        )
    if tables:
        return tuple(tables)
    if document.text_blocks:
        rows = tuple(
            RawRow(index, (block.text,))
            for index, block in enumerate(document.text_blocks, start=1)
            if block.text
        )
        if rows:
            return (
                RawTable(
                    document.source,
                    "Document text",
                    rows,
                    extraction_method="DOCUMENT_TEXT_BLOCKS",
                    confidence=min(
                        (
                            block.confidence
                            for block in document.text_blocks
                            if block.confidence is not None
                        ),
                        default=0.4,
                    ),
                    requires_review=True,
                ),
            )
    for page in document.pages:
        rows = tuple(
            RawRow(index, (block.text,))
            for index, block in enumerate(page.text_blocks, start=1)
            if block.text
        )
        if rows:
            tables.append(
                RawTable(
                    document.source,
                    str(page.metadata.get("legacy_name") or f"Page {page.page_number} text"),
                    rows,
                    extraction_method="DOCUMENT_TEXT_BLOCKS",
                    confidence=min(
                        (
                            block.confidence
                            for block in page.text_blocks
                            if block.confidence is not None
                        ),
                        default=0.4,
                    ),
                    requires_review=True,
                )
            )
    return tuple(tables)


def legacy_read(reader: DocumentReader, path: Path, media_type: str) -> Sequence[RawTable]:
    context = default_context(path, reader, media_type)
    output = reader.parse(path, context)
    return document_to_raw_tables(output.document.finalized())


__all__ = ["document_to_raw_tables", "legacy_read"]
