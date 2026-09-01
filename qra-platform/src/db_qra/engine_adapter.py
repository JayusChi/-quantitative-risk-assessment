from __future__ import annotations

import json
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from qra_engine import ENGINE_VERSION
from qra_engine.data_categories import resolve_data_categories
from qra_engine.dynamic import plan_dynamic_flow, run_dynamic_flow
from qra_engine.validation import validate_import_contract

from .database import QraDatabase, json_sha256

DB_ADAPTER_VERSION = "0.1.0"


def preview_case(case: dict[str, Any]) -> dict[str, Any]:
    """Return a non-persistent data and capability preview for Admin uploads."""
    contract = validate_import_contract(case)
    contract.raise_for_errors()
    segments = case.get("segments", [])
    plan = plan_dynamic_flow(case, None)
    categories = resolve_data_categories(case)
    raw_categories = case.get("raw_data_categories", {})
    if not isinstance(raw_categories, dict):
        raw_categories = {}
    raw_record_count = sum(
        len(category.get("records", []))
        for category in raw_categories.values()
        if isinstance(category, dict)
    )
    indicators = case.get("engineering_indicators", {})
    indicator_count = 0
    if isinstance(indicators, dict):
        indicator_count += len(indicators.get("observations_global", {}))
        indicator_count += sum(
            len(rows)
            for rows in indicators.get("observations_by_segment", {}).values()
            if isinstance(rows, dict)
        )
    runnable = [
        row for row in plan.get("plan", []) if row.get("status") == "RUNNABLE"
    ]
    blocked = [
        row for row in plan.get("plan", []) if row.get("status") != "RUNNABLE"
    ]
    metadata = case.get("metadata", {})
    return {
        "valid_json": True,
        "payload_sha256": json_sha256(case),
        "project_name": metadata.get("project_name") or metadata.get("case_id"),
        "schema_version": case.get("schema_version", "dynamic-case-v1"),
        "top_level_sections": sorted(case),
        "segment_count": len(segments),
        "total_length_km": sum(
            float(row.get("length_km", 0.0))
            for row in segments
            if isinstance(row, dict)
        ),
        "data_category_count": categories["category_count"],
        "data_category_definition": categories["definition"],
        "data_categories": categories["categories"],
        "raw_record_count": raw_record_count,
        "indicator_observation_count": indicator_count,
        "runnable_node_count": len(runnable),
        "blocked_node_count": len(blocked),
        "runnable_nodes": runnable,
        "blocked_nodes": blocked,
    }


def execute_run(
    database: QraDatabase,
    run_id: str,
    snapshot_id: str,
    *,
    targets: Iterable[str] | None = None,
    generate_charts: bool = True,
    runtime_root: Path | str | None = None,
) -> dict[str, Any]:
    """Execute a previously queued run, allowing an Admin API to return early."""
    run = database.get_run(run_id)
    if str(run["snapshot_id"]) != str(snapshot_id):
        raise ValueError("计算任务与输入快照不匹配")
    snapshot = database.snapshot_metadata(snapshot_id)
    if str(run["input_sha256"]) != str(snapshot["payload_sha256"]):
        raise ValueError("计算任务输入哈希与不可变快照不一致")
    if targets is None and run.get("target_node_ids"):
        targets = list(run["target_node_ids"])
    case = database.load_snapshot(snapshot_id)
    database.set_run_running(
        run_id,
        engine_version=f"qra-engine/{ENGINE_VERSION}; db-adapter/{DB_ADAPTER_VERSION}",
    )
    work_parent = Path(runtime_root).resolve() if runtime_root else None
    if work_parent:
        work_parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(
            prefix=f"{run_id}-", dir=work_parent
        ) as temporary_directory:
            output_dir = Path(temporary_directory)
            run_dynamic_flow(
                case,
                output_dir,
                targets=targets,
                generate_charts=generate_charts,
                job_id=run_id,
            )
            manifest_path = output_dir / "dynamic_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            database.complete_run(
                run_id,
                output_dir=output_dir,
                manifest=manifest,
            )
    except Exception as exc:
        database.fail_run(run_id, f"{type(exc).__name__}: {exc}")
        raise
    return database.get_run(run_id)


def calculate_snapshot(
    database: QraDatabase,
    snapshot_id: str,
    *,
    targets: Iterable[str] | None = None,
    generate_charts: bool = True,
    runtime_root: Path | str | None = None,
) -> dict[str, Any]:
    """Load one DB snapshot, call the unchanged engine, and persist all outputs."""
    case = database.load_snapshot(snapshot_id)
    run_id = database.create_run(
        snapshot_id,
        json_sha256(case),
        targets=list(targets) if targets is not None else None,
        generate_charts=generate_charts,
    )
    return execute_run(
        database,
        run_id,
        snapshot_id,
        targets=targets,
        generate_charts=generate_charts,
        runtime_root=runtime_root,
    )


__all__ = [
    "DB_ADAPTER_VERSION",
    "ENGINE_VERSION",
    "calculate_snapshot",
    "execute_run",
    "preview_case",
]
