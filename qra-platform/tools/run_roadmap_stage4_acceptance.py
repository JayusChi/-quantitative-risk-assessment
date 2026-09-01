"""Run or inspect the Roadmap Stage 4 raw-source-to-screening-report closure."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = (PROJECT_ROOT / "workspace").resolve()
ENGINEERING_SOURCE = (
    PROJECT_ROOT / "tests" / "fixtures" / "converter_jiujiang_stage1" / "脱敏高后果区.csv"
)
DEFAULT_PILOT_ID = "jiujiang-qra-screening-pilot-v1"
ALLOWED_SOURCE_SUFFIXES = {".csv", ".xls", ".xlsx", ".docx", ".pdf", ".png", ".jpg", ".jpeg"}
MAX_PILOT_SOURCE_BYTES = 256 * 1024 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("engineering", "pilot"), required=True)
    parser.add_argument("--pilot-id", default=DEFAULT_PILOT_ID)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--reviewer")
    parser.add_argument("--authorized", action="store_true")
    parser.add_argument("--allow-external-models", action="store_true")
    parser.add_argument("--record", type=Path)
    parser.add_argument("--database", type=Path, help="Inspect an existing browser acceptance DB")
    parser.add_argument("--conversion-id")
    parser.add_argument("--review-session-id")
    parser.add_argument("--run-id")
    parser.add_argument("--browser-evidence", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_manifest(paths: list[Path]) -> tuple[list[dict[str, object]], str]:
    rows = [
        {
            "source_id": f"SRC-{index:04d}",
            "sha256": _sha256(path),
            "byte_count": path.stat().st_size,
            "suffix": path.suffix.casefold(),
        }
        for index, path in enumerate(paths, start=1)
    ]
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return rows, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _pilot_source_paths(source_root: Path, pilot_manifest: dict[str, object]) -> list[Path]:
    root = source_root.resolve()
    approved = (PROJECT_ROOT / str(pilot_manifest["approved_real_source_root"])).resolve()
    if root != approved or not root.is_relative_to(WORKSPACE_ROOT):
        raise ValueError("真实试点来源目录必须等于清单批准的workspace目录")
    paths = sorted(path for path in root.rglob("*") if path.is_file())
    if not paths:
        raise ValueError("批准的真实试点目录没有源文件")
    forbidden = [path for path in paths if path.suffix.casefold() not in ALLOWED_SOURCE_SUFFIXES]
    if forbidden:
        raise ValueError("真实试点目录包含JSON、数据库或其他未批准输入格式")
    total = sum(path.stat().st_size for path in paths)
    if total > MAX_PILOT_SOURCE_BYTES:
        raise ValueError("真实试点资料总量超过本地受控验收上限")
    inventory_path = PROJECT_ROOT / "resources" / "pilots" / str(
        pilot_manifest["pilot_id"]
    ) / "source-inventory.csv"
    with inventory_path.open("r", encoding="utf-8-sig", newline="") as handle:
        expected_hashes = {
            str(row.get("sha256") or "").lower()
            for row in csv.DictReader(handle)
            if row.get("sha256")
        }
    actual_hashes = {_sha256(path) for path in paths}
    if expected_hashes != actual_hashes:
        raise ValueError("批准来源清单哈希与受控目录不一致")
    return paths


def _configure_pilot_limits(paths: list[Path]) -> None:
    maximum = max(path.stat().st_size for path in paths)
    total = sum(path.stat().st_size for path in paths)
    os.environ["QRA_MAX_UPLOAD_FILE_BYTES"] = str(maximum + 1024)
    os.environ["QRA_MAX_UPLOAD_TOTAL_BYTES"] = str(total + 1024)
    os.environ["QRA_MAX_ARCHIVE_MEMBER_BYTES"] = str(maximum + 1024)


def _file_payloads(paths: list[Path]) -> list[dict[str, object]]:
    return [
        {
            "file_name": path.name,
            "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "content": path.read_bytes(),
        }
        for path in paths
    ]


def _accept_every_candidate(service: object, session_id: str, reviewer: str) -> None:
    items = service.list_items(session_id, limit=500)["items"]
    if not items:
        raise RuntimeError("M1.NO_CANDIDATE")
    for item in items:
        detail = service.get_item(session_id, str(item["review_item_key"]))
        candidates = detail.get("candidates") or []
        if not candidates:
            raise RuntimeError("M1.NO_CANDIDATE")
        candidate = candidates[0]
        evidence_rows = candidate.get("evidence") or []
        if not evidence_rows:
            raise RuntimeError("M1.NO_EVIDENCE")
        evidence = evidence_rows[0]
        service.save_decision(
            session_id,
            review_item_key=str(detail["review_item_key"]),
            action="ACCEPT_CANDIDATE",
            selected_candidate_id=str(candidate["candidate_id"]),
            override_value=None,
            override_unit=None,
            reason="内部功能验收逐项核对候选与来源证据",
            actor=reviewer,
            expected_revision=int(service.get_session(session_id)["revision"]),
            source_id=str(evidence.get("location", {}).get("file_id") or "") or None,
            evidence_id=str(evidence["evidence_id"]),
        )


def _run_new_chain(database: object, paths: list[Path], args: argparse.Namespace, runtime: Path):
    from db_qra.conversion_adapter import run_conversion_job, submit_conversion
    from db_qra.engine_adapter import execute_run
    from db_qra.review_service import ReviewService

    reviewer = args.reviewer or "stage4-engineering-reviewer"
    job_id, created = submit_conversion(
        database,
        profile="jiangxi-natural-gas.jiujiang.v1",
        files=_file_payloads(paths),
        case_id=f"ROADMAP-STAGE4-{args.mode.upper()}",
        project_name="路线图第四阶段原始资料到筛查报告闭环",
        actor=reviewer,
        failure_policy="QUARANTINE_AND_CONTINUE" if args.mode == "pilot" else "ALL_OR_NOTHING",
        external_sharing_allowed=False,
    )
    if not created:
        raise RuntimeError("M1.ACCEPTANCE_OUTPUT_REUSED")
    run_conversion_job(database, job_id, runtime_root=runtime / "conversion")
    service = ReviewService(database)
    session, _ = service.create_or_resume_session(job_id, actor=reviewer, owner=reviewer)
    session_id = str(session["id"])
    _accept_every_candidate(service, session_id, reviewer)
    gate = service.run_gate(session_id, actor=reviewer)
    if gate["status"] != "PASS":
        raise RuntimeError("M1.GATE_NOT_PASS")
    current = service.get_session(session_id)
    confirmation = service.confirm(
        session_id,
        snapshot_name="路线图第四阶段内部验收快照",
        reviewer=reviewer,
        reason="内部功能验收，非正式业务签批",
        expected_revision=int(current["revision"]),
        expected_candidate_set_hash=str(current["candidate_set_hash"]),
        expected_decision_set_hash=str(current["decision_set_hash"]),
        run_after_confirm=True,
        generate_charts=True,
    )
    run_id = str(confirmation["run_id"])
    execute_run(
        database,
        run_id,
        str(confirmation["snapshot_id"]),
        targets=confirmation["targets"],
        generate_charts=True,
        runtime_root=runtime / "calculation",
    )
    return job_id, session_id, run_id


def _browser_evidence(
    args: argparse.Namespace, expected_ids: tuple[str, str, str]
) -> dict[str, object]:
    if args.browser_evidence is None:
        return {"status": "PENDING", "screenshot_hashes": [], "checks": []}
    value = json.loads(args.browser_evidence.resolve().read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("status") != "PASS":
        raise ValueError("浏览器验收证据未通过")
    actual_ids = tuple(
        value.get(key)
        for key in ("conversion_job_id", "review_session_id", "calculation_run_id")
    )
    if actual_ids != expected_ids:
        raise ValueError("浏览器验收证据与当前闭环ID不一致")
    hashes = value.get("screenshot_hashes")
    if not isinstance(hashes, list) or not hashes or not all(
        isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item) for item in hashes
    ):
        raise ValueError("浏览器验收证据缺少脱敏截图哈希")
    return {
        "status": "PASS",
        "screenshot_hashes": hashes,
        "checks": list(value.get("checks") or []),
    }


def _determinism(database: object, job_id: str, session_id: str, run_id: str, runtime: Path):
    from db_qra.engine_adapter import execute_run
    from db_qra.review_service import ReviewService

    first_session = ReviewService(database).get_session(session_id)
    first_run = database.get_run(run_id)
    service = ReviewService(database)
    repeated, _ = service.create_or_resume_session(
        job_id,
        actor="stage4-determinism-reviewer",
        owner="stage4-determinism-reviewer",
        target_node_ids=list(first_session["target_node_ids"]),
    )
    repeated_id = str(repeated["id"])
    with database.session() as connection:
        decision_rows = connection.execute(
            """SELECT decision.* FROM review_decision decision
               WHERE decision.session_id = ? AND NOT EXISTS (
                   SELECT 1 FROM review_decision newer
                   WHERE newer.supersedes_decision_id = decision.id
               ) ORDER BY decision.review_item_key""",
            (session_id,),
        ).fetchall()
    for row in decision_rows:
        detail = service.get_item(repeated_id, str(row["review_item_key"]))
        candidate_id = row["selected_candidate_id"]
        evidence = None
        if candidate_id:
            candidate = next(
                value for value in detail["candidates"] if value["candidate_id"] == candidate_id
            )
            evidence = (candidate.get("evidence") or [None])[0]
        service.save_decision(
            repeated_id,
            review_item_key=str(row["review_item_key"]),
            action=str(row["action"]),
            selected_candidate_id=str(candidate_id) if candidate_id else None,
            override_value=(
                json.loads(str(row["override_raw_value_json"]))
                if row["override_raw_value_json"] is not None
                else None
            ),
            override_unit=row["override_unit"],
            reason=str(row["reason"]),
            actor="stage4-determinism-reviewer",
            expected_revision=int(service.get_session(repeated_id)["revision"]),
            source_id=(str(evidence.get("location", {}).get("file_id")) if evidence else None),
            evidence_id=(str(evidence["evidence_id"]) if evidence else None),
        )
    gate = service.run_gate(repeated_id, actor="stage4-determinism-reviewer")
    if gate["status"] != "PASS":
        return "FAILED", None
    current = service.get_session(repeated_id)
    confirmation = service.confirm(
        repeated_id,
        snapshot_name="路线图第四阶段确定性复核快照",
        reviewer="stage4-determinism-reviewer",
        reason="内部功能验收，非正式业务签批",
        expected_revision=int(current["revision"]),
        expected_candidate_set_hash=str(current["candidate_set_hash"]),
        expected_decision_set_hash=str(current["decision_set_hash"]),
        run_after_confirm=False,
        generate_charts=True,
    )
    if confirmation["snapshot_id"] != first_session["confirmed_snapshot_id"]:
        return "FAILED", None
    repeat_run_id = database.create_run(
        str(confirmation["snapshot_id"]),
        str(first_run["input_sha256"]),
        targets=list(first_session["target_node_ids"]),
        generate_charts=True,
    )
    execute_run(
        database,
        repeat_run_id,
        str(confirmation["snapshot_id"]),
        targets=list(first_session["target_node_ids"]),
        generate_charts=True,
        runtime_root=runtime / "determinism-calculation",
    )
    repeated_run = database.get_run(repeat_run_id)
    stable = (
        current["candidate_set_hash"] == first_session["candidate_set_hash"]
        and current["decision_set_hash"] == first_session["decision_set_hash"]
        and repeated_run["result_sha256"] == first_run["result_sha256"]
    )
    return ("PASS" if stable else "FAILED"), repeat_run_id


def _redaction_guard(record: dict[str, object]) -> None:
    def strings(value: object):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for nested in value.values():
                yield from strings(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from strings(nested)

    text = "\n".join(strings(record))
    forbidden = {
        "ABSOLUTE_PATH": r"[A-Za-z]:[\\/]",
        "AUTHORIZATION": r"Authorization",
        "BEARER_TOKEN": r"Bearer\s+[A-Za-z0-9._-]+",
        "SECRET_FIELD": r"(?:api[_-]?key|secret)[\"']?\s*[:=]",
        "LARGE_BASE64": r"[A-Za-z0-9+/]{500,}={0,2}",
    }
    for label, pattern in forbidden.items():
        if re.search(pattern, text, flags=re.IGNORECASE):
            raise ValueError(f"M1.ACCEPTANCE_RECORD_NOT_REDACTED:{label}")


def main() -> int:
    args = parse_args()
    if args.mode == "pilot" and not args.authorized:
        raise SystemExit("pilot模式必须显式提供--authorized")
    if args.allow_external_models:
        raise SystemExit("当前试点清单禁止真实资料外发，不能启用公网模型")

    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from db_qra.database import SCHEMA_VERSION, QraDatabase
    from db_qra.pilot_registry import load_pilot_manifest
    from db_qra.roadmap_stage4 import MILESTONE, build_m1_summary
    from qra_converter.service import CONVERTER_VERSION
    from qra_engine import ENGINE_VERSION

    pilot = load_pilot_manifest(args.pilot_id)
    if args.mode == "pilot":
        if args.source_root is None or not args.reviewer:
            raise SystemExit("pilot模式必须提供--source-root和--reviewer")
        source_paths = _pilot_source_paths(args.source_root, pilot)
        _configure_pilot_limits(source_paths)
    else:
        if args.source_root is not None or args.database is not None:
            raise SystemExit("engineering模式使用仓库内脱敏fixture，不接受外部来源或现有数据库")
        source_paths = [ENGINEERING_SOURCE]

    manifest_rows, source_manifest_sha256 = _source_manifest(source_paths)
    run_root = WORKSPACE_ROOT / "runtime" / "roadmap-stage4" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    run_root.mkdir(parents=True, exist_ok=False)

    if args.database is not None:
        if not args.database.resolve().is_relative_to(WORKSPACE_ROOT):
            raise SystemExit("现有验收数据库必须位于workspace范围")
        if not all((args.conversion_id, args.review_session_id, args.run_id)):
            raise SystemExit("检查现有数据库必须提供conversion/review/run三个ID")
        database = QraDatabase(args.database.resolve())
        job_id, session_id, run_id = (
            str(args.conversion_id),
            str(args.review_session_id),
            str(args.run_id),
        )
    else:
        database = QraDatabase(run_root / "qra.sqlite3")
        job_id, session_id, run_id = _run_new_chain(
            database, source_paths, args, run_root
        )

    summary = build_m1_summary(
        database,
        job_id,
        review_session_id=session_id,
        calculation_run_id=run_id,
    )
    determinism_status, repeat_run_id = _determinism(
        database, job_id, session_id, run_id, run_root
    )
    browser = _browser_evidence(args, (job_id, session_id, run_id))
    with database.session() as connection:
        model_audit_counts = {
            str(row["status"]): int(row["count"])
            for row in connection.execute(
                "SELECT status, count(*) AS count FROM model_call_audit "
                "WHERE job_id = ? GROUP BY status",
                (job_id,),
            ).fetchall()
        }
        dispatched_model_calls = int(
            connection.execute(
                "SELECT count(*) FROM model_call_audit WHERE job_id = ? "
                "AND provider_request_id IS NOT NULL AND trim(provider_request_id) <> ''",
                (job_id,),
            ).fetchone()[0]
        )
        dispatched_model_failures = int(
            connection.execute(
                "SELECT count(*) FROM model_call_audit WHERE job_id = ? "
                "AND provider_request_id IS NOT NULL AND trim(provider_request_id) <> '' "
                "AND status <> 'SUCCEEDED'",
                (job_id,),
            ).fetchone()[0]
        )
        prevented_model_calls = int(
            connection.execute(
                "SELECT count(*) FROM model_call_audit WHERE job_id = ? "
                "AND error_code IN ('PARSE.OCR_PROVIDER_NOT_CONFIGURED', "
                "'PARSE.EXTERNAL_SHARING_NOT_ALLOWED')",
                (job_id,),
            ).fetchone()[0]
        )
        database_source_hashes = sorted(
            str(row[0])
            for row in connection.execute(
                "SELECT sha256 FROM conversion_source WHERE job_id = ? "
                "AND archive_member_path IS NULL ORDER BY sha256",
                (job_id,),
            ).fetchall()
        )
    expected_source_hashes = sorted(str(row["sha256"]) for row in manifest_rows)
    source_chain_status = (
        "PASS" if database_source_hashes == expected_source_hashes else "FAILED"
    )
    summary_checks = list(summary["checks"])
    if source_chain_status != "PASS":
        summary_checks.append({"code": "M1.SOURCE_MANIFEST_MISMATCH", "status": "FAILED"})
    technical_pass = (
        summary["status"] == "PASS"
        and determinism_status == "PASS"
        and source_chain_status == "PASS"
        and dispatched_model_calls == 0
    )
    if args.mode == "engineering":
        final_status = "ENGINEERING_PASS" if technical_pass else "FAILED"
    elif technical_pass and browser["status"] == "PASS":
        final_status = "M1_INTERNAL_PILOT_PASS"
    elif technical_pass:
        final_status = "BLOCKED"
    else:
        final_status = "FAILED"
    record = {
        "schema_version": "qra.roadmap-stage4-acceptance/1.0.0",
        "milestone": MILESTONE,
        "acceptance_mode": args.mode,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "software_versions": {
            "database_schema": SCHEMA_VERSION,
            "converter": CONVERTER_VERSION,
            "engine": ENGINE_VERSION,
        },
        "pilot_id": args.pilot_id,
        "authorization_scope": (
            "APPROVED_INTERNAL_PILOT_NO_EXTERNAL_SHARING"
            if args.mode == "pilot"
            else "SANITIZED_ENGINEERING_FIXTURE"
        ),
        "source_count": len(manifest_rows),
        "source_manifest_sha256": source_manifest_sha256,
        "source_chain_status": source_chain_status,
        "conversion_job_id": job_id,
        "conversion_status": summary["conversion_status"],
        "parse_artifact_count": summary["parse_artifact_count"],
        "extracted_entity_count": summary["extracted_entity_count"],
        "candidate_field_count": summary["candidate_field_count"],
        "candidate_evidence_link_count": summary["candidate_evidence_link_count"],
        "quality_issue_counts": summary["quality_issue_counts"],
        "review_session_id": session_id,
        "review_status": summary["review_status"],
        "review_decision_count": summary["review_decision_count"],
        "gate_status": summary["gate_status"],
        "gate_run_id": summary["gate_run_id"],
        "candidate_set_hash": summary["candidate_set_hash"],
        "decision_set_hash": summary["decision_set_hash"],
        "snapshot_id": summary["snapshot_id"],
        "payload_sha256": summary["snapshot_payload_sha256"],
        "input_snapshot_provenance_count": summary["provenance_count"],
        "input_snapshot_review_provenance_count": summary["review_provenance_count"],
        "calculation_run_id": run_id,
        "calculation_status": summary["calculation_status"],
        "completed_target_nodes": summary["completed_target_nodes"],
        "skipped_nodes_with_reasons": summary["skipped_nodes_with_reasons"],
        "failed_nodes": summary["failed_nodes"],
        "segment_result_count": summary["segment_result_count"],
        "report_artifacts": summary["report_artifacts"],
        "reverse_trace_status": summary["reverse_trace_status"],
        "determinism_status": determinism_status,
        "determinism_repeat_run_id": repeat_run_id,
        "browser_acceptance_status": browser["status"],
        "browser_evidence": browser,
        "model_calls": {
            "external_call_count": dispatched_model_calls,
            "failure_count": dispatched_model_failures,
            "audit_failure_count": sum(
                count
                for status, count in model_audit_counts.items()
                if status not in {"SUCCEEDED"}
            ),
            "audit_record_count": sum(model_audit_counts.values()),
            "prevented_before_dispatch_count": prevented_model_calls,
            "by_status": model_audit_counts,
        },
        "checks": summary_checks,
        "remaining_business_approvals": [
            "FORMAL_BUSINESS_SIGNOFF",
            "COMPLETE_11_OF_11_QRA_DATA_AND_EXPERT_APPROVAL",
        ] + (["REAL_BROWSER_ACCEPTANCE"] if browser["status"] != "PASS" else []),
        "final_status": final_status,
    }
    _redaction_guard(record)
    record_path = (args.record or (run_root / "stage4-e2e-acceptance.json")).resolve()
    if record_path.exists():
        raise SystemExit("验收记录已存在，拒绝混入旧结果")
    if not record_path.parent.is_relative_to(PROJECT_ROOT.resolve()):
        raise SystemExit("验收记录必须位于项目受控目录")
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.json:
        print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    else:
        print(final_status)
    return 0 if final_status in {"ENGINEERING_PASS", "M1_INTERNAL_PILOT_PASS"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
