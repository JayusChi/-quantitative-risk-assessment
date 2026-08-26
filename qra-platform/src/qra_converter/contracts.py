"""Stable data contracts shared by converter implementations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


class IssueSeverity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass(frozen=True)
class SourceReference:
    source_id: str
    source_path: str
    reader_id: str
    checksum_sha256: str
    location: str | None = None


@dataclass(frozen=True)
class RawRow:
    """One source-faithful row before header or business-field recognition."""

    row_number: int
    cells: tuple[Any, ...]


@dataclass(frozen=True)
class RawTable:
    """A worksheet or delimited file represented without mapping semantics."""

    source: SourceReference
    sheet_name: str
    rows: tuple[RawRow, ...]
    extraction_method: str = "STRUCTURED_TABLE"
    confidence: float = 1.0
    requires_review: bool = False


@dataclass(frozen=True)
class FieldLineage:
    """Trace one emitted value back to its exact source cell."""

    source_id: str
    file_name: str
    sheet_name: str
    row_number: int
    column_name: str
    target_path: str
    original_value: Any
    normalized_value: Any
    source_unit: str | None = None
    target_unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("original_value", "normalized_value"):
            if isinstance(value[key], date | datetime):
                value[key] = value[key].isoformat()
        return value


@dataclass(frozen=True)
class ConversionIssue:
    severity: IssueSeverity
    code: str
    message: str
    source_id: str | None = None
    location: str | None = None
    target_path: str | None = None


@dataclass(frozen=True)
class ReviewItem:
    """A stable, machine-readable request for a human conversion decision."""

    review_id: str
    kind: str
    reason: str
    target_path: str
    record_key: dict[str, Any]
    proposed_value: Any
    candidates: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    confidence: float | None = None
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewAuditEntry:
    """An immutable record of one applied human review decision."""

    review_id: str
    action: str
    reviewer: str
    reviewed_at: str
    reason: str
    target_path: str
    before_value: Any
    after_value: Any

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConversionResult:
    """A draft payload plus all evidence required for review and audit."""

    payload: dict[str, Any]
    mapping_version: str
    sources: tuple[SourceReference, ...] = field(default_factory=tuple)
    issues: tuple[ConversionIssue, ...] = field(default_factory=tuple)
    lineage: tuple[FieldLineage, ...] = field(default_factory=tuple)
    contract_status: str | None = None
    review_items: tuple[ReviewItem, ...] = field(default_factory=tuple)
    review_audit: tuple[ReviewAuditEntry, ...] = field(default_factory=tuple)
    review_decision_source: dict[str, Any] | None = None
    capability_plan: dict[str, Any] | None = None

    @property
    def has_errors(self) -> bool:
        return any(issue.severity is IssueSeverity.ERROR for issue in self.issues)

    @property
    def has_blocking_reviews(self) -> bool:
        return any(item.blocking for item in self.review_items)

    @property
    def is_blocked(self) -> bool:
        return self.has_errors or self.has_blocking_reviews

    def to_dict(self) -> dict[str, Any]:
        return {
            "payload": self.payload,
            "mapping_version": self.mapping_version,
            "sources": [asdict(source) for source in self.sources],
            "issues": [
                {**asdict(issue), "severity": issue.severity.value} for issue in self.issues
            ],
            "lineage": [row.to_dict() for row in self.lineage],
            "contract_status": self.contract_status,
            "review_items": [item.to_dict() for item in self.review_items],
            "review_audit": [entry.to_dict() for entry in self.review_audit],
            "review_decision_source": self.review_decision_source,
            "capability_plan": self.capability_plan,
            "status": "BLOCKED" if self.is_blocked else "READY_FOR_REVIEW",
        }
