"""Conservative conversion of provider table-task text into cell contracts."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from ..parsing.contracts import BoundingBox
from .ports import OcrCell, OcrTable


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[tuple[str, int, int]]]] = []
        self._table: list[list[tuple[str, int, int]]] | None = None
        self._row: list[tuple[str, int, int]] | None = None
        self._cell_parts: list[str] | None = None
        self._row_span = 1
        self._column_span = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.casefold()
        if name == "table":
            self._table = []
        elif name == "tr" and self._table is not None:
            self._row = []
        elif name in {"td", "th"} and self._row is not None:
            values = {key.casefold(): value for key, value in attrs}
            self._row_span = _positive_span(values.get("rowspan"))
            self._column_span = _positive_span(values.get("colspan"))
            self._cell_parts = []
        elif name == "br" and self._cell_parts is not None:
            self._cell_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name in {"td", "th"} and self._row is not None and self._cell_parts is not None:
            text = " ".join("".join(self._cell_parts).split())
            self._row.append((text, self._row_span, self._column_span))
            self._cell_parts = None
        elif name == "tr" and self._table is not None and self._row is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif name == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


def _positive_span(value: str | None) -> int:
    try:
        return max(1, min(100, int(value or 1)))
    except ValueError:
        return 1


def _markdown_tables(text: str) -> list[list[list[tuple[str, int, int]]]]:
    tables: list[list[list[tuple[str, int, int]]]] = []
    current: list[list[str]] = []

    def flush() -> None:
        nonlocal current
        if len(current) >= 2:
            separator_indices = [
                index
                for index, row in enumerate(current)
                if row and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in row)
            ]
            rows = [row for index, row in enumerate(current) if index not in separator_indices]
            if rows and (separator_indices or len(rows) >= 2):
                tables.append(
                    [[(" ".join(cell.split()), 1, 1) for cell in row] for row in rows]
                )
        current = []

    for line in text.replace("```html", "").replace("```", "").splitlines():
        stripped = line.strip()
        if "|" not in stripped:
            flush()
            continue
        cells = stripped.strip("|").split("|")
        if len(cells) < 2:
            flush()
            continue
        current.append(cells)
    flush()
    return tables


def _to_table(
    rows: list[list[tuple[str, int, int]]], *, width: int, height: int
) -> OcrTable | None:
    occupied: set[tuple[int, int]] = set()
    placed: list[tuple[int, int, str, int, int]] = []
    column_count = 0
    for row_index, row in enumerate(rows):
        column_index = 0
        for text, row_span, column_span in row:
            while (row_index, column_index) in occupied:
                column_index += 1
            placed.append((row_index, column_index, text, row_span, column_span))
            for extra_row in range(row_span):
                for extra_column in range(column_span):
                    occupied.add((row_index + extra_row, column_index + extra_column))
            column_count = max(column_count, column_index + column_span)
            column_index += column_span
    row_count = max((row + row_span for row, _, _, row_span, _ in placed), default=0)
    if row_count < 1 or column_count < 1:
        return None
    cell_width = float(width) / column_count
    cell_height = float(height) / row_count
    cells = tuple(
        OcrCell(
            row_index=row_index,
            column_index=column_index,
            text=text,
            bbox=BoundingBox(
                column_index * cell_width,
                row_index * cell_height,
                column_span * cell_width,
                row_span * cell_height,
            ),
            confidence=0.7,
            row_span=row_span,
            column_span=column_span,
        )
        for row_index, column_index, text, row_span, column_span in placed
    )
    return OcrTable(
        cells=cells,
        row_count=row_count,
        column_count=column_count,
        bbox=BoundingBox(0, 0, float(width), float(height)),
        confidence=0.7,
    )


def tables_from_provider_text(text: str, *, width: int, height: int) -> tuple[OcrTable, ...]:
    parser = _TableParser()
    parser.feed(text)
    raw_tables = parser.tables or _markdown_tables(text)
    return tuple(
        table
        for rows in raw_tables
        if (table := _to_table(rows, width=width, height=height)) is not None
    )


__all__ = ["tables_from_provider_text"]
