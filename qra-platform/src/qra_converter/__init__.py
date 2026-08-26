"""Source-file to QRA JSON conversion boundary.

Reader and mapping implementations are intentionally added independently from
the verified calculation engine.
"""

from .contracts import (
    ConversionIssue,
    ConversionResult,
    FieldLineage,
    IssueSeverity,
    RawRow,
    RawTable,
    ReviewAuditEntry,
    ReviewItem,
    SourceReference,
)
from .ports import MappingProvider, SourceReader

__all__ = [
    "ConversionIssue",
    "ConversionResult",
    "FieldLineage",
    "IssueSeverity",
    "MappingProvider",
    "RawRow",
    "RawTable",
    "ReviewAuditEntry",
    "ReviewItem",
    "SourceReader",
    "SourceReference",
]
