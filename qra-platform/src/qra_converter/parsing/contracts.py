"""Immutable, source-faithful contracts for stage-three document parsing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from enum import Enum
from typing import Any

from ..contracts import IssueSeverity, SourceReference

PARSING_CONTRACT_VERSION = "qra.parsed-document/1.0.0"


class CoordinateSpace(str, Enum):
    PDF_POINTS_TOP_LEFT = "PDF_POINTS_TOP_LEFT"
    IMAGE_PIXELS_TOP_LEFT = "IMAGE_PIXELS_TOP_LEFT"
    WORKSHEET_GRID = "WORKSHEET_GRID"
    DOCX_STRUCTURE = "DOCX_STRUCTURE"


@dataclass(frozen=True)
class BoundingBox:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


@dataclass(frozen=True)
class ParseIssue:
    code: str
    message: str
    severity: IssueSeverity = IssueSeverity.WARNING
    page_number: int | None = None
    object_id: str | None = None
    location: str | None = None
    retryable: bool = False


@dataclass(frozen=True)
class ExtractionCandidate:
    method: str
    text: str
    confidence: float | None = None
    bbox: BoundingBox | None = None
    candidate_id: str | None = None
    source_fragment_sha256: str | None = None


@dataclass(frozen=True)
class TextBlock:
    block_id: str
    text: str
    normalized_text: str | None
    reading_order: int
    block_type: str
    extraction_method: str
    source_fragment_sha256: str
    page_number: int | None = None
    structure_location: str | None = None
    bbox: BoundingBox | None = None
    coordinate_space: CoordinateSpace | None = None
    page_width: float | None = None
    page_height: float | None = None
    confidence: float | None = None
    language: str | None = None
    style_hint: dict[str, Any] = field(default_factory=dict)
    extraction_candidates: tuple[ExtractionCandidate, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ParsedCell:
    row_index: int
    column_index: int
    address: str
    raw_value: Any
    display_text: str
    value_type: str
    source_location: str
    extraction_method: str
    confidence: float | None = None
    row_span: int = 1
    column_span: int = 1
    formula_text: str | None = None
    cached_value: Any = None
    parsed_value: Any = None
    number_format: str | None = None
    bbox: BoundingBox | None = None
    coordinate_space: CoordinateSpace | None = None
    is_merged_expansion: bool = False
    source_fragment_sha256: str | None = None


@dataclass(frozen=True)
class ParsedTable:
    table_id: str
    row_count: int
    column_count: int
    cells: tuple[ParsedCell, ...]
    extraction_method: str
    confidence: float
    page_number: int | None = None
    sheet_name: str | None = None
    title_candidate: str | None = None
    bbox: BoundingBox | None = None
    coordinate_space: CoordinateSpace | None = None
    continuation_of: str | None = None
    continuation_confidence: float | None = None
    rule_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ImageRegion:
    image_id: str
    source_part: str
    pixel_width: int
    pixel_height: int
    content_sha256: str
    page_number: int | None = None
    bbox: BoundingBox | None = None
    coordinate_space: CoordinateSpace | None = None
    preview_ref: str | None = None
    caption_candidate: str | None = None
    ocr_block_ids: tuple[str, ...] = field(default_factory=tuple)
    original_to_processed: tuple[float, float, float, float, float, float] | None = None
    processing_steps: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ParsedPage:
    page_number: int
    width: float | None
    height: float | None
    coordinate_space: CoordinateSpace
    classification: str
    text_blocks: tuple[TextBlock, ...] = field(default_factory=tuple)
    table_ids: tuple[str, ...] = field(default_factory=tuple)
    image_ids: tuple[str, ...] = field(default_factory=tuple)
    native_character_count: int = 0
    ocr_character_count: int = 0
    image_coverage_ratio: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedDocument:
    document_id: str
    source: SourceReference
    media_type: str
    document_kind: str
    parser_id: str
    parser_version: str
    page_count: int
    pages: tuple[ParsedPage, ...] = field(default_factory=tuple)
    text_blocks: tuple[TextBlock, ...] = field(default_factory=tuple)
    tables: tuple[ParsedTable, ...] = field(default_factory=tuple)
    images: tuple[ImageRegion, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)
    issues: tuple[ParseIssue, ...] = field(default_factory=tuple)
    parse_sha256: str = ""
    contract_version: str = PARSING_CONTRACT_VERSION

    @property
    def has_errors(self) -> bool:
        return any(issue.severity is IssueSeverity.ERROR for issue in self.issues)

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = _json_safe(asdict(self))
        if not include_hash:
            value.pop("parse_sha256", None)
        return value

    def finalized(self) -> ParsedDocument:
        payload = canonical_json(self.to_dict(include_hash=False)).encode("utf-8")
        return replace(self, parse_sha256=hashlib.sha256(payload).hexdigest())


@dataclass(frozen=True)
class PreviewResource:
    path: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class ReaderOutput:
    document: ParsedDocument
    resources: tuple[PreviewResource, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ParseExecution:
    document: ParsedDocument
    quality_report: dict[str, Any]
    preview_manifest: dict[str, Any]
    artifact_dir: str | None
    cache_hit: bool
    succeeded: bool


def source_fragment_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(_json_safe(value)).encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        raise ValueError("解析合同不允许非有限数值")
    return value


def parsed_document_from_dict(value: dict[str, Any]) -> ParsedDocument:
    """Rehydrate a canonical artifact without weakening typed evidence fields."""

    def bbox(raw: dict[str, Any] | None) -> BoundingBox | None:
        return BoundingBox(**raw) if raw else None

    def candidate(raw: dict[str, Any]) -> ExtractionCandidate:
        return ExtractionCandidate(
            method=str(raw["method"]),
            text=str(raw["text"]),
            confidence=raw.get("confidence"),
            bbox=bbox(raw.get("bbox")),
            candidate_id=raw.get("candidate_id"),
            source_fragment_sha256=raw.get("source_fragment_sha256"),
        )

    def block(raw: dict[str, Any]) -> TextBlock:
        return TextBlock(
            block_id=str(raw["block_id"]),
            text=str(raw["text"]),
            normalized_text=raw.get("normalized_text"),
            reading_order=int(raw["reading_order"]),
            block_type=str(raw["block_type"]),
            extraction_method=str(raw["extraction_method"]),
            source_fragment_sha256=str(raw["source_fragment_sha256"]),
            page_number=raw.get("page_number"),
            structure_location=raw.get("structure_location"),
            bbox=bbox(raw.get("bbox")),
            coordinate_space=(
                CoordinateSpace(raw["coordinate_space"]) if raw.get("coordinate_space") else None
            ),
            page_width=raw.get("page_width"),
            page_height=raw.get("page_height"),
            confidence=raw.get("confidence"),
            language=raw.get("language"),
            style_hint=dict(raw.get("style_hint") or {}),
            extraction_candidates=tuple(
                candidate(item) for item in raw.get("extraction_candidates", [])
            ),
        )

    def typed_value(raw: Any, value_type: str) -> Any:
        if not isinstance(raw, str):
            return raw
        try:
            if value_type == "DATE":
                return date.fromisoformat(raw)
            if value_type == "DATETIME":
                return datetime.fromisoformat(raw)
        except ValueError:
            return raw
        return raw

    def cell(raw: dict[str, Any]) -> ParsedCell:
        value_type = str(raw["value_type"])
        return ParsedCell(
            row_index=int(raw["row_index"]),
            column_index=int(raw["column_index"]),
            address=str(raw["address"]),
            raw_value=typed_value(raw.get("raw_value"), value_type),
            display_text=str(raw.get("display_text") or ""),
            value_type=value_type,
            source_location=str(raw["source_location"]),
            extraction_method=str(raw["extraction_method"]),
            confidence=raw.get("confidence"),
            row_span=int(raw.get("row_span", 1)),
            column_span=int(raw.get("column_span", 1)),
            formula_text=raw.get("formula_text"),
            cached_value=typed_value(raw.get("cached_value"), value_type),
            parsed_value=typed_value(raw.get("parsed_value"), value_type),
            number_format=raw.get("number_format"),
            bbox=bbox(raw.get("bbox")),
            coordinate_space=(
                CoordinateSpace(raw["coordinate_space"]) if raw.get("coordinate_space") else None
            ),
            is_merged_expansion=bool(raw.get("is_merged_expansion", False)),
            source_fragment_sha256=raw.get("source_fragment_sha256"),
        )

    def table(raw: dict[str, Any]) -> ParsedTable:
        return ParsedTable(
            table_id=str(raw["table_id"]),
            row_count=int(raw["row_count"]),
            column_count=int(raw["column_count"]),
            cells=tuple(cell(item) for item in raw.get("cells", [])),
            extraction_method=str(raw["extraction_method"]),
            confidence=float(raw["confidence"]),
            page_number=raw.get("page_number"),
            sheet_name=raw.get("sheet_name"),
            title_candidate=raw.get("title_candidate"),
            bbox=bbox(raw.get("bbox")),
            coordinate_space=(
                CoordinateSpace(raw["coordinate_space"]) if raw.get("coordinate_space") else None
            ),
            continuation_of=raw.get("continuation_of"),
            continuation_confidence=raw.get("continuation_confidence"),
            rule_version=raw.get("rule_version"),
            metadata=dict(raw.get("metadata") or {}),
        )

    def image(raw: dict[str, Any]) -> ImageRegion:
        transform = raw.get("original_to_processed")
        return ImageRegion(
            image_id=str(raw["image_id"]),
            source_part=str(raw["source_part"]),
            pixel_width=int(raw["pixel_width"]),
            pixel_height=int(raw["pixel_height"]),
            content_sha256=str(raw["content_sha256"]),
            page_number=raw.get("page_number"),
            bbox=bbox(raw.get("bbox")),
            coordinate_space=(
                CoordinateSpace(raw["coordinate_space"]) if raw.get("coordinate_space") else None
            ),
            preview_ref=raw.get("preview_ref"),
            caption_candidate=raw.get("caption_candidate"),
            ocr_block_ids=tuple(str(item) for item in raw.get("ocr_block_ids", [])),
            original_to_processed=(
                tuple(float(item) for item in transform) if transform is not None else None
            ),
            processing_steps=tuple(dict(item) for item in raw.get("processing_steps", [])),
        )

    def page(raw: dict[str, Any]) -> ParsedPage:
        return ParsedPage(
            page_number=int(raw["page_number"]),
            width=raw.get("width"),
            height=raw.get("height"),
            coordinate_space=CoordinateSpace(raw["coordinate_space"]),
            classification=str(raw["classification"]),
            text_blocks=tuple(block(item) for item in raw.get("text_blocks", [])),
            table_ids=tuple(str(item) for item in raw.get("table_ids", [])),
            image_ids=tuple(str(item) for item in raw.get("image_ids", [])),
            native_character_count=int(raw.get("native_character_count", 0)),
            ocr_character_count=int(raw.get("ocr_character_count", 0)),
            image_coverage_ratio=float(raw.get("image_coverage_ratio", 0.0)),
            metadata=dict(raw.get("metadata") or {}),
        )

    def issue(raw: dict[str, Any]) -> ParseIssue:
        return ParseIssue(
            code=str(raw["code"]),
            message=str(raw["message"]),
            severity=IssueSeverity(raw.get("severity", "WARNING")),
            page_number=raw.get("page_number"),
            object_id=raw.get("object_id"),
            location=raw.get("location"),
            retryable=bool(raw.get("retryable", False)),
        )

    return ParsedDocument(
        document_id=str(value["document_id"]),
        source=SourceReference(**value["source"]),
        media_type=str(value["media_type"]),
        document_kind=str(value["document_kind"]),
        parser_id=str(value["parser_id"]),
        parser_version=str(value["parser_version"]),
        page_count=int(value["page_count"]),
        pages=tuple(page(item) for item in value.get("pages", [])),
        text_blocks=tuple(block(item) for item in value.get("text_blocks", [])),
        tables=tuple(table(item) for item in value.get("tables", [])),
        images=tuple(image(item) for item in value.get("images", [])),
        metadata=dict(value.get("metadata") or {}),
        issues=tuple(issue(item) for item in value.get("issues", [])),
        parse_sha256=str(value.get("parse_sha256") or ""),
        contract_version=str(value.get("contract_version") or PARSING_CONTRACT_VERSION),
    )


__all__ = [
    "BoundingBox",
    "CoordinateSpace",
    "ExtractionCandidate",
    "ImageRegion",
    "PARSING_CONTRACT_VERSION",
    "ParseExecution",
    "ParseIssue",
    "ParsedCell",
    "ParsedDocument",
    "ParsedPage",
    "ParsedTable",
    "PreviewResource",
    "ReaderOutput",
    "TextBlock",
    "canonical_json",
    "parsed_document_from_dict",
    "source_fragment_sha256",
]
