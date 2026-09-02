"""Safe SQLite backup and restore helpers for the packaged synthetic demo."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .database import QraDatabase


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_database(path: Path) -> dict[str, Any]:
    source = path.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"数据库文件不存在：{source}")
    connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if quick_check != "ok":
            raise ValueError(f"SQLite完整性检查失败：{quick_check}")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "db_schema" not in tables:
            raise ValueError("备份缺少QRA db_schema表")
        versions = [
            str(row[0])
            for row in connection.execute(
                "SELECT version FROM db_schema ORDER BY applied_at, version"
            ).fetchall()
        ]
        counts = {}
        for table in (
            "input_snapshot",
            "calculation_run",
            "business_project",
            "controlled_report",
            "audit_event",
        ):
            counts[table] = (
                int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
                if table in tables
                else 0
            )
    finally:
        connection.close()
    return {
        "path": str(source),
        "byte_count": source.stat().st_size,
        "sha256": _file_sha256(source),
        "quick_check": quick_check,
        "schema_versions": versions,
        "counts": counts,
    }


def backup_database(
    database: QraDatabase,
    output_path: Path,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    database.initialize()
    source = database.path.resolve()
    target = output_path.resolve()
    if source == target:
        raise ValueError("备份目标不能与当前数据库相同")
    if target.exists() and not replace:
        raise FileExistsError(f"备份文件已存在；如需覆盖请显式使用replace：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(temporary)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()
    inspect_database(temporary)
    temporary.replace(target)
    result = inspect_database(target)
    result["operation"] = "BACKUP_CREATED"
    return result


def restore_database(
    input_path: Path,
    target_path: Path,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    source = input_path.resolve()
    target = target_path.resolve()
    source_inspection = inspect_database(source)
    if source == target:
        raise ValueError("恢复源不能与目标数据库相同")
    if target.exists() and not replace:
        raise FileExistsError(f"恢复目标已存在；如需覆盖请显式使用replace：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".restore.tmp")
    if temporary.exists():
        temporary.unlink()
    source_connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(temporary)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()
    restored = inspect_database(temporary)
    if restored["sha256"] != source_inspection["sha256"]:
        raise ValueError("恢复后的数据库哈希与备份不一致")
    temporary.replace(target)
    result = inspect_database(target)
    result["operation"] = "BACKUP_RESTORED"
    return result


def write_backup_manifest(path: Path, result: dict[str, Any]) -> Path:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


__all__ = [
    "backup_database",
    "inspect_database",
    "restore_database",
    "write_backup_manifest",
]
