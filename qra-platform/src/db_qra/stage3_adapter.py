"""Database-side persistence adapter for the stage-3 synthetic workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .database import QraDatabase


def persist_confirmed_snapshot(
    database_path: Path,
    qra_input: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """Persist an explicitly confirmed stage-3 result as an immutable snapshot."""

    database = QraDatabase(database_path)
    snapshot_id, created = database.import_case(
        qra_input,
        name="S00_BASELINE × D00_CLEAN 阶段3确认快照",
        source_path="stage3://synthetic-full-chain",
    )
    database.record_event(
        event_type="STAGE3_SNAPSHOT_CONFIRMED",
        entity_type="input_snapshot",
        entity_id=snapshot_id,
        detail=provenance,
        actor="stage3-acceptance-reviewer",
    )
    metadata = database.snapshot_metadata(snapshot_id)
    return {
        "database_path": str(database_path),
        "database_snapshot_id": snapshot_id,
        "created": created,
        "payload_sha256": metadata["payload_sha256"],
        "immutable_storage": "SQLite input_snapshot update/delete triggers",
    }


__all__ = ["persist_confirmed_snapshot"]
