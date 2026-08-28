"""Compatibility imports for the pre-stage-three reader module path."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..parsing.registry import MEDIA_TYPES_BY_SUFFIX, ParsingRegistry
from .csv_reader import CsvReader
from .excel_reader import XlsReader, XlsxReader

SUPPORTED_SUFFIXES = frozenset(MEDIA_TYPES_BY_SUFFIX)


class ReaderRegistry:
    """Legacy RawTable facade backed by the single media-type parsing registry."""

    def __init__(self) -> None:
        self._registry = ParsingRegistry()

    @property
    def supported_suffixes(self) -> frozenset[str]:
        return self._registry.supported_suffixes

    def reader_for(self, path: Path, detected_media_type: str | None = None) -> Any:
        media_type = self._registry.media_type_for(path, detected_media_type)
        return self._registry.reader_for(media_type)

    def read(self, path: Path, detected_media_type: str | None = None):
        return self.reader_for(path, detected_media_type).read(path)


def json_safe_cell(value: Any) -> Any:
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value


__all__ = [
    "CsvReader",
    "ReaderRegistry",
    "SUPPORTED_SUFFIXES",
    "XlsReader",
    "XlsxReader",
    "json_safe_cell",
]
