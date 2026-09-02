"""Run and record the final full-synthetic end-to-end acceptance gate."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from build_full_synthetic_demo_release import build_release  # noqa: E402

from db_qra.backup import backup_database, restore_database  # noqa: E402
from db_qra.controlled_reporting import ControlledReportService  # noqa: E402
from db_qra.database import QraDatabase, bytes_sha256, json_sha256  # noqa: E402
from db_qra.engine_adapter import execute_run  # noqa: E402
from db_qra.stage3_adapter import persist_confirmed_snapshot  # noqa: E402
from db_qra.synthetic_stage4 import SyntheticStage4Workflow  # noqa: E402
from qra_converter.synthetic_stage3 import SyntheticStage3Workflow  # noqa: E402

GATE_NAME = "M1_5_FULL_SYNTHETIC_END_TO_END_ACCEPTED"
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "workspace" / "outputs" / "m1-5-stage7-end-to-end-20260901"
)
DEFAULT_RECORD = (
    PROJECT_ROOT
    / "resources"
    / "synthetic"
    / "full-chain-v1"
    / "stage7"
    / "stage7-acceptance.json"
)
DEFAULT_BROWSER_RECORD = DEFAULT_RECORD.parent / "stage7-browser-acceptance.json"
STAGE4_PROFILE = (
    PROJECT_ROOT
    / "resources"
    / "synthetic"
    / "full-chain-v1"
    / "stage4"
    / "acceptance-profile.json"
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _add_check(
    checks: list[dict[str, Any]],
    check_id: str,
    passed: bool,
    detail: Any,
) -> None:
    checks.append(
        {"check_id": check_id, "status": "PASS" if passed else "FAIL", "detail": detail}
    )


def _fresh_output(path: Path) -> Path:
    target = path.resolve()
    allowed_root = (PROJECT_ROOT / "workspace" / "outputs").resolve()
    if not target.is_relative_to(allowed_root):
        raise ValueError("阶段7输出目录必须位于workspace/outputs内")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    return target


def _attach_project(
    database: QraDatabase,
    *,
    name: str,
    case_id: str,
    snapshot_id: str,
    run_id: str,
    is_demo: bool,
) -> str:
    project = database.create_project(
        name=name,
        case_id=case_id,
        data_classification="SYNTHETIC_TEST_ONLY",
        is_demo=is_demo,
        actor="stage7-acceptance",
    )
    project_id = str(project["id"])
    database.attach_project_snapshot(project_id, snapshot_id)
    database.attach_project_run(project_id, run_id)
    return project_id


def _run_snapshot(
    database: QraDatabase,
    snapshot_id: str,
    runtime_root: Path,
) -> dict[str, Any]:
    metadata = database.snapshot_metadata(snapshot_id)
    run_id = database.create_run(
        snapshot_id,
        str(metadata["payload_sha256"]),
        generate_charts=True,
    )
    execute_run(
        database,
        run_id,
        snapshot_id,
        generate_charts=True,
        runtime_root=runtime_root,
    )
    return database.get_run(run_id)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.bind(("127.0.0.1", 0))
        return int(stream.getsockname()[1])


def _fresh_release_smoke(
    release_root: Path,
    runtime_root: Path,
) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(release_root / "src")
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["QRA_WORKSPACE_ROOT"] = str(runtime_root)
    database_path = runtime_root / "state" / "qra.sqlite3"
    load = subprocess.run(
        [
            sys.executable,
            "-m",
            "db_qra",
            "--database",
            str(database_path),
            "load-demo",
            "--runtime-root",
            str(runtime_root / "runtime"),
            "--actor",
            "stage7-fresh-release-smoke",
        ],
        cwd=release_root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )
    if load.returncode != 0:
        raise RuntimeError(f"全新发布目录加载失败：{load.stderr[-1000:]}")
    prepared = json.loads(load.stdout)
    port = _free_port()
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "db_qra",
            "--database",
            str(database_path),
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=release_root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        creationflags=creation_flags,
    )
    base = f"http://127.0.0.1:{port}"
    project_page = ""
    report_page = ""
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with urlopen(base + prepared["project_url"], timeout=3) as response:
                    project_page = response.read().decode("utf-8")
                break
            except OSError:
                time.sleep(0.25)
        if not project_page:
            raise RuntimeError("发布包本地服务未在30秒内就绪")
        with urlopen(base + prepared["report_url"], timeout=10) as response:
            report_page = response.read().decode("utf-8")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
    return {
        "prepared": prepared,
        "project_page_ok": "QRA 项目工作台" in project_page
        and "全合成演示数据" in project_page,
        "report_page_ok": "合成数据 · 仅供软件测试" in report_page,
        "database": str(database_path),
    }


def run_acceptance(
    *,
    output_root: Path,
    record_path: Path,
    browser_record_path: Path,
) -> dict[str, Any]:
    output = _fresh_output(output_root)
    database_path = output / "state" / "stage7.sqlite3"
    stage3 = SyntheticStage3Workflow(
        PROJECT_ROOT,
        snapshot_persister=persist_confirmed_snapshot,
    )
    profile = json.loads(STAGE4_PROFILE.read_text(encoding="utf-8"))

    scenario_a = stage3.run(
        condition_id="D00_CLEAN",
        confirm=True,
        output_dir=output / "scenarios" / "A_D00_CLEAN" / "conversion",
        database_path=database_path,
    )
    snapshot_a = str(scenario_a["snapshot_persistence"]["database_snapshot_id"])
    stage4 = SyntheticStage4Workflow(PROJECT_ROOT).run(
        stage3_snapshot_path=(
            output / "scenarios" / "A_D00_CLEAN" / "conversion" / "snapshot.json"
        ),
        database_path=database_path,
        output_root=output / "scenarios" / "A_D00_CLEAN" / "calculation",
        snapshot_id=snapshot_a,
    )
    database = QraDatabase(database_path)
    run_a = database.get_run(str(stage4["summary"]["calculation_run_id"]))
    project_a = _attach_project(
        database,
        name="阶段7场景A·完整正常资料",
        case_id="S00_BASELINE-D00_CLEAN",
        snapshot_id=snapshot_a,
        run_id=str(run_a["id"]),
        is_demo=True,
    )
    report_service = ControlledReportService(database)
    report_a_build = report_service.generate(project_a, actor="stage7-acceptance")
    report_a = report_service.confirm(
        str(report_a_build.report["id"]),
        reviewer="stage7-reviewer",
        reason="阶段7完整正常资料测试报告确认",
    )

    scenario_b_blocked = stage3.run(
        condition_id="D10_CONFLICT",
        output_dir=output / "scenarios" / "B_D10_CONFLICT" / "blocked",
    )
    scenario_b = stage3.run(
        condition_id="D10_CONFLICT",
        decisions={"pipeline.operating_pressure_mpa": "OVERLAY"},
        confirm=True,
        output_dir=output / "scenarios" / "B_D10_CONFLICT" / "resolved",
        database_path=database_path,
    )
    snapshot_b = str(scenario_b["snapshot_persistence"]["database_snapshot_id"])
    run_b = _run_snapshot(
        database,
        snapshot_b,
        output / "scenarios" / "B_D10_CONFLICT" / "runtime",
    )
    project_b = _attach_project(
        database,
        name="阶段7场景B·字段冲突已复核",
        case_id="S00_BASELINE-D10_CONFLICT",
        snapshot_id=snapshot_b,
        run_id=str(run_b["id"]),
        is_demo=False,
    )
    report_b = report_service.generate(project_b, actor="stage7-acceptance")
    decision_evidence = next(
        (
            row
            for row in report_b.context["evidence_index"]
            if row["evidence_id"] == "EVIDENCE.REVIEW_DECISION_SET"
        ),
        None,
    )

    scenario_c = stage3.run(
        condition_id="D20_MISSING",
        output_dir=output / "scenarios" / "C_D20_MISSING" / "conversion",
    )
    incomplete_screening = stage4["d20_report"]
    _write_json(
        output / "scenarios" / "C_D20_MISSING" / "incomplete-screening-report.json",
        incomplete_screening,
    )
    scenario_d = stage3.run(
        condition_id="D30_LOW_QUALITY_SCAN",
        output_dir=output / "scenarios" / "D_D30_LOW_QUALITY_SCAN",
    )
    scenario_e = stage3.run(
        condition_id="D40_OVERSIZED_IMAGE",
        output_dir=output / "scenarios" / "E_D40_OVERSIZED_IMAGE",
    )
    scenario_f = stage3.run(
        condition_id="D50_PROMPT_INJECTION",
        output_dir=output / "scenarios" / "F_D50_PROMPT_INJECTION",
    )
    scenario_g = stage3.run(
        condition_id="D00_CLEAN",
        provider_mode="online",
        output_dir=output / "scenarios" / "G_MODEL_UNAVAILABLE",
    )

    snapshot_a_hash = str(database.snapshot_metadata(snapshot_a)["payload_sha256"])
    result_a_hash = str(run_a["result_sha256"])
    report_a_hashes = {
        format_name: bytes_sha256(
            database.get_controlled_report_artifact(str(report_a["id"]), format_name)[1]
        )
        for format_name in ("html", "pdf", "docx")
    }
    interrupted_id = database.create_run(snapshot_a, snapshot_a_hash, generate_charts=False)
    database.set_run_running(interrupted_id, "interrupted-stage7-fixture")
    restarted = QraDatabase(database_path)
    recovered_ids = restarted.requeue_interrupted_runs()
    execute_run(
        restarted,
        interrupted_id,
        snapshot_a,
        generate_charts=False,
        runtime_root=output / "scenarios" / "H_SERVICE_RESTART" / "runtime",
    )
    recovered_run = restarted.get_run(interrupted_id)
    restart_invariants = {
        "recovered": interrupted_id in recovered_ids,
        "snapshot_unchanged": (
            restarted.snapshot_metadata(snapshot_a)["payload_sha256"] == snapshot_a_hash
        ),
        "result_unchanged": recovered_run["result_sha256"] == result_a_hash,
        "report_resources_unchanged": all(
            bytes_sha256(
                restarted.get_controlled_report_artifact(
                    str(report_a["id"]), format_name
                )[1]
            )
            == expected
            for format_name, expected in report_a_hashes.items()
        ),
    }

    backup_path = output / "backup" / "stage7-demo.sqlite3"
    backup_result = backup_database(restarted, backup_path)
    restored_path = output / "restored" / "stage7-demo.sqlite3"
    restore_result = restore_database(backup_path, restored_path)
    restored = QraDatabase(restored_path)
    restored_report = restored.get_controlled_report(str(report_a["id"]))
    backup_restore_ok = (
        restored.snapshot_metadata(snapshot_a)["payload_sha256"] == snapshot_a_hash
        and restored.get_run(str(run_a["id"]))["result_sha256"] == result_a_hash
        and restored_report["context_sha256"] == report_a["context_sha256"]
    )

    tamper_build = report_service.generate(project_a, actor="stage7-tamper-fixture")
    tampered_draft = copy.deepcopy(tamper_build.draft)
    tampered_draft["sections"][0]["paragraphs"][0]["text_template"] += " 篡改值999。"
    tamper_validation = report_service.validate_confirmation_candidate(
        str(tamper_build.report["id"]),
        tampered_draft,
        actor="stage7-tamper-reviewer",
    )
    tamper_stored = database.get_controlled_report(str(tamper_build.report["id"]))
    tamper_audit = next(
        (
            event
            for event in database.list_audit_events(limit=500)
            if event["event_type"] == "CONTROLLED_REPORT_CONFIRMATION_REJECTED"
            and event["entity_id"] == tamper_build.report["id"]
        ),
        None,
    )

    traceability = {
        "schema_version": "qra-stage7-traceability-ledger/1.0.0",
        "raw_sources": scenario_a["provenance"]["source_manifest"],
        "candidate_ids": scenario_a["provenance"]["candidate_ids"],
        "evidence_ids": scenario_a["provenance"]["evidence_ids"],
        "decision_set_sha256": scenario_a["review_workbench"]["decision_set_sha256"],
        "snapshot": {"id": snapshot_a, "sha256": snapshot_a_hash},
        "calculation": {
            "id": run_a["id"],
            "result_sha256": result_a_hash,
            "node_result_refs": [
                row["result_ref"] for row in report_a_build.context["result_index"]
            ],
        },
        "report": {
            "id": report_a["id"],
            "context_sha256": report_a["context_sha256"],
            "draft_sha256": report_a["draft_sha256"],
            "artifact_sha256": report_a_hashes,
        },
    }
    traceability["ledger_sha256"] = json_sha256(traceability)
    _write_json(output / "traceability-ledger-v1.json", traceability)

    acceptance_release = (
        PROJECT_ROOT / "workspace" / "releases" / "QRA全合成端到端演示版_v1_acceptance"
    )
    release_result = build_release(acceptance_release)
    fresh_smoke = _fresh_release_smoke(
        acceptance_release,
        output / "fresh-release-runtime",
    )
    browser_record = (
        json.loads(browser_record_path.read_text(encoding="utf-8"))
        if browser_record_path.is_file()
        else {"status": "MISSING"}
    )

    checks: list[dict[str, Any]] = []
    _add_check(
        checks,
        "S7-A-RAW_TO_GATE",
        len(scenario_a["provenance"]["source_manifest"]) == 10
        and len(scenario_a["candidates"]) == 256
        and len(scenario_a["evidence"]) == 256
        and scenario_a["gate"]["status"] == "PASS",
        {
            "source_count": len(scenario_a["provenance"]["source_manifest"]),
            "candidate_count": len(scenario_a["candidates"]),
            "evidence_count": len(scenario_a["evidence"]),
            "gate": scenario_a["gate"]["status"],
        },
    )
    _add_check(
        checks,
        "S7-A-SNAPSHOT_EQUALS_GOLDEN",
        scenario_a["golden_diff"]["equal"],
        scenario_a["golden_diff"],
    )
    _add_check(
        checks,
        "S7-A-11_OF_11_EQUALS_NUMERIC_BASELINE",
        run_a["summary"]["completed_node_count"] == 11
        and run_a["result_sha256"] == profile["expected_numerical_result_sha256"],
        {
            "completed": run_a["summary"]["completed_node_count"],
            "result_sha256": run_a["result_sha256"],
        },
    )
    _add_check(
        checks,
        "S7-A-CONTROLLED_REPORT_FORMAL_GATE_CLOSED",
        report_a["status"] == "CONFIRMED_TEST_ONLY"
        and not report_a_build.context["formal_report_allowed"],
        {"report_id": report_a["id"], "status": report_a["status"]},
    )
    _add_check(
        checks,
        "S7-B-CONFLICT_REQUIRES_HUMAN_DECISION",
        scenario_b_blocked["gate"]["status"] == "BLOCKED"
        and scenario_b_blocked["snapshot"] is None,
        scenario_b_blocked["gate"],
    )
    _add_check(
        checks,
        "S7-B-DECISION_HASH_AND_11_OF_11",
        scenario_b["gate"]["status"] == "PASS"
        and scenario_b["review_workbench"]["decision_set_sha256"]
        != scenario_b_blocked["review_workbench"]["decision_set_sha256"]
        and run_b["summary"]["completed_node_count"] == 11,
        {
            "decision_set_sha256": scenario_b["review_workbench"][
                "decision_set_sha256"
            ],
            "completed": run_b["summary"]["completed_node_count"],
        },
    )
    _add_check(
        checks,
        "S7-B-REPORT_RECORDS_REVIEW_DECISION",
        decision_evidence is not None
        and decision_evidence["sha256"]
        == scenario_b["review_workbench"]["decision_set_sha256"],
        decision_evidence,
    )
    _add_check(
        checks,
        "S7-C-MISSING_NOT_ZERO_AND_INCOMPLETE_SCREENING",
        scenario_c["gate"]["status"] == "BLOCKED"
        and scenario_c["coverage_report"]["blank_to_zero_count"] == 0
        and bool(scenario_c["capability"]["blocked_node_ids"])
        and incomplete_screening["blocked_nodes_not_completed"]
        and bool(incomplete_screening["fill_data_list"])
        and not incomplete_screening["formal_report_allowed"],
        {
            "blocked_nodes": scenario_c["capability"]["blocked_node_ids"],
            "fill_data_count": len(incomplete_screening["fill_data_list"]),
        },
    )
    unaffected_documents = [
        row
        for row in scenario_d["parsed_artifacts"]["documents"]
        if row["source_document"] != "09_现场检查扫描件.pdf"
    ]
    _add_check(
        checks,
        "S7-D-LOW_QUALITY_SCAN_REVIEW_AND_ISOLATION",
        scenario_d["gate"]["status"] == "BLOCKED"
        and len(scenario_d["parsed_artifacts"]["pdf_preprocessing"]) == 42
        and all(
            row["original_bbox_normalized"]
            for row in scenario_d["parsed_artifacts"]["pdf_preprocessing"]
        )
        and len(unaffected_documents) == 9
        and all(row["status"] == "PARSED" for row in unaffected_documents),
        {
            "low_confidence_fields": scenario_d["gate"]["unresolved_review_count"],
            "unaffected_parsed_documents": len(unaffected_documents),
        },
    )
    tiles = scenario_e["parsed_artifacts"]["image_tiling"]
    _add_check(
        checks,
        "S7-E-OVERSIZED_IMAGE_BOUNDED_AND_REVERSIBLE",
        scenario_e["gate"]["status"] == "PASS"
        and len(tiles) == 8
        and all(row["scaled_request_within_model_limit"] for row in tiles)
        and all(row["original_bbox_pixels"] for row in tiles)
        and all(row["reextract_scope"] == ["page", "field"] for row in tiles),
        {"tile_count": len(tiles), "last_original_bbox": tiles[-1]["original_bbox_pixels"]},
    )
    security = scenario_f["security_audit"]
    _add_check(
        checks,
        "S7-F-PROMPT_INJECTION_CONTAINED_AND_AUDITED",
        scenario_f["gate"]["status"] == "PASS"
        and bool(security["detected_evidence_ids"])
        and not security["document_commands_trusted"]
        and not security["workflow_changed"]
        and not security["contract_changed"]
        and not security["gate_changed"]
        and security["candidate_count_from_injection"] == 0,
        security,
    )
    online = scenario_g["online_demo"]
    _add_check(
        checks,
        "S7-G-MODEL_UNAVAILABLE_PRESERVES_DETERMINISTIC_WORK",
        scenario_g["gate"]["status"] == "BLOCKED"
        and online["parsed_artifacts_preserved"]
        and online["human_review_route_available"]
        and not online["external_call_made"]
        and bool(scenario_g["candidates"]),
        online,
    )
    _add_check(
        checks,
        "S7-H-SERVICE_RESTART_RECOVERS_WITHOUT_MUTATION",
        all(restart_invariants.values()),
        restart_invariants,
    )
    _add_check(
        checks,
        "S7-BACKUP-RESTORE_VERIFIED",
        backup_restore_ok
        and backup_result["quick_check"] == "ok"
        and restore_result["quick_check"] == "ok",
        {"backup": backup_result, "restore": restore_result},
    )
    _add_check(
        checks,
        "S7-I-REPORT_TAMPER_REJECTED_AND_AUDITED",
        tamper_validation["status"] == "FAIL"
        and tamper_stored["status"] == "DRAFT"
        and tamper_audit is not None,
        {
            "validation_status": tamper_validation["status"],
            "stored_status": tamper_stored["status"],
            "audit_event_id": (tamper_audit or {}).get("id"),
        },
    )
    _add_check(
        checks,
        "S7-BIDIRECTIONAL-TRACEABILITY",
        len(traceability["raw_sources"]) == 10
        and len(traceability["candidate_ids"]) == 256
        and len(traceability["evidence_ids"]) == 256
        and len(traceability["calculation"]["node_result_refs"]) == 11
        and bool(traceability["report"]["artifact_sha256"]),
        {"ledger_sha256": traceability["ledger_sha256"]},
    )
    _add_check(
        checks,
        "S7-FRESH-RELEASE-DIRECTORY-STARTS-WITHOUT-JSON-OR-DB-EDIT",
        release_result["status"] == "PASS"
        and fresh_smoke["prepared"]["completed_node_count"] == 11
        and bool(fresh_smoke["prepared"]["report_id"])
        and fresh_smoke["project_page_ok"]
        and fresh_smoke["report_page_ok"],
        {"release": release_result, "smoke": fresh_smoke},
    )
    _add_check(
        checks,
        "S7-REAL-BROWSER-ORDINARY-USER-FLOW",
        browser_record.get("status") == "PASS"
        and browser_record.get("console_error_count") == 0
        and browser_record.get("formal_report_allowed") is False,
        browser_record,
    )
    _add_check(
        checks,
        "S7-SYNTHETIC-AND-FORMAL-BOUNDARY",
        all(
            not value
            for value in (
                report_a_build.context["formal_report_allowed"],
                incomplete_screening["formal_report_allowed"],
                release_result["formal_report_allowed"],
            )
        ),
        {"formal_report_allowed": False},
    )

    passed = all(row["status"] == "PASS" for row in checks)
    record = {
        "schema_version": "qra-stage7-acceptance/1.0.0",
        "stage": 7,
        "gate": GATE_NAME,
        "status": "PASS" if passed else "FAIL",
        "checked_at": "2026-09-01T00:00:00+08:00",
        "check_count": len(checks),
        "passed_count": sum(row["status"] == "PASS" for row in checks),
        "checks": checks,
        "scenario_ids": [
            "A_D00_CLEAN",
            "B_D10_CONFLICT",
            "C_D20_MISSING",
            "D_D30_LOW_QUALITY_SCAN",
            "E_D40_OVERSIZED_IMAGE",
            "F_D50_PROMPT_INJECTION",
            "G_MODEL_UNAVAILABLE",
            "H_SERVICE_RESTART",
            "I_REPORT_TAMPER",
        ],
        "project_id": project_a,
        "snapshot_id": snapshot_a,
        "calculation_run_id": run_a["id"],
        "controlled_report_id": report_a["id"],
        "completed_node_count": run_a["summary"]["completed_node_count"],
        "numerical_result_sha256": result_a_hash,
        "expected_numerical_result_sha256": profile["expected_numerical_result_sha256"],
        "traceability_ledger_sha256": traceability["ledger_sha256"],
        "formal_report_allowed": False,
        "output_root": str(output),
        "release_acceptance_root": str(acceptance_release),
    }
    _write_json(output / "stage7-acceptance.json", record)
    _write_json(record_path, record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--record", type=Path, default=DEFAULT_RECORD)
    parser.add_argument("--browser-record", type=Path, default=DEFAULT_BROWSER_RECORD)
    args = parser.parse_args()
    record = run_acceptance(
        output_root=args.output_root,
        record_path=args.record,
        browser_record_path=args.browser_record,
    )
    print(
        json.dumps(
            {
                "gate": record["gate"],
                "status": record["status"],
                "passed": f"{record['passed_count']}/{record['check_count']}",
                "output_root": record["output_root"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
