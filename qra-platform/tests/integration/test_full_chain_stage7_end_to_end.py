from __future__ import annotations

import copy
import tempfile
from pathlib import Path

from db_qra.backup import backup_database, restore_database
from db_qra.controlled_reporting import ControlledReportService
from db_qra.database import QraDatabase
from db_qra.demo_release import prepare_full_synthetic_demo


def test_demo_preparation_is_complete_idempotent_and_restore_safe() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        database = QraDatabase(root / "state" / "qra.sqlite3")
        first = prepare_full_synthetic_demo(
            database,
            runtime_root=root / "runtime",
            actor="stage7-test",
        )
        second = prepare_full_synthetic_demo(
            database,
            runtime_root=root / "runtime",
            actor="stage7-test-replay",
        )
        assert first["status"] == "PASS"
        assert first["completed_node_count"] == 11
        assert first["formal_report_allowed"] is False
        assert first["project_id"] == second["project_id"]
        assert first["snapshot_id"] == second["snapshot_id"]
        assert first["run_id"] == second["run_id"]
        assert first["report_id"] == second["report_id"]

        backup_database(database, root / "backup" / "demo.sqlite3")
        restore_database(
            root / "backup" / "demo.sqlite3",
            root / "restored" / "qra.sqlite3",
        )
        restored = QraDatabase(root / "restored" / "qra.sqlite3")
        assert restored.snapshot_metadata(first["snapshot_id"])["payload_sha256"] == first[
            "snapshot_sha256"
        ]
        assert restored.get_run(first["run_id"])["result_sha256"] == first["result_sha256"]
        assert restored.get_controlled_report(first["report_id"])["status"] == "DRAFT"


def test_report_tamper_candidate_remains_draft_and_is_audited() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        database = QraDatabase(root / "qra.sqlite3")
        prepared = prepare_full_synthetic_demo(
            database,
            runtime_root=root / "runtime",
            actor="stage7-tamper-test",
        )
        service = ControlledReportService(database)
        stored = database.get_controlled_report(prepared["report_id"])
        tampered = copy.deepcopy(stored["draft"])
        tampered["sections"][0]["paragraphs"][0]["text_template"] += " 篡改值999。"
        validation = service.validate_confirmation_candidate(
            prepared["report_id"],
            tampered,
            actor="stage7-tamper-reviewer",
        )
        assert validation["status"] == "FAIL"
        assert database.get_controlled_report(prepared["report_id"])["status"] == "DRAFT"
        audits = database.list_audit_events(limit=100)
        assert any(
            event["event_type"] == "CONTROLLED_REPORT_CONFIRMATION_REJECTED"
            and event["entity_id"] == prepared["report_id"]
            for event in audits
        )

