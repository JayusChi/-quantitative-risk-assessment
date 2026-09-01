"""Media-type keyed document reader registry."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..contracts import SourceReference
from ..ocr.disabled import DisabledOcrProvider
from ..ocr.service import OcrService
from .contracts import ReaderOutput

MEDIA_TYPES_BY_SUFFIX = {
    ".csv": "text/csv",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


@dataclass(frozen=True)
class ParseContext:
    source: SourceReference
    media_type: str
    ocr_service: OcrService
    languages: tuple[str, ...] = ("zh-Hans", "en")
    ocr_timeout_seconds: float = 30.0
    low_confidence_threshold: float = 0.75
    cancel_check: Callable[[], None] | None = None
    page_progress: Callable[[int, int, str], None] | None = None
    selected_pages: frozenset[int] | None = None


class DocumentReader(Protocol):
    reader_id: str
    parser_version: str
    media_types: frozenset[str]

    def parse(self, path: Path, context: ParseContext) -> ReaderOutput: ...


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def standalone_source(path: Path, reader_id: str) -> SourceReference:
    checksum = file_sha256(path)
    return SourceReference(
        source_id=checksum,
        source_path=path.name,
        reader_id=reader_id,
        checksum_sha256=checksum,
    )


def default_context(path: Path, reader: DocumentReader, media_type: str) -> ParseContext:
    return ParseContext(
        source=standalone_source(path, reader.reader_id),
        media_type=media_type,
        ocr_service=OcrService(DisabledOcrProvider()),
    )


class ParsingRegistry:
    def __init__(self, readers: tuple[DocumentReader, ...] | None = None):
        if readers is None:
            from ..readers.csv_reader import CsvReader
            from ..readers.docx_reader import DocxReader
            from ..readers.excel_reader import XlsReader, XlsxReader
            from ..readers.image_reader import ImageReader
            from ..readers.pdf_reader import PdfReader

            readers = (
                CsvReader(),
                XlsReader(),
                XlsxReader(),
                DocxReader(),
                PdfReader(),
                ImageReader(),
            )
        self._readers = readers
        by_media: dict[str, DocumentReader] = {}
        for reader in readers:
            for media_type in reader.media_types:
                if media_type in by_media:
                    raise ValueError(f"媒体类型{media_type}存在多个主读取器")
                by_media[media_type] = reader
        self._by_media = by_media

    @property
    def supported_suffixes(self) -> frozenset[str]:
        return frozenset(MEDIA_TYPES_BY_SUFFIX)

    @property
    def supported_media_types(self) -> frozenset[str]:
        return frozenset(self._by_media)

    def media_type_for(self, path: Path, detected_media_type: str | None = None) -> str:
        if detected_media_type:
            if detected_media_type not in self._by_media:
                raise ValueError(f"不支持的检测媒体类型：{detected_media_type}")
            return detected_media_type
        media_type = MEDIA_TYPES_BY_SUFFIX.get(path.suffix.casefold())
        if media_type is None:
            raise ValueError(f"不支持的源文件格式：{path.suffix or path.name}")
        return media_type

    def reader_for(self, media_type: str) -> DocumentReader:
        reader = self._by_media.get(media_type)
        if reader is None:
            raise ValueError(f"没有注册媒体类型{media_type}的主读取器")
        return reader


__all__ = [
    "DocumentReader",
    "MEDIA_TYPES_BY_SUFFIX",
    "ParseContext",
    "ParsingRegistry",
    "default_context",
    "file_sha256",
    "standalone_source",
]
