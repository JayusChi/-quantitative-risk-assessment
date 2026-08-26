"""Ports implemented by concrete readers and versioned mapping providers."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from .contracts import ConversionIssue, RawTable


class SourceReader(Protocol):
    reader_id: str

    def supports(self, path: Path) -> bool:
        """Return whether this reader can faithfully extract the source."""

    def read(self, path: Path) -> Sequence[RawTable]:
        """Return source-faithful tables without applying business mappings."""


class MappingProvider(Protocol):
    mapping_version: str

    def map_records(
        self, records: Sequence[dict[str, Any]]
    ) -> tuple[dict[str, Any], Sequence[ConversionIssue]]:
        """Return a standard JSON draft and structured mapping issues."""
