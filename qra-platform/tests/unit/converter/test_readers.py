from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from qra_converter.readers import CsvReader, DocxReader, XlsxReader


class TabularReaderTests(unittest.TestCase):
    def test_csv_reader_preserves_source_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "data.csv"
            path.write_text("编号,值\nA,0\n", encoding="utf-8")
            table = CsvReader().read(path)[0]
        self.assertEqual(table.rows[1].row_number, 2)
        self.assertEqual(table.rows[1].cells, ("A", "0"))
        self.assertEqual(len(table.source.checksum_sha256), 64)

    def test_csv_reader_preserves_quoted_newlines_and_physical_row_number(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "data.csv"
            path.write_text('编号,说明\nA,"第一行\n第二行"\nB,完成\n', encoding="utf-8")
            table = CsvReader().read(path)[0]
        self.assertEqual(table.rows[1].cells[0], "A")
        self.assertEqual(table.rows[1].cells[1].splitlines(), ["第一行", "第二行"])
        self.assertEqual(table.rows[2].row_number, 4)

    def test_xlsx_reader_preserves_sheet_and_typed_values(self) -> None:
        try:
            from openpyxl import Workbook
        except ImportError:
            self.skipTest("openpyxl is not installed")
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "data.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "管段台账"
            sheet.append(["编号", "长度"])
            sheet.append(["S1", 1.5])
            workbook.save(path)
            workbook.close()
            table = XlsxReader().read(path)[0]
        self.assertEqual(table.sheet_name, "管段台账")
        self.assertEqual(table.rows[1].cells, ("S1", 1.5))

    def test_docx_reader_extracts_native_table_as_review_required(self) -> None:
        document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:tbl>
    <w:tr><w:tc><w:p><w:r><w:t>管段编号</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>长度</w:t></w:r></w:p></w:tc></w:tr>
    <w:tr><w:tc><w:p><w:r><w:t>S1</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>1.5</w:t></w:r></w:p></w:tc></w:tr>
  </w:tbl></w:body>
</w:document>"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "管段台账.docx"
            with ZipFile(path, "w") as package:
                package.writestr("word/document.xml", document_xml)
            table = DocxReader().read(path)[0]
        self.assertEqual(table.sheet_name, "Table 1")
        self.assertEqual(table.rows[1].cells, ("S1", "1.5"))
        self.assertTrue(table.requires_review)
        self.assertLess(table.confidence, 1.0)


if __name__ == "__main__":
    unittest.main()
