"""Provider-neutral OCR request and response contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..parsing.contracts import BoundingBox


@dataclass(frozen=True)
class OcrTextBlock:
    text: str
    bbox: BoundingBox
    confidence: float
    language: str | None = None
    block_type: str = "PARAGRAPH"


@dataclass(frozen=True)
class OcrCell:
    row_index: int
    column_index: int
    text: str
    bbox: BoundingBox | None = None
    confidence: float | None = None
    row_span: int = 1
    column_span: int = 1


@dataclass(frozen=True)
class OcrTable:
    cells: tuple[OcrCell, ...]
    row_count: int
    column_count: int
    bbox: BoundingBox | None = None
    confidence: float = 0.8


@dataclass(frozen=True)
class OcrRequest:
    image_bytes: bytes
    width: int
    height: int
    languages: tuple[str, ...]
    detect_tables: bool
    request_id: str
    timeout_seconds: float


@dataclass(frozen=True)
class OcrResponse:
    provider_id: str
    model_version: str
    text_blocks: tuple[OcrTextBlock, ...] = field(default_factory=tuple)
    tables: tuple[OcrTable, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    raw_response_sha256: str = ""
    provider_request_id: str | None = None


class OcrProviderError(RuntimeError):
    code = "PARSE.OCR_PROVIDER_ERROR"
    retryable = False


class OcrProviderNotConfigured(OcrProviderError):
    code = "PARSE.OCR_PROVIDER_NOT_CONFIGURED"


class OcrTimeout(OcrProviderError):
    code = "PARSE.OCR_TIMEOUT"
    retryable = True


class OcrConnectionFailed(OcrProviderError):
    code = "PARSE.OCR_PROVIDER_ERROR"
    retryable = True


class OcrRateLimited(OcrProviderError):
    code = "PARSE.OCR_RATE_LIMITED"
    retryable = True


class OcrAuthenticationFailed(OcrProviderError):
    code = "PARSE.OCR_AUTHENTICATION_FAILED"


class OcrUnreadable(OcrProviderError):
    code = "PARSE.OCR_UNREADABLE"


class OcrContractInvalid(OcrProviderError):
    code = "PARSE.OCR_OUTPUT_INVALID"


@runtime_checkable
class OcrProvider(Protocol):
    provider_id: str
    model_version: str

    def recognize(self, request: OcrRequest) -> OcrResponse: ...


__all__ = [
    "OcrAuthenticationFailed",
    "OcrCell",
    "OcrConnectionFailed",
    "OcrContractInvalid",
    "OcrProvider",
    "OcrProviderError",
    "OcrProviderNotConfigured",
    "OcrRateLimited",
    "OcrRequest",
    "OcrResponse",
    "OcrTable",
    "OcrTextBlock",
    "OcrTimeout",
    "OcrUnreadable",
]
