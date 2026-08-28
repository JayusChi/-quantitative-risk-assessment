"""Unified stage-three document parsing API."""

from .contracts import (
    BoundingBox,
    CoordinateSpace,
    ImageRegion,
    ParsedCell,
    ParsedDocument,
    ParsedPage,
    ParsedTable,
    ParseExecution,
    ParseIssue,
    TextBlock,
)

__all__ = [
    "BoundingBox",
    "CoordinateSpace",
    "ImageRegion",
    "ParseExecution",
    "ParseIssue",
    "ParsedCell",
    "ParsedDocument",
    "ParsedPage",
    "ParsedTable",
    "TextBlock",
]
