"""Repeatable preparation of the packaged full-synthetic end-to-end demo."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .controlled_reporting import ControlledReportService
from .database import QraDatabase
from .engine_adapter import execute_run
from .project_service import ProjectService

DEMO_RELEASE_NAME = "QRA全合成端到端演示版_v1"
DEMO_RELEASE_VERSION = "1.0.0"


def prepare_full_synthetic_demo(
    database: QraDatabase,
    *,
    runtime_root: Path,
    actor: str = "demo-launcher",
    generate_report: bool = True,
) -> dict[str, Any]:
    """Idempotently load, calculate, and optionally report the built-in demo."""

    database.initialize()
    project_service = ProjectService(database)
    loaded = project_service.create_demo(actor=actor)
    project = loaded["project"]
    project_id = str(project["id"])
    run_id = str((project.get("advanced_audit") or {}).get("run_id") or loaded.get("run_id") or "")
    if not run_id:
        raise ValueError("演示项目未绑定计算任务")
    run = database.get_run(run_id)
    if run["status"] == "RUNNING":
        database.requeue_interrupted_runs()
        run = database.get_run(run_id)
    if run["status"] == "FAILED":
        snapshot_id = str(project["advanced_audit"]["snapshot_id"])
        snapshot_sha256 = str(project["advanced_audit"]["snapshot_sha256"])
        run_id = database.create_run(snapshot_id, snapshot_sha256, generate_charts=True)
        database.attach_project_run(project_id, run_id)
        run = database.get_run(run_id)
    if run["status"] == "QUEUED":
        execute_run(
            database,
            run_id,
            str(run["snapshot_id"]),
            generate_charts=True,
            runtime_root=runtime_root,
        )
        run = database.get_run(run_id)
    if run["status"] != "COMPLETED":
        raise ValueError(f"演示计算未完成：{run['status']}")

    report = database.latest_project_report(project_id)
    if generate_report and (report is None or str(report["run_id"]) != run_id):
        report = ControlledReportService(database).generate(
            project_id,
            actor=actor,
        ).report
    project = project_service.get(project_id)
    database.record_event(
        event_type="FULL_SYNTHETIC_DEMO_PREPARED",
        entity_type="business_project",
        entity_id=project_id,
        detail={
            "release_name": DEMO_RELEASE_NAME,
            "release_version": DEMO_RELEASE_VERSION,
            "run_id": run_id,
            "report_id": (report or {}).get("id"),
            "formal_report_allowed": False,
        },
        actor=actor,
    )
    return {
        "status": "PASS",
        "release_name": DEMO_RELEASE_NAME,
        "release_version": DEMO_RELEASE_VERSION,
        "project_id": project_id,
        "project_status": project["status"],
        "snapshot_id": project["advanced_audit"]["snapshot_id"],
        "snapshot_sha256": project["advanced_audit"]["snapshot_sha256"],
        "run_id": run_id,
        "result_sha256": run["result_sha256"],
        "completed_node_count": project["calculation_progress"]["completed"],
        "report_id": (report or {}).get("id"),
        "report_status": (report or {}).get("status"),
        "formal_report_allowed": False,
        "project_url": f"/projects/{project_id}/",
        "report_url": f"/reports/{report['id']}/" if report else None,
    }


__all__ = [
    "DEMO_RELEASE_NAME",
    "DEMO_RELEASE_VERSION",
    "prepare_full_synthetic_demo",
]
