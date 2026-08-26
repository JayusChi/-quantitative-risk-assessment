from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from ..contracts import RawRow, RawTable
from .tabular import _source, _trim_rows

WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{WORD_NAMESPACE}}}"


def _text(element: ElementTree.Element) -> str:
    paragraphs: list[str] = []
    paragraph_nodes = [element] if element.tag == f"{W}p" else list(element.findall(f".//{W}p"))
    for paragraph in paragraph_nodes:
        value = "".join(node.text or "" for node in paragraph.findall(f".//{W}t"))
        if value.strip():
            paragraphs.append(value.strip())
    return "\n".join(paragraphs)


class DocxReader:
    """Extract native DOCX tables without interpreting their business meaning."""

    reader_id = "docx/ooxml-table-v1"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".docx"

    def read(self, path: Path) -> Sequence[RawTable]:
        source = _source(path, self.reader_id)
        try:
            with ZipFile(path) as package:
                document_xml = package.read("word/document.xml")
        except (BadZipFile, KeyError) as exc:
            raise ValueError(f"DOCX结构无效：{path.name}") from exc
        try:
            root = ElementTree.fromstring(document_xml)
        except ElementTree.ParseError as exc:
            raise ValueError(f"DOCX正文XML无效：{path.name}") from exc

        tables: list[RawTable] = []
        for table_index, table in enumerate(root.findall(f".//{W}tbl"), start=1):
            rows = _trim_rows(
                RawRow(
                    row_index,
                    tuple(_text(cell) for cell in row.findall(f"./{W}tc")),
                )
                for row_index, row in enumerate(table.findall(f"./{W}tr"), start=1)
            )
            tables.append(
                RawTable(
                    source,
                    f"Table {table_index}",
                    rows,
                    extraction_method="DOCX_NATIVE_TABLE",
                    confidence=0.85,
                    requires_review=True,
                )
            )
        if tables:
            return tuple(tables)

        paragraphs = [
            value
            for value in (_text(paragraph) for paragraph in root.findall(f".//{W}body/{W}p"))
            if value
        ]
        rows = tuple(RawRow(index, (value,)) for index, value in enumerate(paragraphs, start=1))
        return (
            RawTable(
                source,
                "Document text",
                rows,
                extraction_method="DOCX_FREE_TEXT",
                confidence=0.25,
                requires_review=True,
            ),
        )


__all__ = ["DocxReader"]
