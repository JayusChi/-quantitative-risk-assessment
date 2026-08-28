"""Source-faithful CSV parser with physical-line evidence."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from ..contracts import IssueSeverity
from ..parsing.compatibility import legacy_read
from ..parsing.contracts import (
    BoundingBox,
    CoordinateSpace,
    ParsedCell,
    ParsedDocument,
    ParsedPage,
    ParsedTable,
    ParseIssue,
    ReaderOutput,
    source_fragment_sha256,
)
from ..parsing.registry import ParseContext


class CsvReader:
    reader_id = "csv/stdlib"
    parser_version = "2.0.0"
    media_types = frozenset({"text/csv"})

    @staticmethod
    def _decode(path: Path) -> tuple[str, str]:
        data = path.read_bytes()
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                return data.decode(encoding), encoding
            except UnicodeDecodeError:
                continue
        raise ValueError(f"CSV编码无法识别（仅支持UTF-8-SIG/UTF-8/GB18030）：{path.name}")

    def parse(self, path: Path, context: ParseContext) -> ReaderOutput:
        if context.page_progress:
            context.page_progress(1, 1, "正在解析CSV记录")
        text, encoding = self._decode(path)
        issues: list[ParseIssue] = []
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
            dialect_source = "SNIFFER"
        except csv.Error:
            dialect = csv.excel
            dialect_source = "FALLBACK_COMMA"
            issues.append(
                ParseIssue(
                    "PARSE.CSV_DIALECT_FALLBACK",
                    "无法可靠确定CSV方言，已回退为逗号分隔和标准引号规则",
                    IssueSeverity.WARNING,
                    page_number=1,
                )
            )
        reader = csv.reader(io.StringIO(text, newline=""), dialect)
        records: list[tuple[int, int, tuple[str, ...]]] = []
        previous_end = 0
        try:
            for row in reader:
                records.append((previous_end + 1, reader.line_num, tuple(row)))
                previous_end = reader.line_num
        except csv.Error as exc:
            raise ValueError(f"CSV结构无效：{exc}") from exc
        row_count = len(records)
        column_count = max((len(row) for _, _, row in records), default=0)
        cells: list[ParsedCell] = []
        for row_index, (start_line, end_line, row) in enumerate(records):
            for column_index, value in enumerate(row):
                location = (
                    f"record:{row_index + 1};physical-lines:{start_line}-{end_line};"
                    f"column:{column_index + 1}"
                )
                cells.append(
                    ParsedCell(
                        row_index=row_index,
                        column_index=column_index,
                        address=f"R{row_index + 1}C{column_index + 1}",
                        raw_value=value,
                        display_text=value,
                        value_type="EMPTY_STRING" if value == "" else "STRING",
                        source_location=location,
                        extraction_method="CSV_NATIVE",
                        confidence=1.0,
                        bbox=BoundingBox(column_index, row_index, 1, 1),
                        coordinate_space=CoordinateSpace.WORKSHEET_GRID,
                        source_fragment_sha256=source_fragment_sha256(
                            [start_line, end_line, column_index, value]
                        ),
                    )
                )
        if not cells:
            issues.append(
                ParseIssue(
                    "PARSE.NO_CONTENT",
                    "CSV没有可解析记录",
                    IssueSeverity.ERROR,
                    page_number=1,
                )
            )
        table = ParsedTable(
            table_id="table-1",
            row_count=row_count,
            column_count=column_count,
            cells=tuple(cells),
            extraction_method="CSV_NATIVE",
            confidence=1.0,
            sheet_name=path.stem,
            coordinate_space=CoordinateSpace.WORKSHEET_GRID,
            metadata={
                "legacy_name": path.stem,
                "encoding": encoding,
                "delimiter": dialect.delimiter,
                "quotechar": dialect.quotechar,
                "dialect_source": dialect_source,
                "record_physical_line_ranges": [[start, end] for start, end, _ in records],
            },
        )
        newline_type = (
            "CRLF" if "\r\n" in text else "CR" if "\r" in text else "LF" if "\n" in text else "NONE"
        )
        page = ParsedPage(
            page_number=1,
            width=float(column_count),
            height=float(row_count),
            coordinate_space=CoordinateSpace.WORKSHEET_GRID,
            classification="STRUCTURED",
            table_ids=(table.table_id,),
            metadata={"sheet_name": path.stem, "newline_type": newline_type},
        )
        document = ParsedDocument(
            document_id=context.source.source_id,
            source=context.source,
            media_type=context.media_type,
            document_kind="CSV",
            parser_id=self.reader_id,
            parser_version=self.parser_version,
            page_count=1,
            pages=(page,),
            tables=(table,),
            metadata={
                "encoding": encoding,
                "delimiter": dialect.delimiter,
                "newline_type": newline_type,
            },
            issues=tuple(issues),
        )
        return ReaderOutput(document)

    def supports(self, path: Path) -> bool:
        return path.suffix.casefold() == ".csv"

    def read(self, path: Path):
        return legacy_read(self, path, "text/csv")


__all__ = ["CsvReader"]
