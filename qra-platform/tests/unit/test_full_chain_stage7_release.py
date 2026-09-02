from __future__ import annotations

import tempfile
from pathlib import Path

from db_qra.backup import backup_database, inspect_database, restore_database
from db_qra.cli import build_parser
from db_qra.database import QraDatabase
from db_qra.demo_release import DEMO_RELEASE_NAME, DEMO_RELEASE_VERSION

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_stage7_release_identity_and_cli_contract() -> None:
    assert DEMO_RELEASE_NAME == "QRA全合成端到端演示版_v1"
    assert DEMO_RELEASE_VERSION == "1.0.0"
    parser = build_parser()
    assert parser.parse_args(["load-demo"]).command == "load-demo"
    assert parser.parse_args(["backup", "--output", "backup.sqlite3"]).command == "backup"
    assert parser.parse_args(["restore", "--input", "backup.sqlite3"]).command == "restore"


def test_backup_restore_round_trip_is_checked_and_non_overwriting() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        database = QraDatabase(root / "source.sqlite3")
        database.initialize()
        project = database.create_project(
            name="阶段7备份测试",
            case_id="STAGE7-BACKUP",
            data_classification="SYNTHETIC_TEST_ONLY",
            is_demo=True,
        )
        backup = backup_database(database, root / "backup.sqlite3")
        restored = restore_database(root / "backup.sqlite3", root / "restored.sqlite3")
        assert backup["quick_check"] == "ok"
        assert restored["quick_check"] == "ok"
        restored_database = QraDatabase(root / "restored.sqlite3")
        assert restored_database.get_project(str(project["id"]))["name"] == "阶段7备份测试"
        assert inspect_database(root / "restored.sqlite3")["counts"]["business_project"] == 1


def test_one_click_scripts_are_bounded_to_a_local_runtime() -> None:
    installer = (PROJECT_ROOT / "install_full_synthetic_demo.ps1").read_text("utf-8")
    launcher = (PROJECT_ROOT / "start_full_synthetic_demo.ps1").read_text("utf-8")
    backup = (PROJECT_ROOT / "backup_full_synthetic_demo.ps1").read_text("utf-8")
    restore = (PROJECT_ROOT / "restore_full_synthetic_demo.ps1").read_text("utf-8")
    assert ".runtime\\venv" in installer
    assert "$venvCreated" in installer
    assert "Get-Command python" in installer
    assert "Test-Path -LiteralPath $venvPython" in installer
    assert "load-demo" in launcher and "serve" in launcher
    assert "QRA_PROJECT_ROOT" in launcher
    assert "QRA_WORKSPACE_ROOT" in launcher
    assert "'backup'" in backup
    assert "'restore'" in restore
    assert "$arguments += '--replace'" in backup
    assert "$arguments += '--replace'" in restore
    assert "[Alias('Input')]" in restore and "$BackupPath" in restore
    assert "[string]$Input" not in restore
    assert "--replace:$replace" not in (backup + restore).lower()
