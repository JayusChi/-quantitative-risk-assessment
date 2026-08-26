from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..contracts import RawRow, RawTable
from .tabular import _source, _trim_rows


def _clean_cell(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    return value


class PdfReader:
    """Auxiliary PDF table/text extraction; every emitted row requires review."""

    reader_id = "pdf/auxiliary-table-v1"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".pdf"

    def _with_pdfplumber(self, path: Path) -> tuple[RawTable, ...]:
        try:
            import pdfplumber
        except ImportError:
            return ()
        source = _source(path, "pdf/pdfplumber-table-v1")
        result: list[RawTable] = []
        with pdfplumber.open(path) as document:
            for page_index, page in enumerate(document.pages, start=1):
                for table_index, table in enumerate(page.extract_tables(), start=1):
                    rows = _trim_rows(
                        RawRow(index, tuple(_clean_cell(cell) for cell in row))
                        for index, row in enumerate(table, start=1)
                    )
                    if rows:
                        result.append(
                            RawTable(
                                source,
                                f"Page {page_index} Table {table_index}",
                                rows,
                                extraction_method="PDF_EXTRACTED_TABLE",
                                confidence=0.7,
                                requires_review=True,
                            )
                        )
        return tuple(result)

    def _with_pypdf(self, path: Path) -> tuple[RawTable, ...]:
        try:
            from pypdf import PdfReader as PyPdfReader
        except ImportError as exc:
            raise RuntimeError("读取PDF需要安装pdfplumber>=0.11或pypdf>=5") from exc
        source = _source(path, "pdf/pypdf-text-v1")
        document = PyPdfReader(str(path))
        result: list[RawTable] = []
        for page_index, page in enumerate(document.pages, start=1):
            text = page.extract_text() or ""
            rows: list[RawRow] = []
            for line_number, line in enumerate(text.splitlines(), start=1):
                cells = tuple(
                    item.strip() for item in re.split(r"\t+|\s{2,}", line.strip()) if item.strip()
                )
                if cells:
                    rows.append(RawRow(line_number, cells))
            result.append(
                RawTable(
                    source,
                    f"Page {page_index} text",
                    _trim_rows(rows),
                    extraction_method=("PDF_TEXT_HEURISTIC" if rows else "PDF_OCR_REQUIRED"),
                    confidence=0.4 if rows else 0.0,
                    requires_review=True,
                )
            )
        return tuple(result)

    def read(self, path: Path) -> Sequence[RawTable]:
        extracted = self._with_pdfplumber(path)
        return extracted or self._with_pypdf(path)


__all__ = ["PdfReader"]
