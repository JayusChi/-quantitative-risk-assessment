"""Roadmap Stage 4/M1 lineage summary and acceptance rules."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any

from .database import QraDatabase, normalize_artifact_path

MILESTONE = "Roadmap Stage 4 / M1：原始资料到筛查报告闭环"
SUMMARY_SCHEMA_VERSION = "qra.roadmap-stage4-summary/1.0.0"


def _json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, dict | list):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return fallback


def _one(connection: Any, sql: str, parameters: tuple[Any, ...]) -> Any:
    return connection.execute(sql, parameters).fetchone()


def _count(connection: Any, sql: str, parameters: tuple[Any, ...]) -> int:
    return int(connection.execute(sql, parameters).fetchone()[0])


def _safe_artifact(row: Any) -> tuple[dict[str, Any], bool]:
    path = str(row["path"])
    content = bytes(row["content"])
    try:
        safe_path = normalize_artifact_path(path)
    except ValueError:
        safe_path = path
        valid = False
    else:
        valid = (
            safe_path == path
            and len(content) > 0
            and len(content) == int(row["byte_count"])
            and hashlib.sha256(content).hexdigest() == str(row["sha256"])
        )
    return (
        {
            "path": safe_path,
            "kind": PurePosixPath(safe_path).suffix.lstrip(".").upper() or "BINARY",
            "sha256": str(row["sha256"]),
            "byte_count": int(row["byte_count"]),
            "integrity_status": "PASS" if valid else "FAILED",
        },
        valid,
    )


def build_m1_summary(
    database: QraDatabase,
    conversion_job_id: str,
    *,
    review_session_id: str | None = None,
    calculation_run_id: str | None = None,
) -> dict[str, Any]:
    """Build one foreign-key-scoped, non-mutating M1 closure summary."""

    database.initialize()
    with database.session() as connection:
        job = _one(connection, "SELECT * FROM conversion_job WHERE id = ?", (conversion_job_id,))
        if job is None:
            raise KeyError(f"转换任务不存在：{conversion_job_id}")

        source_count = _count(
            connection,
            "SELECT count(*) FROM conversion_source WHERE job_id = ?",
            (conversion_job_id,),
        )
        parsed_source_count = _count(
            connection,
            "SELECT count(*) FROM conversion_source "
            "WHERE job_id = ? AND security_status = 'PARSED'",
            (conversion_job_id,),
        )
        failed_source_count = _count(
            connection,
            """SELECT count(*) FROM conversion_source
               WHERE job_id = ? AND security_status IN ('PARSE_FAILED','QUARANTINED')""",
            (conversion_job_id,),
        )
        parse_artifact_count = _count(
            connection,
            "SELECT count(*) FROM conversion_parse_artifact WHERE job_id = ?",
            (conversion_job_id,),
        )
        extracted_entity_count = _count(
            connection,
            "SELECT count(*) FROM extracted_entity WHERE job_id = ?",
            (conversion_job_id,),
        )
        candidate_field_count = _count(
            connection,
            "SELECT count(*) FROM candidate_field WHERE job_id = ?",
            (conversion_job_id,),
        )
        candidate_evidence_link_count = _count(
            connection,
            "SELECT count(*) FROM candidate_evidence_link WHERE job_id = ?",
            (conversion_job_id,),
        )
        issue_rows = connection.execute(
            "SELECT severity, blocking, count(*) AS count FROM quality_issue "
            "WHERE job_id = ? GROUP BY severity, blocking",
            (conversion_job_id,),
        ).fetchall()
        quality_issue_counts: dict[str, int] = {}
        for row in issue_rows:
            key = str(row["severity"])
            quality_issue_counts[key] = quality_issue_counts.get(key, 0) + int(row["count"])
        quality_issue_count = sum(quality_issue_counts.values())
        blocking_issue_count = sum(int(row["count"]) for row in issue_rows if row["blocking"])

        session = None
        if review_session_id:
            session = _one(
                connection,
                "SELECT * FROM review_session WHERE id = ? AND conversion_job_id = ?",
                (review_session_id, conversion_job_id),
            )
            if session is None:
                raise ValueError("复核会话不属于当前转换任务")
        else:
            session = _one(
                connection,
                """SELECT * FROM review_session WHERE conversion_job_id = ?
                   ORDER BY confirmed_at IS NULL, confirmed_at DESC, created_at DESC LIMIT 1""",
                (conversion_job_id,),
            )
        session_id = str(session["id"]) if session is not None else None
        target_node_ids = (
            [str(value) for value in _json(session["target_node_ids_json"], [])]
            if session is not None
            else [str(value) for value in _json(job["target_node_ids_json"], [])]
        )
        review_decision_count = (
            _count(
                connection,
                """SELECT count(*) FROM review_decision decision
                   WHERE decision.session_id = ? AND NOT EXISTS (
                       SELECT 1 FROM review_decision newer
                       WHERE newer.supersedes_decision_id = decision.id
                   )""",
                (session_id,),
            )
            if session_id
            else 0
        )

        review_provenance = None
        if session_id:
            review_provenance = _one(
                connection,
                """SELECT * FROM input_snapshot_review_provenance
                   WHERE conversion_job_id = ? AND review_session_id = ?
                   ORDER BY confirmed_at DESC LIMIT 1""",
                (conversion_job_id, session_id),
            )
        gate = None
        if review_provenance is not None:
            gate = _one(
                connection,
                "SELECT * FROM review_gate_run WHERE id = ?",
                (str(review_provenance["review_gate_run_id"]),),
            )
        elif session_id:
            gate = _one(
                connection,
                "SELECT * FROM review_gate_run WHERE session_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            )
        gate_result = _json(gate["result_json"], {}) if gate is not None else {}

        snapshot_id = None
        if review_provenance is not None:
            snapshot_id = str(review_provenance["snapshot_id"])
        elif session is not None and session["confirmed_snapshot_id"]:
            snapshot_id = str(session["confirmed_snapshot_id"])
        elif job["snapshot_id"]:
            snapshot_id = str(job["snapshot_id"])
        snapshot = (
            _one(connection, "SELECT * FROM input_snapshot WHERE id = ?", (snapshot_id,))
            if snapshot_id
            else None
        )
        provenance_count = (
            _count(
                connection,
                """SELECT count(*) FROM input_snapshot_provenance
                   WHERE snapshot_id = ? AND conversion_job_id = ?""",
                (snapshot_id, conversion_job_id),
            )
            if snapshot_id
            else 0
        )
        review_provenance_count = (
            _count(
                connection,
                """SELECT count(*) FROM input_snapshot_review_provenance
                   WHERE snapshot_id = ? AND conversion_job_id = ? AND review_session_id = ?""",
                (snapshot_id, conversion_job_id, session_id),
            )
            if snapshot_id and session_id
            else 0
        )

        run = None
        selected_run_id = calculation_run_id
        if selected_run_id is None and session is not None and session["confirmed_run_id"]:
            selected_run_id = str(session["confirmed_run_id"])
        if selected_run_id:
            run = _one(connection, "SELECT * FROM calculation_run WHERE id = ?", (selected_run_id,))
            if run is None:
                raise KeyError(f"计算任务不存在：{selected_run_id}")
            if session_id and run["review_session_id"] != session_id:
                raise ValueError("计算任务不属于本次复核确认")

        node_rows = (
            connection.execute(
                "SELECT * FROM calculation_node WHERE run_id = ? ORDER BY sequence_no, node_id",
                (selected_run_id,),
            ).fetchall()
            if run is not None
            else []
        )
        nodes = {str(row["node_id"]): row for row in node_rows}
        completed_target_nodes = [
            node_id
            for node_id in target_node_ids
            if node_id in nodes and str(nodes[node_id]["status"]) == "COMPLETED"
        ]
        failed_nodes = [
            str(row["node_id"])
            for row in node_rows
            if str(row["status"]) == "FAILED"
        ]
        skipped_nodes_with_reasons = [
            {
                "node_id": str(row["node_id"]),
                "missing_inputs": _json(row["missing_inputs_json"], []),
                "blocked_dependencies": _json(row["blocked_dependencies_json"], []),
            }
            for row in node_rows
            if str(row["status"]) == "SKIPPED"
        ]
        segment_result_count = (
            _count(
                connection,
                "SELECT count(*) FROM calculation_segment_result WHERE run_id = ?",
                (selected_run_id,),
            )
            if run is not None
            else 0
        )
        artifact_rows = (
            connection.execute(
                "SELECT path, content_type, content, byte_count, sha256 "
                "FROM calculation_artifact WHERE run_id = ? ORDER BY path",
                (selected_run_id,),
            ).fetchall()
            if run is not None
            else []
        )
        report_artifacts: list[dict[str, Any]] = []
        artifact_integrity = True
        report_content: bytes | None = None
        for row in artifact_rows:
            item, valid = _safe_artifact(row)
            report_artifacts.append(item)
            artifact_integrity = artifact_integrity and valid
            if item["path"] == "report_dashboard.html":
                report_content = bytes(row["content"])
        report_entry = next(
            (row for row in report_artifacts if row["path"] == "report_dashboard.html"), None
        )

    errors: list[str] = []
    if source_count <= 0:
        errors.append("M1.NO_RAW_SOURCE")
    if str(job["status"]) != "CONFIRMED":
        errors.append("M1.CONVERSION_NOT_CONFIRMED")
    if parsed_source_count <= 0 or parse_artifact_count <= 0:
        errors.append("M1.NO_PARSE_ARTIFACT")
    if extracted_entity_count <= 0:
        errors.append("M1.NO_EXTRACTED_ENTITY")
    if candidate_field_count <= 0:
        errors.append("M1.NO_CANDIDATE")
    if candidate_evidence_link_count <= 0:
        errors.append("M1.NO_EVIDENCE")
    if session is None:
        errors.append("M1.NO_REVIEW_SESSION")
    elif str(session["status"]) != "CONFIRMED":
        errors.append("M1.REVIEW_NOT_CONFIRMED")
    if gate is None or str(gate["status"]) != "PASS":
        errors.append("M1.GATE_NOT_PASS")
    if snapshot is None or provenance_count <= 0:
        errors.append("M1.SNAPSHOT_NOT_FROM_CONVERSION")
    if review_provenance_count <= 0:
        errors.append("M1.NO_REVIEW_PROVENANCE")
    if run is None:
        errors.append("M1.NO_CALCULATION_RUN")
    else:
        if str(run["snapshot_id"]) != str(snapshot_id):
            errors.append("M1.RUN_SNAPSHOT_MISMATCH")
        if snapshot is None or str(run["input_sha256"]) != str(snapshot["payload_sha256"]):
            errors.append("M1.RUN_INPUT_HASH_MISMATCH")
        if str(run["status"]) != "COMPLETED":
            errors.append("M1.RUN_NOT_COMPLETED")
    if set(completed_target_nodes) != set(target_node_ids) or not target_node_ids:
        errors.append("M1.TARGET_NODE_INCOMPLETE")
    if segment_result_count <= 0:
        errors.append("M1.NO_SEGMENT_RESULT")
    if report_entry is None:
        errors.append("M1.REPORT_MISSING")
    elif report_content is not None:
        report_text = report_content.decode("utf-8", errors="replace")
        if "筛查" not in report_text or "尚不能直接作为接受性结论" not in report_text:
            errors.append("M1.REPORT_BOUNDARY_MISSING")
    if not artifact_integrity:
        errors.append("M1.REPORT_INTEGRITY")
    reverse_trace = bool(
        run is not None
        and session_id
        and run["review_session_id"] == session_id
        and snapshot_id
        and provenance_count > 0
        and review_provenance_count > 0
    )
    if not reverse_trace:
        errors.append("M1.REVERSE_TRACE_FAILED")

    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "milestone": MILESTONE,
        "pilot_id": job["pilot_id"],
        "pilot_version": job["pilot_version"],
        "pilot_manifest_sha256": job["pilot_manifest_sha256"],
        "target_node_ids": target_node_ids,
        "conversion_job_id": conversion_job_id,
        "conversion_status": str(job["status"]),
        "source_count": source_count,
        "parsed_source_count": parsed_source_count,
        "failed_source_count": failed_source_count,
        "parse_artifact_count": parse_artifact_count,
        "extracted_entity_count": extracted_entity_count,
        "candidate_field_count": candidate_field_count,
        "candidate_evidence_link_count": candidate_evidence_link_count,
        "quality_issue_count": quality_issue_count,
        "quality_issue_counts": quality_issue_counts,
        "blocking_issue_count": blocking_issue_count,
        "review_session_id": session_id,
        "review_status": str(session["status"]) if session is not None else None,
        "review_revision": int(session["revision"]) if session is not None else None,
        "review_decision_count": review_decision_count,
        "gate_run_id": str(gate["id"]) if gate is not None else None,
        "gate_status": str(gate["status"]) if gate is not None else None,
        "unresolved_count": int(gate["unresolved_field_count"]) if gate is not None else None,
        "gate_blocking_count": int(gate["blocking_issue_count"]) if gate is not None else None,
        "candidate_set_hash": str(session["candidate_set_hash"]) if session is not None else None,
        "decision_set_hash": str(gate["decision_set_hash"]) if gate is not None else None,
        "snapshot_id": snapshot_id,
        "snapshot_payload_sha256": (
            str(snapshot["payload_sha256"]) if snapshot is not None else None
        ),
        "provenance_count": provenance_count,
        "review_provenance_count": review_provenance_count,
        "calculation_run_id": str(run["id"]) if run is not None else None,
        "calculation_status": str(run["status"]) if run is not None else None,
        "completed_target_nodes": completed_target_nodes,
        "skipped_nodes_with_reasons": skipped_nodes_with_reasons,
        "failed_nodes": failed_nodes,
        "segment_result_count": segment_result_count,
        "report_artifact_count": len(report_artifacts),
        "report_entry_path": report_entry["path"] if report_entry else None,
        "report_artifacts": report_artifacts,
        "reverse_trace_status": "PASS" if reverse_trace else "FAILED",
        "gate_summary": gate_result,
        "checks": [{"code": code, "status": "FAILED"} for code in errors],
        "error_codes": errors,
        "status": "PASS" if not errors else "FAILED",
    }


__all__ = ["MILESTONE", "SUMMARY_SCHEMA_VERSION", "build_m1_summary"]
