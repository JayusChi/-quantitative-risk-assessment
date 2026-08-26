"""Source-faithful structured and auxiliary document readers."""

from .documents import DocxReader
from .pdf import PdfReader
from .tabular import CsvReader, ReaderRegistry, XlsReader, XlsxReader

__all__ = [
    "CsvReader",
    "DocxReader",
    "PdfReader",
    "ReaderRegistry",
    "XlsReader",
    "XlsxReader",
]
