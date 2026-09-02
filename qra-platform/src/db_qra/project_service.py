"""Ordinary-user project aggregation for the stage-5 guided journey."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qra_engine.dynamic import NODE_REGISTRY

from .database import QraDatabase, json_sha256
from .paths import PROJECT_ROOT
from .review_service import ReviewService

PROJECT_SERVICE_VERSION = "qra.project-journey/1.0.0"
DEMO_CASE_ID = "SYN-TEST-EDITION-V1-S00_BASELINE"
DEMO_NAME = "全合成QRA演示项目"
DEMO_SNAPSHOT_PATH = (
    PROJECT_ROOT
    / "resources"
    / "synthetic"
    / "full-chain-v1"
    / "stage2"
    / "generated"
    / "S00_BASELINE_D00_CLEAN"
    / "golden"
    / "expected-snapshot.json"
)
DEMO_SOURCE_ROOT = DEMO_SNAPSHOT_PATH.parents[1] / "source-documents"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_review_session_id(database: QraDatabase, conversion_job_id: str) -> str | None:
    with database.session() as connection:
        row = connection.execute(
            """
            SELECT id FROM review_session WHERE conversion_job_id = ?
            ORDER BY created_at DESC, id DESC LIMIT 1
            """,
            (conversion_job_id,),
        ).fetchone()
    return str(row["id"]) if row is not None else None


def _latest_run_for_snapshot(database: QraDatabase, snapshot_id: str) -> dict[str, Any] | None:
    return next(
        (row for row in database.list_runs() if str(row["snapshot_id"]) == snapshot_id),
        None,
    )


def _source_public(source: dict[str, Any]) -> dict[str, Any]:
    status = str(source.get("security_status") or "UPLOADED")
    label = {
        "READY_FOR_PARSE": "等待处理",
        "PARSED": "已安全解析",
        "PARSE_FAILED": "解析失败",
        "QUARANTINED": "已隔离",
    }.get(status, status)
    return {
        "name": source.get("relative_path")
        or source.get("original_file_name")
        or source.get("file_name"),
        "media_type": source.get("detected_media_type") or source.get("media_type"),
        "byte_count": int(source.get("byte_count") or 0),
        "status": status,
        "status_label": label,
        "duplicate": bool(source.get("duplicate_of_source_id")),
        "version_group": source.get("version_group_id"),
        "issue": source.get("security_issue_message"),
    }


def _demo_sources() -> list[dict[str, Any]]:
    if not DEMO_SOURCE_ROOT.is_dir():
        return []
    return [
        {
            "name": path.name,
            "media_type": path.suffix.lower().lstrip(".").upper(),
            "byte_count": path.stat().st_size,
            "status": "PARSED",
            "status_label": "演示资料已验收",
            "duplicate": False,
            "version_group": None,
            "issue": None,
        }
        for path in sorted(DEMO_SOURCE_ROOT.iterdir())
        if path.is_file()
    ]


def _journey(
    *,
    project: dict[str, Any],
    conversion: dict[str, Any] | None,
    review: dict[str, Any] | None,
    snapshot: dict[str, Any] | None,
    run: dict[str, Any] | None,
    nodes: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], dict[str, Any], str | None]:
    demo = bool(project["is_demo"])
    conversion_status = str((conversion or {}).get("status") or "")
    run_status = str((run or {}).get("status") or "")
    conversion_ready = demo or conversion_status in {
        "BLOCKED",
        "READY_FOR_CONFIRMATION",
        "CONFIRMED",
    }
    conversion_failed = conversion_status in {"FAILED", "CANCELLED"} or (
        conversion_status == "BLOCKED" and not (conversion or {}).get("stage4_result_sha256")
    )
    needs_review = bool(
        conversion
        and conversion.get("stage4_result_sha256")
        and snapshot is None
        and conversion_status in {"BLOCKED", "READY_FOR_CONFIRMATION", "CONFIRMED"}
    )
    run_failed = run_status == "FAILED"
    run_complete = run_status == "COMPLETED"
    report_ready = run_complete and bool((run or {}).get("summary"))

    if report_ready:
        status = "REPORT_READY"
        next_action = {"id": "OPEN_REPORT", "label": "查看结果与报告"}
        blocked_reason = None
    elif run_failed:
        status = "CALCULATION_FAILED"
        next_action = {"id": "RETRY_CALCULATION", "label": "重新计算"}
        blocked_reason = str(run.get("error_message") or "计算未完成，可从已确认数据重新开始")
    elif run_status in {"QUEUED", "RUNNING"}:
        status = "CALCULATING"
        next_action = {"id": "WAIT", "label": "计算进行中"}
        blocked_reason = None
    elif snapshot:
        status = "READY_TO_CALCULATE"
        next_action = {"id": "START_CALCULATION", "label": "开始风险计算"}
        blocked_reason = None
    elif needs_review:
        status = "NEEDS_REVIEW"
        next_action = {"id": "OPEN_REVIEW", "label": "处理标出的复核项"}
        unresolved = int((review or {}).get("progress", {}).get("unresolved", 0))
        blocked_reason = (
            f"还有 {unresolved} 个复核项需要处理"
            if unresolved
            else "请检查系统标出的字段并完成数据确认"
        )
    elif conversion_failed:
        status = "SOURCE_PROCESSING_FAILED"
        next_action = {"id": "RETRY_SOURCE_PROCESSING", "label": "重试资料处理"}
        blocked_reason = str(
            (conversion or {}).get("status_message")
            or ((conversion or {}).get("error") or {}).get("message")
            or "资料处理中断，请查看问题后重试"
        )
    elif conversion_status in {"QUEUED", "RUNNING"}:
        status = "PROCESSING_SOURCES"
        next_action = {"id": "WAIT", "label": "资料处理中"}
        blocked_reason = None
    else:
        status = "NEEDS_UPLOAD"
        next_action = {"id": "UPLOAD_FILES", "label": "上传项目资料"}
        blocked_reason = None

    failed_nodes = [row for row in nodes if row["status"] == "FAILED_ISOLATED"]
    skipped_nodes = [row for row in nodes if row["status"].startswith("SKIPPED")]
    if failed_nodes or skipped_nodes:
        blocked_reason = (
            f"{len(failed_nodes)} 个计算环节失败，{len(skipped_nodes)} 个因缺少资料未运行"
        )

    step_specs = [
        ("UPLOAD", "上传资料"),
        ("CONVERT", "自动整理"),
        ("REVIEW", "数据复核"),
        ("CONFIRM", "确认数据"),
        ("CALCULATE", "风险计算"),
        ("REPORT", "报告中心"),
    ]
    upload_done = bool(conversion or snapshot)
    convert_done = bool(snapshot or conversion_ready)
    review_done = bool(snapshot)
    confirm_done = bool(snapshot)
    calculate_done = run_complete
    completed = {
        "UPLOAD": upload_done,
        "CONVERT": convert_done,
        "REVIEW": review_done,
        "CONFIRM": confirm_done,
        "CALCULATE": calculate_done,
        "REPORT": report_ready,
    }
    active_by_status = {
        "NEEDS_UPLOAD": "UPLOAD",
        "PROCESSING_SOURCES": "CONVERT",
        "SOURCE_PROCESSING_FAILED": "CONVERT",
        "NEEDS_REVIEW": "REVIEW",
        "READY_TO_CALCULATE": "CALCULATE",
        "CALCULATING": "CALCULATE",
        "CALCULATION_FAILED": "CALCULATE",
        "REPORT_READY": "REPORT",
    }
    active = active_by_status[status]
    blocked_steps = {
        "SOURCE_PROCESSING_FAILED": "CONVERT",
        "NEEDS_REVIEW": "REVIEW",
        "CALCULATION_FAILED": "CALCULATE",
    }
    steps = []
    for step_id, label in step_specs:
        state = "COMPLETED" if completed[step_id] else "NOT_STARTED"
        if step_id == active:
            state = "BLOCKED" if blocked_steps.get(status) == step_id else "ACTIVE"
        steps.append({"id": step_id, "label": label, "state": state})
    return status, steps, next_action, blocked_reason


class ProjectService:
    def __init__(self, database: QraDatabase):
        self.database = database

    def create(
        self, *, name: str, case_id: str | None, actor: str = "local-user"
    ) -> dict[str, Any]:
        project = self.database.create_project(
            name=name,
            case_id=case_id,
            data_classification="PROJECT_DATA",
            actor=actor,
        )
        return self.get(str(project["id"]))

    def create_demo(self, *, actor: str = "local-user") -> dict[str, Any]:
        for project in self.database.list_projects(include_archived=False):
            if project["is_demo"] and project.get("case_id") == DEMO_CASE_ID:
                return {"created": False, "project": self.get(str(project["id"])), "run_id": None}
        envelope = _read_json(DEMO_SNAPSHOT_PATH)
        case = envelope["qra_input"]
        if json_sha256(case) != envelope["qra_input_sha256"]:
            raise ValueError("内置演示项目数据校验失败")
        if envelope["formal_report_allowed"] is not False:
            raise ValueError("演示项目必须保持正式报告门关闭")
        project = self.database.create_project(
            name=DEMO_NAME,
            case_id=DEMO_CASE_ID,
            data_classification="SYNTHETIC_TEST_ONLY",
            is_demo=True,
            actor=actor,
        )
        snapshot_id, _ = self.database.import_case(
            case,
            name=f"{DEMO_NAME} · 已确认数据",
            source_path="synthetic-demo://full-chain-v1/S00_BASELINE/D00_CLEAN",
        )
        self.database.attach_project_snapshot(str(project["id"]), snapshot_id)
        metadata = self.database.snapshot_metadata(snapshot_id)
        run_id = self.database.create_run(
            snapshot_id,
            str(metadata["payload_sha256"]),
            generate_charts=True,
        )
        self.database.attach_project_run(str(project["id"]), run_id)
        self.database.record_event(
            event_type="SYNTHETIC_DEMO_LOADED",
            entity_type="business_project",
            entity_id=str(project["id"]),
            detail={
                "snapshot_id": snapshot_id,
                "run_id": run_id,
                "formal_report_allowed": False,
                "source_file_count": len(_demo_sources()),
            },
            actor=actor,
        )
        return {"created": True, "project": self.get(str(project["id"])), "run_id": run_id}

    def list(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        return [
            self.get(str(project["id"]))
            for project in self.database.list_projects(include_archived=include_archived)
        ]

    def get(self, project_id: str) -> dict[str, Any]:
        project = self.database.get_project(project_id)
        conversion = None
        if project.get("conversion_job_id"):
            conversion = self.database.get_conversion_job(
                str(project["conversion_job_id"]), detailed=False
            )
            conversion["sources"] = self.database.list_conversion_sources(str(conversion["id"]))
            conversion["issue_summary"] = self.database._conversion_issue_summary(conversion)

        review = None
        if conversion:
            session_id = _latest_review_session_id(self.database, str(conversion["id"]))
            if session_id:
                review = ReviewService(self.database).get_session(session_id)

        snapshot_id = str(project.get("snapshot_id") or "")
        if not snapshot_id and conversion and conversion.get("snapshot_id"):
            snapshot_id = str(conversion["snapshot_id"])
        if not snapshot_id and review and review.get("confirmed_snapshot_id"):
            snapshot_id = str(review["confirmed_snapshot_id"])
        snapshot = self.database.snapshot_metadata(snapshot_id) if snapshot_id else None

        run = self.database.get_run(str(project["run_id"])) if project.get("run_id") else None
        if run is None and review and review.get("confirmed_run_id"):
            run = self.database.get_run(str(review["confirmed_run_id"]))
        if run is None and snapshot_id:
            run = _latest_run_for_snapshot(self.database, snapshot_id)
        nodes = self.database.list_nodes(str(run["id"])) if run else []
        artifacts = self.database.list_artifacts(str(run["id"])) if run else []
        controlled_reports = self.database.list_project_reports(project_id)
        controlled_report = next(
            (
                row
                for row in controlled_reports
                if run is not None and str(row.get("run_id")) == str(run["id"])
            ),
            None,
        )

        status, steps, next_action, blocked_reason = _journey(
            project=project,
            conversion=conversion,
            review=review,
            snapshot=snapshot,
            run=run,
            nodes=nodes,
        )
        if snapshot:
            completeness = 100
        elif review:
            completeness = int(review.get("progress", {}).get("percent", 0))
        elif conversion:
            completeness = min(95, max(10, int(conversion.get("progress") or 0)))
        else:
            completeness = 0

        sources = _demo_sources() if project["is_demo"] else [
            _source_public(row) for row in (conversion or {}).get("sources", [])
        ]
        missing_inputs = [
            missing
            for node in nodes
            for missing in node.get("missing_inputs", [])
        ]
        pending_issues = (
            int((conversion or {}).get("issue_summary", {}).get("blocking", 0))
            + int((review or {}).get("progress", {}).get("unresolved", 0))
            + len(missing_inputs)
            + sum(row["status"] == "FAILED_ISOLATED" for row in nodes)
        )
        completed_nodes = sum(row["status"] == "COMPLETED" for row in nodes)
        report_ready = bool(run and run["status"] == "COMPLETED")
        demo_parameter_packs = (
            [
                str(row.get("parameter_pack_id") or row.get("pack_id"))
                for row in _read_json(DEMO_SNAPSHOT_PATH).get("parameter_pack_bindings", [])
            ]
            if project["is_demo"]
            else []
        )
        numerical_consistency = bool(
            report_ready
            and (run or {}).get("result_sha256")
            and ((run or {}).get("summary") or {}).get("numerical_result_sha256")
        )
        citations_bound = bool(nodes) and all(row.get("standard_ref") for row in nodes)
        controlled_validation = (
            (controlled_report or {}).get("validation")
            if controlled_report is not None
            else None
        )
        controlled_checks = {
            row.get("check_id"): row
            for row in (controlled_validation or {}).get("checks", [])
        }
        controlled_numeric_pass = bool(
            controlled_report
            and (controlled_validation or {}).get("status") == "PASS"
            and controlled_checks.get("NUMERIC_REFERENCES", {}).get("status") == "PASS"
            and controlled_checks.get("RENDERED_NUMERIC_AND_WATERMARK", {}).get("status")
            == "PASS"
        )
        controlled_citations_bound = bool(
            controlled_report
            and controlled_checks.get("EVIDENCE_REFERENCES", {}).get("status") == "PASS"
            and controlled_checks.get("RESULT_REFERENCES", {}).get("status") == "PASS"
        )
        if controlled_report:
            latest_report = {
                "label": f"QRA受控测试报告 v{controlled_report['version_no']}",
                "run_id": run["id"],
                "report_id": controlled_report["id"],
                "version_no": controlled_report["version_no"],
                "status": controlled_report["status"],
                "html_url": f"/reports/{controlled_report['id']}/",
                "html_download_url": (
                    f"/admin/api/reports/{controlled_report['id']}/artifacts/html"
                ),
                "pdf_url": f"/admin/api/reports/{controlled_report['id']}/artifacts/pdf",
                "docx_url": f"/admin/api/reports/{controlled_report['id']}/artifacts/docx",
                "zip_url": f"/admin/api/reports/{controlled_report['id']}/export",
                "context_sha256": controlled_report["context_sha256"],
                "draft_sha256": controlled_report["draft_sha256"],
            }
        else:
            latest_report = (
                {
                    "label": "QRA计算结果",
                    "run_id": run["id"],
                    "html_url": f"/runs/{run['id']}/",
                    "zip_url": f"/admin/api/runs/{run['id']}/export",
                }
                if report_ready
                else None
            )
        result = {
            **project,
            "service_version": PROJECT_SERVICE_VERSION,
            "status": status,
            "status_label": {
                "NEEDS_UPLOAD": "等待资料",
                "PROCESSING_SOURCES": "正在整理资料",
                "SOURCE_PROCESSING_FAILED": "资料处理受阻",
                "NEEDS_REVIEW": "等待数据复核",
                "READY_TO_CALCULATE": "可以开始计算",
                "CALCULATING": "正在计算",
                "CALCULATION_FAILED": "计算未完成",
                "REPORT_READY": "报告已就绪",
            }[status],
            "journey_steps": steps,
            "next_action": next_action,
            "blocked_reason": blocked_reason,
            "data_completeness_percent": completeness,
            "pending_issue_count": pending_issues,
            "calculation_progress": {
                "completed": completed_nodes,
                "total": len(NODE_REGISTRY),
                "percent": round(completed_nodes / len(NODE_REGISTRY) * 100),
                "status": str((run or {}).get("status") or "NOT_STARTED"),
            },
            "latest_report": latest_report,
            "sources": sources,
            "review": review,
            "confirmed_data": snapshot,
            "calculation": run,
            "nodes": nodes,
            "calculation_versions": {
                "data_version": (snapshot or {}).get("id") or snapshot_id or None,
                "data_sha256": (snapshot or {}).get("payload_sha256"),
                "engine_version": (run or {}).get("engine_version"),
                "parameter_pack_ids": demo_parameter_packs,
                "parameter_binding": (
                    "随全合成测试快照受控绑定"
                    if project["is_demo"]
                    else "随已确认数据和计算引擎版本受控绑定"
                ),
            },
            "report_center": {
                "status": (
                    str(controlled_report["status"])
                    if controlled_report
                    else ("READY" if report_ready else "NOT_READY")
                ),
                "draft": (
                    str(controlled_report["status"]) == "DRAFT"
                    if controlled_report
                    else not bool(
                        ((run or {}).get("summary") or {}).get(
                            "formal_acceptance_judgement_allowed", False
                        )
                    )
                ),
                "completeness": (
                    "PASS"
                    if report_ready and completed_nodes == len(NODE_REGISTRY)
                    else "INCOMPLETE"
                ),
                "numerical_consistency": (
                    "PASS"
                    if controlled_numeric_pass or (not controlled_report and numerical_consistency)
                    else "PENDING"
                ),
                "citations": (
                    "BOUND"
                    if controlled_citations_bound or (not controlled_report and citations_bound)
                    else "PENDING"
                ),
                "manual_status": (
                    str(controlled_report["status"])
                    if controlled_report
                    else (
                        "SYNTHETIC_NOT_FOR_APPROVAL"
                        if project["is_demo"]
                        else "PENDING_REVIEW"
                    )
                ),
                "formats": {
                    "HTML": report_ready,
                    "ZIP": report_ready,
                    "PDF": bool(controlled_report),
                    "DOCX": bool(controlled_report),
                },
                "artifact_count": len(artifacts) + (6 if controlled_report else 0),
                "controlled_report_id": (controlled_report or {}).get("id"),
                "version_no": (controlled_report or {}).get("version_no"),
                "generation_mode": (controlled_report or {}).get("generation_mode"),
                "provider_id": (controlled_report or {}).get("provider_id"),
                "context_sha256": (controlled_report or {}).get("context_sha256"),
                "draft_sha256": (controlled_report or {}).get("draft_sha256"),
                "history": controlled_reports,
            },
            "missing_inputs": missing_inputs,
            "synthetic_warning": (
                "本项目全部资料、参数和结果均为人工合成，仅用于软件演示与测试，"
                "不得用于真实资产评价、监管申报或安全决策。"
                if project["is_demo"]
                else None
            ),
            "advanced_audit": {
                "project_id": project["id"],
                "conversion_job_id": (conversion or {}).get("id"),
                "review_session_id": (review or {}).get("id"),
                "snapshot_id": snapshot_id or None,
                "snapshot_sha256": (snapshot or {}).get("payload_sha256"),
                "run_id": (run or {}).get("id"),
                "input_sha256": (run or {}).get("input_sha256"),
                "result_sha256": (run or {}).get("result_sha256"),
                "engine_version": (run or {}).get("engine_version"),
                "controlled_report_id": (controlled_report or {}).get("id"),
                "report_context_sha256": (controlled_report or {}).get("context_sha256"),
                "report_draft_sha256": (controlled_report or {}).get("draft_sha256"),
            },
        }
        return result


__all__ = [
    "DEMO_CASE_ID",
    "DEMO_NAME",
    "PROJECT_SERVICE_VERSION",
    "ProjectService",
]
