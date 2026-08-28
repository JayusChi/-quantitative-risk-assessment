from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = "1.5.0"

ADMIN_BROWSABLE_TABLES = (
    "conversion_job",
    "conversion_source",
    "conversion_parse_artifact",
    "input_snapshot_provenance",
    "input_snapshot",
    "input_segment",
    "input_indicator_observation",
    "input_population_receptor",
    "input_raw_record",
    "calculation_run",
    "calculation_node",
    "calculation_result_document",
    "calculation_segment_result",
    "calculation_artifact",
    "audit_event",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class QraDatabase:
    """SQLite persistence used by the isolated database-backed QRA adapter."""

    def __init__(self, path: Path | str):
        self.path = Path(path).resolve()
        self._initialized = False
        self._initialize_lock = Lock()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection and always commit/rollback and close it."""
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        if self._initialized:
            return
        schema = """
        CREATE TABLE IF NOT EXISTS db_schema (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS input_snapshot (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            source_path TEXT,
            schema_version TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS input_segment (
            snapshot_id TEXT NOT NULL REFERENCES input_snapshot(id) ON DELETE CASCADE,
            segment_code TEXT NOT NULL,
            start_km REAL NOT NULL,
            end_km REAL NOT NULL,
            length_km REAL NOT NULL,
            outside_diameter_mm REAL,
            wall_thickness_mm REAL,
            start_xy_json TEXT,
            end_xy_json TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (snapshot_id, segment_code)
        );

        CREATE TABLE IF NOT EXISTS input_indicator_observation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT NOT NULL REFERENCES input_snapshot(id) ON DELETE CASCADE,
            segment_code TEXT,
            indicator_id TEXT NOT NULL,
            value_json TEXT NOT NULL,
            quality TEXT,
            source_ref TEXT
        );

        CREATE TABLE IF NOT EXISTS input_population_receptor (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT NOT NULL REFERENCES input_snapshot(id) ON DELETE CASCADE,
            receptor_code TEXT NOT NULL,
            segment_code TEXT,
            receptor_type TEXT,
            xy_json TEXT,
            population_day REAL,
            population_night REAL,
            outdoor_fraction_day REAL,
            outdoor_fraction_night REAL,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS input_raw_record (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT NOT NULL REFERENCES input_snapshot(id) ON DELETE CASCADE,
            category_id TEXT NOT NULL,
            record_code TEXT,
            segment_code TEXT,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS conversion_job (
            id TEXT PRIMARY KEY,
            batch_id TEXT,
            parent_job_id TEXT REFERENCES conversion_job(id),
            dedupe_key TEXT NOT NULL,
            status TEXT NOT NULL,
            progress INTEGER NOT NULL,
            status_message TEXT NOT NULL,
            profile_id TEXT NOT NULL,
            profile_version TEXT NOT NULL,
            profile_sha256 TEXT NOT NULL,
            profile_path TEXT NOT NULL,
            contract_id TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            contract_sha256 TEXT NOT NULL,
            contract_path TEXT NOT NULL,
            failure_policy TEXT NOT NULL DEFAULT 'ALL_OR_NOTHING',
            intake_rules_version TEXT,
            file_manifest_sha256 TEXT,
            intake_issues_json TEXT,
            cancel_requested_at TEXT,
            cancelled_at TEXT,
            cancelled_by TEXT,
            revision INTEGER NOT NULL DEFAULT 1,
            converter_version TEXT NOT NULL,
            case_id TEXT,
            project_name TEXT,
            source_count INTEGER NOT NULL,
            source_bytes INTEGER NOT NULL,
            review_decisions_json TEXT,
            payload_json TEXT,
            case_sha256 TEXT,
            source_manifest_json TEXT,
            conversion_report_json TEXT,
            preview_json TEXT,
            review_audit_json TEXT,
            error_json TEXT,
            snapshot_id TEXT REFERENCES input_snapshot(id),
            retry_count INTEGER NOT NULL DEFAULT 0,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            confirmed_by TEXT,
            confirmed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS conversion_source (
            job_id TEXT NOT NULL REFERENCES conversion_job(id) ON DELETE CASCADE,
            id TEXT NOT NULL,
            file_name TEXT NOT NULL,
            media_type TEXT NOT NULL,
            relative_path TEXT,
            original_file_name TEXT,
            declared_media_type TEXT,
            detected_media_type TEXT,
            byte_count INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            source_kind TEXT,
            security_status TEXT,
            security_issue_code TEXT,
            security_issue_message TEXT,
            duplicate_of_source_id TEXT,
            version_group_id TEXT,
            archive_name TEXT,
            archive_member_path TEXT,
            parser_id TEXT,
            parser_version TEXT,
            parse_sha256 TEXT,
            parse_quality_json TEXT,
            parsed_at TEXT,
            created_at TEXT,
            content BLOB NOT NULL,
            PRIMARY KEY (job_id, file_name)
        );

        CREATE TABLE IF NOT EXISTS conversion_parse_artifact (
            job_id TEXT NOT NULL REFERENCES conversion_job(id) ON DELETE CASCADE,
            source_id TEXT NOT NULL,
            path TEXT NOT NULL,
            artifact_kind TEXT NOT NULL,
            content_type TEXT NOT NULL,
            content BLOB NOT NULL,
            byte_count INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            parse_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (job_id, source_id, path)
        );

        CREATE TABLE IF NOT EXISTS input_snapshot_provenance (
            snapshot_id TEXT PRIMARY KEY REFERENCES input_snapshot(id) ON DELETE CASCADE,
            conversion_job_id TEXT NOT NULL REFERENCES conversion_job(id),
            converter_version TEXT NOT NULL,
            mapping_profile_id TEXT NOT NULL,
            mapping_version TEXT NOT NULL,
            mapping_sha256 TEXT NOT NULL,
            contract_id TEXT,
            contract_version TEXT,
            contract_sha256 TEXT,
            source_manifest_json TEXT NOT NULL,
            case_sha256 TEXT NOT NULL,
            confirmed_by TEXT NOT NULL,
            confirmed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS calculation_run (
            id TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL REFERENCES input_snapshot(id),
            status TEXT NOT NULL,
            result_tier TEXT,
            engine_version TEXT,
            input_sha256 TEXT NOT NULL,
            result_sha256 TEXT,
            summary_json TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        );

        CREATE TABLE IF NOT EXISTS calculation_node (
            run_id TEXT NOT NULL REFERENCES calculation_run(id) ON DELETE CASCADE,
            node_id TEXT NOT NULL,
            sequence_no INTEGER NOT NULL,
            label_zh TEXT,
            standard_ref TEXT,
            status TEXT NOT NULL,
            missing_inputs_json TEXT NOT NULL,
            blocked_dependencies_json TEXT NOT NULL,
            result_path TEXT,
            error_message TEXT,
            PRIMARY KEY (run_id, node_id)
        );

        CREATE TABLE IF NOT EXISTS calculation_result_document (
            run_id TEXT NOT NULL REFERENCES calculation_run(id) ON DELETE CASCADE,
            node_id TEXT NOT NULL,
            schema_version TEXT,
            result_json TEXT NOT NULL,
            result_sha256 TEXT NOT NULL,
            PRIMARY KEY (run_id, node_id)
        );

        CREATE TABLE IF NOT EXISTS calculation_segment_result (
            run_id TEXT NOT NULL REFERENCES calculation_run(id) ON DELETE CASCADE,
            segment_code TEXT NOT NULL,
            risk_rank INTEGER,
            pll_per_year REAL NOT NULL,
            risk_lower_bound REAL,
            risk_upper_bound REAL,
            risk_density_per_km_year REAL,
            initiating_frequency_per_year REAL,
            maximum_ir_per_year REAL,
            evidence_factor REAL,
            evidence_coverage REAL,
            uncertainty_factor REAL,
            display_risk_band TEXT,
            dominant_scenario_json TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (run_id, segment_code)
        );

        CREATE TABLE IF NOT EXISTS calculation_artifact (
            run_id TEXT NOT NULL REFERENCES calculation_run(id) ON DELETE CASCADE,
            path TEXT NOT NULL,
            content_type TEXT NOT NULL,
            content BLOB NOT NULL,
            byte_count INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            PRIMARY KEY (run_id, path)
        );

        CREATE TABLE IF NOT EXISTS audit_event (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT,
            actor TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_input_segment_snapshot
            ON input_segment(snapshot_id, start_km);
        CREATE INDEX IF NOT EXISTS idx_conversion_dedupe
            ON conversion_job(dedupe_key, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_conversion_status
            ON conversion_job(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_conversion_parse_artifact_source
            ON conversion_parse_artifact(job_id, source_id, artifact_kind);
        CREATE INDEX IF NOT EXISTS idx_conversion_batch
            ON conversion_job(batch_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_input_indicator_lookup
            ON input_indicator_observation(snapshot_id, segment_code, indicator_id);
        CREATE INDEX IF NOT EXISTS idx_input_raw_lookup
            ON input_raw_record(snapshot_id, category_id, segment_code);
        CREATE INDEX IF NOT EXISTS idx_run_snapshot
            ON calculation_run(snapshot_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_run_status
            ON calculation_run(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_segment_ranking
            ON calculation_segment_result(run_id, risk_rank);
        CREATE INDEX IF NOT EXISTS idx_audit_created
            ON audit_event(created_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_entity
            ON audit_event(entity_type, entity_id, created_at DESC);
        """
        with self._initialize_lock:
            if self._initialized:
                return
            with self.session() as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(schema)
                existing_conversion_columns = {
                    str(row["name"])
                    for row in connection.execute('PRAGMA table_info("conversion_job")').fetchall()
                }
                conversion_migration_columns = {
                    "contract_id": "TEXT",
                    "contract_version": "TEXT",
                    "contract_sha256": "TEXT",
                    "contract_path": "TEXT",
                    "failure_policy": "TEXT NOT NULL DEFAULT 'ALL_OR_NOTHING'",
                    "intake_rules_version": "TEXT",
                    "file_manifest_sha256": "TEXT",
                    "intake_issues_json": "TEXT",
                    "cancel_requested_at": "TEXT",
                    "cancelled_at": "TEXT",
                    "cancelled_by": "TEXT",
                    "revision": "INTEGER NOT NULL DEFAULT 1",
                }
                for column, declaration in conversion_migration_columns.items():
                    if column not in existing_conversion_columns:
                        connection.execute(
                            f'ALTER TABLE conversion_job ADD COLUMN "{column}" {declaration}'
                        )
                existing_source_columns = {
                    str(row["name"])
                    for row in connection.execute(
                        'PRAGMA table_info("conversion_source")'
                    ).fetchall()
                }
                source_migration_columns = {
                    "id": "TEXT",
                    "relative_path": "TEXT",
                    "original_file_name": "TEXT",
                    "declared_media_type": "TEXT",
                    "detected_media_type": "TEXT",
                    "source_kind": "TEXT",
                    "security_status": "TEXT",
                    "security_issue_code": "TEXT",
                    "security_issue_message": "TEXT",
                    "duplicate_of_source_id": "TEXT",
                    "version_group_id": "TEXT",
                    "archive_name": "TEXT",
                    "archive_member_path": "TEXT",
                    "parser_id": "TEXT",
                    "parser_version": "TEXT",
                    "parse_sha256": "TEXT",
                    "parse_quality_json": "TEXT",
                    "parsed_at": "TEXT",
                    "created_at": "TEXT",
                }
                for column, declaration in source_migration_columns.items():
                    if column not in existing_source_columns:
                        connection.execute(
                            f'ALTER TABLE conversion_source ADD COLUMN "{column}" {declaration}'
                        )
                legacy_sources = connection.execute(
                    """
                    SELECT rowid, file_name, media_type FROM conversion_source
                    WHERE id IS NULL OR relative_path IS NULL OR security_status IS NULL
                    """
                ).fetchall()
                for source in legacy_sources:
                    connection.execute(
                        """
                        UPDATE conversion_source
                        SET id = coalesce(id, ?),
                            relative_path = coalesce(relative_path, file_name),
                            original_file_name = coalesce(original_file_name, file_name),
                            declared_media_type = coalesce(declared_media_type, media_type),
                            detected_media_type = coalesce(detected_media_type, media_type),
                            source_kind = coalesce(source_kind, 'UNKNOWN'),
                            security_status = coalesce(security_status, 'READY_FOR_PARSE'),
                            created_at = coalesce(created_at, ?)
                        WHERE rowid = ?
                        """,
                        (f"SOURCE-{uuid4()}", utc_now(), int(source["rowid"])),
                    )
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_conversion_source_id "
                    "ON conversion_source(id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_conversion_source_hash "
                    "ON conversion_source(sha256, created_at)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_conversion_source_status "
                    "ON conversion_source(job_id, security_status, relative_path)"
                )
                existing_provenance_columns = {
                    str(row["name"])
                    for row in connection.execute(
                        'PRAGMA table_info("input_snapshot_provenance")'
                    ).fetchall()
                }
                for column in (
                    "contract_id",
                    "contract_version",
                    "contract_sha256",
                ):
                    if column not in existing_provenance_columns:
                        connection.execute(
                            f'ALTER TABLE input_snapshot_provenance ADD COLUMN "{column}" TEXT'
                        )
                connection.execute(
                    "INSERT OR IGNORE INTO db_schema(version, applied_at) VALUES (?, ?)",
                    (SCHEMA_VERSION, utc_now()),
                )
                connection.execute("PRAGMA optimize")
            self._initialized = True

    def import_case(
        self,
        case: dict[str, Any],
        *,
        name: str,
        source_path: str | None = None,
    ) -> tuple[str, bool]:
        """Persist an immutable input snapshot and query-friendly projections."""
        self.initialize()
        payload_text = canonical_json(case)
        payload_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM input_snapshot WHERE payload_sha256 = ?",
                (payload_hash,),
            ).fetchone()
            if existing:
                return str(existing["id"]), False
            snapshot_id = self._insert_snapshot_in_connection(
                connection,
                case,
                name=name,
                source_path=source_path,
                payload_text=payload_text,
                payload_hash=payload_hash,
            )
            return snapshot_id, True

    @classmethod
    def _insert_snapshot_in_connection(
        cls,
        connection: sqlite3.Connection,
        case: dict[str, Any],
        *,
        name: str,
        source_path: str | None,
        payload_text: str,
        payload_hash: str,
        actor: str = "local-admin",
    ) -> str:
        snapshot_id = f"SNAP-{uuid4()}"
        connection.execute(
            """
            INSERT INTO input_snapshot(
                id, name, source_path, schema_version, payload_json,
                payload_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                name,
                source_path,
                str(case.get("schema_version", "dynamic-case-v1")),
                payload_text,
                payload_hash,
                utc_now(),
            ),
        )
        cls._project_segments(connection, snapshot_id, case)
        cls._project_indicators(connection, snapshot_id, case)
        cls._project_population(connection, snapshot_id, case)
        cls._project_raw_records(connection, snapshot_id, case)
        cls._record_event_in_connection(
            connection,
            event_type="SNAPSHOT_IMPORTED",
            entity_type="input_snapshot",
            entity_id=snapshot_id,
            detail={
                "name": name,
                "payload_sha256": payload_hash,
                "segment_count": len(case.get("segments", [])),
            },
            actor=actor,
        )
        return snapshot_id

    @staticmethod
    def _record_event_in_connection(
        connection: sqlite3.Connection,
        *,
        event_type: str,
        entity_type: str,
        entity_id: str | None,
        detail: dict[str, Any] | None = None,
        actor: str = "local-admin",
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_event(
                event_type, entity_type, entity_id, actor, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                entity_type,
                entity_id,
                actor,
                canonical_json(detail or {}),
                utc_now(),
            ),
        )

    def record_event(
        self,
        *,
        event_type: str,
        entity_type: str,
        entity_id: str | None,
        detail: dict[str, Any] | None = None,
        actor: str = "local-admin",
    ) -> None:
        self.initialize()
        with self.transaction() as connection:
            self._record_event_in_connection(
                connection,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                detail=detail,
                actor=actor,
            )

    @staticmethod
    def _project_segments(
        connection: sqlite3.Connection,
        snapshot_id: str,
        case: dict[str, Any],
    ) -> None:
        for segment in case.get("segments", []):
            connection.execute(
                """
                INSERT INTO input_segment(
                    snapshot_id, segment_code, start_km, end_km, length_km,
                    outside_diameter_mm, wall_thickness_mm, start_xy_json,
                    end_xy_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    str(segment["segment_id"]),
                    float(segment["start_km"]),
                    float(segment["end_km"]),
                    float(segment["length_km"]),
                    segment.get("outside_diameter_mm"),
                    segment.get("wall_thickness_mm"),
                    canonical_json(segment.get("start_xy_m"))
                    if segment.get("start_xy_m") is not None
                    else None,
                    canonical_json(segment.get("end_xy_m"))
                    if segment.get("end_xy_m") is not None
                    else None,
                    canonical_json(segment),
                ),
            )

    @staticmethod
    def _project_indicators(
        connection: sqlite3.Connection,
        snapshot_id: str,
        case: dict[str, Any],
    ) -> None:
        indicators = case.get("engineering_indicators", {})

        def insert(segment_code: str | None, indicator_id: str, value: Any) -> None:
            observation = value if isinstance(value, dict) else {"value": value}
            connection.execute(
                """
                INSERT INTO input_indicator_observation(
                    snapshot_id, segment_code, indicator_id, value_json,
                    quality, source_ref
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    segment_code,
                    indicator_id,
                    canonical_json(observation),
                    observation.get("quality"),
                    observation.get("source_ref"),
                ),
            )

        for indicator_id, value in indicators.get("observations_global", {}).items():
            insert(None, str(indicator_id), value)
        for segment_code, rows in indicators.get("observations_by_segment", {}).items():
            for indicator_id, value in rows.items():
                insert(str(segment_code), str(indicator_id), value)

    @staticmethod
    def _population_rows(case: dict[str, Any]) -> list[dict[str, Any]]:
        rows = case.get("population_cells")
        if isinstance(rows, list) and rows:
            return rows
        return (
            case.get("raw_data_categories", {})
            .get("high_consequence_targets", {})
            .get("records", [])
        )

    @classmethod
    def _project_population(
        cls,
        connection: sqlite3.Connection,
        snapshot_id: str,
        case: dict[str, Any],
    ) -> None:
        for index, row in enumerate(cls._population_rows(case), start=1):
            receptor_code = str(
                row.get("cell_id")
                or row.get("target_id")
                or row.get("record_id")
                or f"REC-{index:04d}"
            )
            connection.execute(
                """
                INSERT INTO input_population_receptor(
                    snapshot_id, receptor_code, segment_code, receptor_type,
                    xy_json, population_day, population_night,
                    outdoor_fraction_day, outdoor_fraction_night, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    receptor_code,
                    row.get("segment_id"),
                    row.get("receptor_type") or row.get("target_type"),
                    canonical_json(row.get("xy_m")) if row.get("xy_m") is not None else None,
                    row.get("population_day"),
                    row.get("population_night"),
                    row.get("outdoor_fraction_day"),
                    row.get("outdoor_fraction_night"),
                    canonical_json(row),
                ),
            )

    @staticmethod
    def _project_raw_records(
        connection: sqlite3.Connection,
        snapshot_id: str,
        case: dict[str, Any],
    ) -> None:
        for category_id, category in case.get("raw_data_categories", {}).items():
            for index, row in enumerate(category.get("records", []), start=1):
                record_code = str(
                    row.get("record_id") or row.get("target_id") or f"{category_id}-{index}"
                )
                connection.execute(
                    """
                    INSERT INTO input_raw_record(
                        snapshot_id, category_id, record_code, segment_code,
                        payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        str(category_id),
                        record_code,
                        row.get("segment_id"),
                        canonical_json(row),
                    ),
                )

    def create_conversion_job(
        self,
        *,
        dedupe_key: str,
        profile_id: str,
        profile_version: str,
        profile_sha256: str,
        profile_path: str,
        contract_id: str,
        contract_version: str,
        contract_sha256: str,
        contract_path: str,
        failure_policy: str = "ALL_OR_NOTHING",
        intake_rules_version: str | None = None,
        file_manifest_sha256: str | None = None,
        intake_issues: list[dict[str, Any]] | None = None,
        converter_version: str,
        sources: list[dict[str, Any]],
        case_id: str | None = None,
        project_name: str | None = None,
        review_decisions: dict[str, Any] | None = None,
        batch_id: str | None = None,
        parent_job_id: str | None = None,
        retry_count: int = 0,
        actor: str = "local-admin",
        force: bool = False,
        initial_status: str = "QUEUED",
    ) -> tuple[str, bool]:
        """Persist an asynchronous conversion request and its protected sources."""
        self.initialize()
        if failure_policy not in {"ALL_OR_NOTHING", "QUARANTINE_AND_CONTINUE"}:
            raise ValueError("failure_policy无效")
        if initial_status not in {"QUEUED", "BLOCKED"}:
            raise ValueError("转换任务初始状态无效")
        with self.transaction() as connection:
            if not force:
                existing = connection.execute(
                    """
                    SELECT id FROM conversion_job
                    WHERE dedupe_key = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (dedupe_key,),
                ).fetchone()
                if existing is not None:
                    return str(existing["id"]), False
            if parent_job_id is not None:
                parent = connection.execute(
                    "SELECT id FROM conversion_job WHERE id = ?", (parent_job_id,)
                ).fetchone()
                if parent is None:
                    raise KeyError(f"转换任务不存在：{parent_job_id}")
            job_id = (
                f"CONV-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
            )
            source_bytes = sum(
                int(source["byte_count"])
                for source in sources
                if source.get("archive_name") is None
            )
            status_message = (
                "入口检查发现隔离文件，未进入解析队列"
                if initial_status == "BLOCKED"
                else "文件已登记并通过入口检查，等待转换工作线程"
            )
            connection.execute(
                """
                INSERT INTO conversion_job(
                    id, batch_id, parent_job_id, dedupe_key, status, progress,
                    status_message, profile_id, profile_version, profile_sha256,
                    profile_path, contract_id, contract_version, contract_sha256,
                    contract_path, failure_policy, intake_rules_version,
                    file_manifest_sha256, intake_issues_json, converter_version,
                    case_id, project_name, source_count, source_bytes,
                    review_decisions_json, retry_count, created_by, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, 0,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    job_id,
                    batch_id,
                    parent_job_id,
                    dedupe_key,
                    initial_status,
                    status_message,
                    profile_id,
                    profile_version,
                    profile_sha256,
                    profile_path,
                    contract_id,
                    contract_version,
                    contract_sha256,
                    contract_path,
                    failure_policy,
                    intake_rules_version,
                    file_manifest_sha256,
                    canonical_json(intake_issues or []),
                    converter_version,
                    case_id,
                    project_name,
                    len(sources),
                    source_bytes,
                    canonical_json(review_decisions) if review_decisions else None,
                    retry_count,
                    actor,
                    utc_now(),
                ),
            )
            additional_intake_issues: list[dict[str, Any]] = []
            for source in sources:
                source_id = str(source.get("id") or f"SOURCE-{uuid4()}")
                duplicate_of = source.get("duplicate_of_source_id")
                if duplicate_of is None and source.get("security_status") == "READY_FOR_PARSE":
                    duplicate = connection.execute(
                        """
                        SELECT id FROM conversion_source
                        WHERE sha256 = ? AND id IS NOT NULL
                        ORDER BY created_at DESC LIMIT 1
                        """,
                        (str(source["sha256"]),),
                    ).fetchone()
                    if duplicate is not None:
                        duplicate_of = str(duplicate["id"])
                version_group_id = source.get("version_group_id")
                original_name = str(
                    source.get("original_file_name") or source.get("file_name") or ""
                )
                historical_version = connection.execute(
                    """
                    SELECT id, version_group_id FROM conversion_source
                    WHERE job_id <> ? AND original_file_name = ? COLLATE NOCASE
                        AND sha256 <> ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (job_id, original_name, str(source["sha256"])),
                ).fetchone()
                if historical_version is not None:
                    version_group_id = str(
                        historical_version["version_group_id"]
                        or (
                            "VERSION-"
                            + hashlib.sha256(original_name.casefold().encode("utf-8")).hexdigest()[
                                :20
                            ]
                        )
                    )
                    connection.execute(
                        "UPDATE conversion_source SET version_group_id = ? WHERE id = ?",
                        (version_group_id, str(historical_version["id"])),
                    )
                    additional_intake_issues.append(
                        {
                            "code": "INTAKE.POSSIBLE_NEW_VERSION",
                            "message": "检测到历史任务中存在同名异哈希资料",
                            "severity": "WARNING",
                            "relative_path": source.get("relative_path") or source["file_name"],
                            "blocking": False,
                        }
                    )
                connection.execute(
                    """
                    INSERT INTO conversion_source(
                        job_id, id, file_name, media_type, relative_path,
                        original_file_name, declared_media_type, detected_media_type,
                        byte_count, sha256, source_kind, security_status,
                        security_issue_code, security_issue_message,
                        duplicate_of_source_id, version_group_id, archive_name,
                        archive_member_path, created_at, content
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        source_id,
                        str(source["file_name"]),
                        str(source["media_type"]),
                        str(source.get("relative_path") or source["file_name"]),
                        original_name,
                        str(source.get("declared_media_type") or source["media_type"]),
                        str(source.get("detected_media_type") or source["media_type"]),
                        int(source["byte_count"]),
                        str(source["sha256"]),
                        str(source.get("source_kind") or "UNKNOWN"),
                        str(source.get("security_status") or "READY_FOR_PARSE"),
                        source.get("security_issue_code"),
                        source.get("security_issue_message"),
                        duplicate_of,
                        version_group_id,
                        source.get("archive_name"),
                        source.get("archive_member_path"),
                        utc_now(),
                        bytes(source["content"]),
                    ),
                )
                self._record_event_in_connection(
                    connection,
                    event_type="SOURCE_RECEIVED",
                    entity_type="conversion_source",
                    entity_id=source_id,
                    detail={
                        "job_id": job_id,
                        "relative_path": source.get("relative_path") or source["file_name"],
                        "byte_count": int(source["byte_count"]),
                        "sha256": str(source["sha256"]),
                        "detected_media_type": source.get("detected_media_type")
                        or source["media_type"],
                    },
                    actor=actor,
                )
                if source.get("security_status") == "QUARANTINED":
                    self._record_event_in_connection(
                        connection,
                        event_type="SOURCE_QUARANTINED",
                        entity_type="conversion_source",
                        entity_id=source_id,
                        detail={
                            "job_id": job_id,
                            "issue_code": source.get("security_issue_code"),
                            "relative_path": source.get("relative_path") or source["file_name"],
                        },
                        actor=actor,
                    )
                if duplicate_of is not None:
                    self._record_event_in_connection(
                        connection,
                        event_type="SOURCE_DUPLICATE_FOUND",
                        entity_type="conversion_source",
                        entity_id=source_id,
                        detail={"job_id": job_id, "duplicate_of_source_id": duplicate_of},
                        actor=actor,
                    )
                if historical_version is not None:
                    self._record_event_in_connection(
                        connection,
                        event_type="SOURCE_POSSIBLE_NEW_VERSION",
                        entity_type="conversion_source",
                        entity_id=source_id,
                        detail={"job_id": job_id, "version_group_id": version_group_id},
                        actor=actor,
                    )
            if additional_intake_issues:
                connection.execute(
                    "UPDATE conversion_job SET intake_issues_json = ? WHERE id = ?",
                    (
                        canonical_json((intake_issues or []) + additional_intake_issues),
                        job_id,
                    ),
                )
            self._record_event_in_connection(
                connection,
                event_type="CONVERSION_QUEUED",
                entity_type="conversion_job",
                entity_id=job_id,
                detail={
                    "batch_id": batch_id,
                    "parent_job_id": parent_job_id,
                    "profile_id": profile_id,
                    "profile_version": profile_version,
                    "contract_id": contract_id,
                    "contract_version": contract_version,
                    "contract_sha256": contract_sha256,
                    "source_count": len(sources),
                    "source_bytes": source_bytes,
                    "dedupe_key": dedupe_key,
                    "failure_policy": failure_policy,
                    "file_manifest_sha256": file_manifest_sha256,
                    "intake_rules_version": intake_rules_version,
                    "initial_status": initial_status,
                },
                actor=actor,
            )
        return job_id, True

    @staticmethod
    def _decode_conversion_row(row: sqlite3.Row, *, detailed: bool) -> dict[str, Any]:
        item = dict(row)
        json_fields = (
            "review_decisions_json",
            "intake_issues_json",
            "source_manifest_json",
            "conversion_report_json",
            "preview_json",
            "review_audit_json",
            "error_json",
        )
        for field_name in json_fields:
            value = item.pop(field_name, None)
            item[field_name.removesuffix("_json")] = (
                json.loads(str(value)) if value is not None else None
            )
        payload = item.pop("payload_json", None)
        if detailed and payload is not None:
            item["payload"] = json.loads(str(payload))
        return item

    def get_conversion_job(self, job_id: str, *, detailed: bool = True) -> dict[str, Any]:
        self.initialize()
        with self.session() as connection:
            row = connection.execute(
                "SELECT * FROM conversion_job WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"转换任务不存在：{job_id}")
        item = self._decode_conversion_row(row, detailed=detailed)
        item["sources"] = self.list_conversion_sources(job_id)
        item["issue_summary"] = self._conversion_issue_summary(item)
        return item

    @staticmethod
    def _conversion_issue_summary(item: dict[str, Any]) -> dict[str, int]:
        issues = item.get("intake_issues") or []
        return {
            "total": len(issues),
            "blocking": sum(bool(issue.get("blocking", True)) for issue in issues),
            "quarantined_sources": sum(
                source.get("security_status") == "QUARANTINED" for source in item.get("sources", [])
            ),
        }

    def list_conversion_jobs(
        self,
        limit: int = 200,
        *,
        status: str | None = None,
        cursor: str | None = None,
    ) -> list[dict[str, Any]]:
        self.initialize()
        safe_limit = max(1, min(int(limit), 500))
        filters: list[str] = []
        parameters: list[Any] = []
        if status:
            filters.append("status = ?")
            parameters.append(str(status))
        if cursor:
            filters.append(
                "(created_at, id) < (SELECT created_at, id FROM conversion_job WHERE id = ?)"
            )
            parameters.append(str(cursor))
        where = " WHERE " + " AND ".join(filters) if filters else ""
        with self.session() as connection:
            rows = connection.execute(
                f"""
                SELECT id, batch_id, parent_job_id, dedupe_key, status, progress,
                       status_message, profile_id, profile_version, profile_sha256,
                       contract_id, contract_version, contract_sha256,
                       failure_policy, intake_rules_version, file_manifest_sha256,
                       cancel_requested_at, cancelled_at, cancelled_by, revision,
                       converter_version, case_id, project_name, source_count,
                       source_bytes, case_sha256, snapshot_id, retry_count,
                       created_by, created_at, started_at, finished_at,
                       confirmed_by, confirmed_at, error_json
                FROM conversion_job
                {where}
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (*parameters, safe_limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            error = item.pop("error_json")
            item["error"] = json.loads(str(error)) if error is not None else None
            result.append(item)
        return result

    def list_conversion_sources(self, job_id: str) -> list[dict[str, Any]]:
        self.initialize()
        with self.session() as connection:
            exists = connection.execute(
                "SELECT 1 FROM conversion_job WHERE id = ?", (job_id,)
            ).fetchone()
            if exists is None:
                raise KeyError(f"转换任务不存在：{job_id}")
            rows = connection.execute(
                """
                SELECT id, file_name, relative_path, original_file_name,
                       media_type, declared_media_type, detected_media_type,
                       byte_count, sha256, source_kind, security_status,
                       security_issue_code, security_issue_message,
                       duplicate_of_source_id, version_group_id, archive_name,
                       archive_member_path, parser_id, parser_version,
                       parse_sha256, parse_quality_json, parsed_at, created_at
                FROM conversion_source
                WHERE job_id = ?
                ORDER BY coalesce(archive_name, ''), coalesce(relative_path, file_name), id
                """,
                (job_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            quality = item.pop("parse_quality_json")
            item["parse_quality"] = json.loads(str(quality)) if quality else None
            result.append(item)
        return result

    def record_conversion_source_parse(
        self,
        job_id: str,
        source_id: str,
        *,
        succeeded: bool,
        parser_id: str,
        parser_version: str,
        parse_sha256: str,
        quality_summary: dict[str, Any],
        artifacts: list[dict[str, Any]],
        issue_code: str | None = None,
        issue_message: str | None = None,
    ) -> None:
        """Atomically persist one source's parse state and controlled artifact resources."""
        if len(parse_sha256) != 64:
            raise ValueError("解析产物哈希无效")
        prepared: list[tuple[str, str, str, bytes, int, str]] = []
        seen: set[str] = set()
        for artifact in artifacts:
            raw_path = str(artifact.get("path") or "").replace("\\", "/")
            relative = PurePosixPath(raw_path)
            if (
                relative.is_absolute()
                or not relative.parts
                or len(raw_path) > 500
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise ValueError("解析产物相对路径无效")
            path_text = relative.as_posix()
            if path_text in seen:
                raise ValueError("解析产物路径重复")
            seen.add(path_text)
            content = bytes(artifact.get("content") or b"")
            content_hash = bytes_sha256(content)
            expected_hash = str(artifact.get("sha256") or content_hash)
            if expected_hash != content_hash:
                raise ValueError("解析产物内容哈希不一致")
            prepared.append(
                (
                    path_text,
                    str(artifact.get("artifact_kind") or "RESOURCE")[:80],
                    str(artifact.get("content_type") or "application/octet-stream")[:120],
                    content,
                    len(content),
                    content_hash,
                )
            )
        now = utc_now()
        status = "PARSED" if succeeded else "PARSE_FAILED"
        with self.transaction() as connection:
            job = connection.execute(
                "SELECT status, cancel_requested_at FROM conversion_job WHERE id = ?",
                (job_id,),
            ).fetchone()
            if job is None or job["status"] != "RUNNING" or job["cancel_requested_at"]:
                raise ValueError("转换任务当前不允许固化解析产物")
            cursor = connection.execute(
                """
                UPDATE conversion_source
                SET security_status = ?, parser_id = ?, parser_version = ?,
                    parse_sha256 = ?, parse_quality_json = ?, parsed_at = ?,
                    security_issue_code = CASE WHEN ? = 'PARSE_FAILED' THEN ?
                                               ELSE security_issue_code END,
                    security_issue_message = CASE WHEN ? = 'PARSE_FAILED' THEN ?
                                                  ELSE security_issue_message END
                WHERE job_id = ? AND id = ? AND security_status = 'READY_FOR_PARSE'
                """,
                (
                    status,
                    parser_id,
                    parser_version,
                    parse_sha256,
                    canonical_json(quality_summary),
                    now,
                    status,
                    issue_code,
                    status,
                    issue_message,
                    job_id,
                    source_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("源文件不属于任务或当前状态不能解析")
            connection.execute(
                "DELETE FROM conversion_parse_artifact WHERE job_id = ? AND source_id = ?",
                (job_id, source_id),
            )
            connection.executemany(
                """
                INSERT INTO conversion_parse_artifact(
                    job_id, source_id, path, artifact_kind, content_type,
                    content, byte_count, sha256, parse_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        job_id,
                        source_id,
                        path,
                        kind,
                        content_type,
                        content,
                        byte_count,
                        content_hash,
                        parse_sha256,
                        now,
                    )
                    for path, kind, content_type, content, byte_count, content_hash in prepared
                ],
            )
            self._record_event_in_connection(
                connection,
                event_type="SOURCE_PARSED" if succeeded else "SOURCE_PARSE_FAILED",
                entity_type="conversion_source",
                entity_id=source_id,
                detail={
                    "job_id": job_id,
                    "parser_id": parser_id,
                    "parser_version": parser_version,
                    "parse_sha256": parse_sha256,
                    "artifact_count": len(prepared),
                    "issue_code": issue_code,
                },
            )

    def list_conversion_parse_artifacts(
        self, job_id: str, source_id: str | None = None
    ) -> list[dict[str, Any]]:
        self.initialize()
        where = " AND source_id = ?" if source_id else ""
        parameters: tuple[Any, ...] = (job_id, source_id) if source_id else (job_id,)
        with self.session() as connection:
            rows = connection.execute(
                f"""
                SELECT source_id, path, artifact_kind, content_type, byte_count,
                       sha256, parse_sha256, created_at
                FROM conversion_parse_artifact
                WHERE job_id = ?{where}
                ORDER BY source_id, path
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_conversion_parse_artifact(
        self, job_id: str, source_id: str, path: str
    ) -> tuple[dict[str, Any], bytes] | None:
        self.initialize()
        with self.session() as connection:
            row = connection.execute(
                """
                SELECT path, artifact_kind, content_type, byte_count, sha256,
                       parse_sha256, created_at, content
                FROM conversion_parse_artifact
                WHERE job_id = ? AND source_id = ? AND path = ?
                """,
                (job_id, source_id, path),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        content = bytes(item.pop("content"))
        return item, content

    def list_conversion_events(self, job_id: str, limit: int = 500) -> list[dict[str, Any]]:
        self.initialize()
        self.get_conversion_job(job_id, detailed=False)
        source_ids = [source["id"] for source in self.list_conversion_sources(job_id)]
        placeholders = ",".join("?" for _ in source_ids)
        source_clause = (
            f" OR (entity_type = 'conversion_source' AND entity_id IN ({placeholders}))"
            if source_ids
            else ""
        )
        safe_limit = max(1, min(int(limit), 1000))
        with self.session() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM audit_event
                WHERE (entity_type = 'conversion_job' AND entity_id = ?){source_clause}
                ORDER BY created_at, id LIMIT ?
                """,
                (job_id, *source_ids, safe_limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["detail"] = json.loads(item.pop("detail_json"))
            result.append(item)
        return result

    def delete_conversion_job(
        self, job_id: str, *, actor: str = "local-admin"
    ) -> dict[str, Any]:
        """Permanently delete one terminal conversion/OCR record and its payloads."""
        self.initialize()
        deletable_statuses = {
            "CANCELLED",
            "BLOCKED",
            "FAILED",
            "READY_FOR_CONFIRMATION",
        }
        safe_actor = actor.strip()[:160] or "local-admin"
        with self.transaction() as connection:
            job = connection.execute(
                """
                SELECT id, status, snapshot_id, source_count, source_bytes
                FROM conversion_job WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if job is None:
                raise KeyError(f"转换任务不存在：{job_id}")
            status = str(job["status"])
            if status not in deletable_statuses:
                if status == "CONFIRMED" or job["snapshot_id"] is not None:
                    raise ValueError("已确认入库的转换任务关联不可变快照，不能删除")
                raise ValueError("正在排队或运行的转换任务请先取消，任务结束后才能删除")
            provenance = connection.execute(
                "SELECT 1 FROM input_snapshot_provenance WHERE conversion_job_id = ?",
                (job_id,),
            ).fetchone()
            if provenance is not None:
                raise ValueError("转换任务已进入不可变数据追溯链，不能删除")
            source_ids = [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM conversion_source WHERE job_id = ?", (job_id,)
                ).fetchall()
            ]
            artifact_count = int(
                connection.execute(
                    "SELECT count(*) FROM conversion_parse_artifact WHERE job_id = ?",
                    (job_id,),
                ).fetchone()[0]
            )
            # A retry can outlive its parent. Detach it before deleting only the
            # selected row so the button never removes a different task.
            connection.execute(
                "UPDATE conversion_job SET parent_job_id = NULL WHERE parent_job_id = ?",
                (job_id,),
            )
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                connection.execute(
                    f"DELETE FROM audit_event WHERE entity_type = 'conversion_source' "
                    f"AND entity_id IN ({placeholders})",
                    source_ids,
                )
            connection.execute(
                "DELETE FROM audit_event WHERE entity_type = 'conversion_job' AND entity_id = ?",
                (job_id,),
            )
            cursor = connection.execute("DELETE FROM conversion_job WHERE id = ?", (job_id,))
            if cursor.rowcount != 1:
                raise ValueError("转换任务删除失败")
            self._record_event_in_connection(
                connection,
                event_type="CONVERSION_DELETED",
                entity_type="conversion_job",
                entity_id=job_id,
                detail={
                    "previous_status": status,
                    "deleted_source_count": int(job["source_count"]),
                    "deleted_source_bytes": int(job["source_bytes"]),
                    "deleted_artifact_count": artifact_count,
                },
                actor=safe_actor,
            )
        return {
            "status": "DELETED",
            "conversion_id": job_id,
            "deleted_source_count": int(job["source_count"]),
            "deleted_source_bytes": int(job["source_bytes"]),
            "deleted_artifact_count": artifact_count,
        }

    def conversion_source_contents(
        self, job_id: str, *, ready_only: bool = False
    ) -> list[dict[str, Any]]:
        """Return protected source bytes for an in-process worker or retry only."""
        self.initialize()
        with self.session() as connection:
            where = " AND security_status = 'READY_FOR_PARSE'" if ready_only else ""
            rows = connection.execute(
                f"""
                SELECT id, file_name, relative_path, original_file_name, media_type,
                       declared_media_type, detected_media_type, byte_count, sha256,
                       source_kind, security_status, security_issue_code,
                       security_issue_message, duplicate_of_source_id,
                       version_group_id, archive_name, archive_member_path, content
                FROM conversion_source WHERE job_id = ?{where}
                ORDER BY coalesce(archive_name, ''), coalesce(relative_path, file_name), id
                """,
                (job_id,),
            ).fetchall()
        if not rows:
            self.get_conversion_job(job_id, detailed=False)
        return [
            {
                **{
                    key: row[key]
                    for key in (
                        "id",
                        "file_name",
                        "relative_path",
                        "original_file_name",
                        "media_type",
                        "declared_media_type",
                        "detected_media_type",
                        "byte_count",
                        "sha256",
                        "source_kind",
                        "security_status",
                        "security_issue_code",
                        "security_issue_message",
                        "duplicate_of_source_id",
                        "version_group_id",
                        "archive_name",
                        "archive_member_path",
                    )
                },
                "content": bytes(row["content"]),
            }
            for row in rows
        ]

    def conversion_source_content(
        self,
        source_id: str,
        *,
        allowed_statuses: set[str] | None = None,
    ) -> dict[str, Any]:
        """Read one protected source by ID for an in-process parser, never by user path."""
        self.initialize()
        statuses = allowed_statuses or {"READY_FOR_PARSE"}
        with self.session() as connection:
            row = connection.execute(
                """
                SELECT job_id, id, relative_path, original_file_name,
                       detected_media_type, byte_count, sha256, source_kind,
                       security_status, archive_name, archive_member_path, content
                FROM conversion_source WHERE id = ?
                """,
                (source_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"源文件不存在：{source_id}")
        if str(row["security_status"]) not in statuses:
            raise ValueError("源文件状态不允许交给当前解析步骤")
        return {
            **{
                key: row[key]
                for key in (
                    "job_id",
                    "id",
                    "relative_path",
                    "original_file_name",
                    "detected_media_type",
                    "byte_count",
                    "sha256",
                    "source_kind",
                    "security_status",
                    "archive_name",
                    "archive_member_path",
                )
            },
            "content": bytes(row["content"]),
        }

    def set_conversion_running(self, job_id: str) -> None:
        self.initialize()
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE conversion_job
                SET status = 'RUNNING', progress = 5, status_message = ?,
                    started_at = ?, finished_at = NULL, error_json = NULL,
                    revision = revision + 1
                WHERE id = ? AND status = 'QUEUED' AND cancel_requested_at IS NULL
                """,
                ("正在准备源资料", utc_now(), job_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("只有排队中的转换任务可以启动")
            self._record_event_in_connection(
                connection,
                event_type="CONVERSION_STARTED",
                entity_type="conversion_job",
                entity_id=job_id,
            )

    def update_conversion_progress(self, job_id: str, progress: int, status_message: str) -> None:
        safe_progress = max(5, min(int(progress), 95))
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE conversion_job
                SET progress = ?, status_message = ?, revision = revision + 1
                WHERE id = ? AND status = 'RUNNING' AND cancel_requested_at IS NULL
                """,
                (safe_progress, status_message[:240], job_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("转换任务当前不在运行")

    def complete_conversion(
        self,
        job_id: str,
        *,
        payload: dict[str, Any],
        case_sha256: str,
        source_manifest: dict[str, Any],
        conversion_report: dict[str, Any],
        preview: dict[str, Any],
        review_audit: dict[str, Any],
        blocked: bool,
    ) -> None:
        status = "BLOCKED" if blocked else "READY_FOR_CONFIRMATION"
        message = "存在需修正或复核的转换问题" if blocked else "等待用户确认转换预览"
        error = (
            {
                "code": "CONVERSION_BLOCKED",
                "message": message,
                "issues": conversion_report.get("issues", []),
            }
            if blocked
            else None
        )
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE conversion_job
                SET status = ?, progress = 100, status_message = ?, payload_json = ?,
                    case_sha256 = ?, source_manifest_json = ?,
                    conversion_report_json = ?, preview_json = ?,
                    review_audit_json = ?, error_json = ?, finished_at = ?,
                    revision = revision + 1
                WHERE id = ? AND status = 'RUNNING' AND cancel_requested_at IS NULL
                """,
                (
                    status,
                    message,
                    canonical_json(payload),
                    case_sha256,
                    canonical_json(source_manifest),
                    canonical_json(conversion_report),
                    canonical_json(preview),
                    canonical_json(review_audit),
                    canonical_json(error) if error else None,
                    utc_now(),
                    job_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("转换任务当前不在运行")
            connection.execute(
                """
                UPDATE conversion_source SET security_status = 'PARSED'
                WHERE job_id = ? AND security_status = 'READY_FOR_PARSE'
                """,
                (job_id,),
            )
            self._record_event_in_connection(
                connection,
                event_type="CONVERSION_BLOCKED" if blocked else "CONVERSION_READY",
                entity_type="conversion_job",
                entity_id=job_id,
                detail={
                    "case_sha256": case_sha256,
                    "issue_counts": conversion_report.get("summary", {}).get("issue_counts"),
                    "pending_review_count": conversion_report.get("summary", {}).get(
                        "pending_review_count"
                    ),
                },
            )

    def fail_conversion(self, job_id: str, error: dict[str, Any]) -> None:
        with self.transaction() as connection:
            current = connection.execute(
                """
                SELECT status, cancel_requested_at, cancelled_by
                FROM conversion_job WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if current is None:
                return
            if current["cancel_requested_at"] is not None:
                now = utc_now()
                connection.execute(
                    """
                    UPDATE conversion_job
                    SET status = 'CANCELLED', progress = 100,
                        status_message = '取消请求已生效', cancelled_at = ?,
                        finished_at = ?, revision = revision + 1
                    WHERE id = ? AND status IN ('QUEUED', 'RUNNING')
                    """,
                    (now, now, job_id),
                )
                self._record_event_in_connection(
                    connection,
                    event_type="CONVERSION_CANCELLED",
                    entity_type="conversion_job",
                    entity_id=job_id,
                    detail={"reason": "cancel_precedes_failure"},
                    actor=str(current["cancelled_by"] or "local-admin"),
                )
                return
            cursor = connection.execute(
                """
                UPDATE conversion_job
                SET status = 'FAILED', progress = 100, status_message = ?,
                    error_json = ?, finished_at = ?, revision = revision + 1
                WHERE id = ? AND status IN ('QUEUED', 'RUNNING')
                """,
                (
                    str(error.get("message") or "转换执行失败")[:240],
                    canonical_json(error),
                    utc_now(),
                    job_id,
                ),
            )
            if cursor.rowcount != 1:
                return
            connection.execute(
                """
                UPDATE conversion_source SET security_status = 'PARSE_FAILED'
                WHERE job_id = ? AND security_status = 'READY_FOR_PARSE'
                """,
                (job_id,),
            )
            self._record_event_in_connection(
                connection,
                event_type="CONVERSION_FAILED",
                entity_type="conversion_job",
                entity_id=job_id,
                detail=error,
            )

    def is_conversion_cancel_requested(self, job_id: str) -> bool:
        self.initialize()
        with self.session() as connection:
            row = connection.execute(
                "SELECT cancel_requested_at FROM conversion_job WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"转换任务不存在：{job_id}")
        return row["cancel_requested_at"] is not None

    def request_conversion_cancel(
        self, job_id: str, *, actor: str = "local-admin"
    ) -> dict[str, Any]:
        self.initialize()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT status, cancel_requested_at FROM conversion_job WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"转换任务不存在：{job_id}")
            status = str(row["status"])
            if status == "CONFIRMED":
                raise ValueError("已确认转换任务不允许取消")
            if status == "CANCELLED":
                pass
            elif row["cancel_requested_at"] is None:
                now = utc_now()
                connection.execute(
                    """
                    UPDATE conversion_job
                    SET cancel_requested_at = ?, cancelled_by = ?,
                        status_message = ?, revision = revision + 1
                    WHERE id = ? AND status = ?
                    """,
                    (
                        now,
                        actor,
                        "取消请求已记录，等待当前步骤结束"
                        if status == "RUNNING"
                        else "取消请求已生效",
                        job_id,
                        status,
                    ),
                )
                self._record_event_in_connection(
                    connection,
                    event_type="CONVERSION_CANCEL_REQUESTED",
                    entity_type="conversion_job",
                    entity_id=job_id,
                    detail={"old_status": status},
                    actor=actor,
                )
            if status not in {"RUNNING", "CANCELLED"}:
                now = utc_now()
                connection.execute(
                    """
                    UPDATE conversion_job
                    SET status = 'CANCELLED', progress = 100,
                        status_message = '任务已取消', cancelled_at = ?,
                        finished_at = coalesce(finished_at, ?),
                        revision = revision + 1
                    WHERE id = ? AND status <> 'CANCELLED'
                    """,
                    (now, now, job_id),
                )
                self._record_event_in_connection(
                    connection,
                    event_type="CONVERSION_CANCELLED",
                    entity_type="conversion_job",
                    entity_id=job_id,
                    detail={"old_status": status, "immediate": True},
                    actor=actor,
                )
        return self.get_conversion_job(job_id, detailed=False)

    def finalize_conversion_cancel(self, job_id: str) -> None:
        self.initialize()
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT status, cancel_requested_at, cancelled_by
                FROM conversion_job WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"转换任务不存在：{job_id}")
            if row["status"] == "CANCELLED":
                return
            if row["cancel_requested_at"] is None:
                raise ValueError("转换任务没有待生效的取消请求")
            now = utc_now()
            cursor = connection.execute(
                """
                UPDATE conversion_job
                SET status = 'CANCELLED', progress = 100,
                    status_message = '取消请求已生效', cancelled_at = ?,
                    finished_at = ?, revision = revision + 1
                WHERE id = ? AND status IN ('QUEUED', 'RUNNING')
                """,
                (now, now, job_id),
            )
            if cursor.rowcount:
                self._record_event_in_connection(
                    connection,
                    event_type="CONVERSION_CANCELLED",
                    entity_type="conversion_job",
                    entity_id=job_id,
                    detail={"old_status": str(row["status"]), "immediate": False},
                    actor=str(row["cancelled_by"] or "local-admin"),
                )

    def retry_conversion_job(
        self,
        job_id: str,
        *,
        review_decisions: dict[str, Any] | None = None,
        actor: str = "local-admin",
    ) -> str:
        job = self.get_conversion_job(job_id)
        if job["status"] not in {"BLOCKED", "FAILED"}:
            raise ValueError("只有阻断或失败的转换任务可以重试")
        sources = self.conversion_source_contents(job_id)
        for source in sources:
            original_source_id = str(source.get("id") or "")
            source["id"] = f"SOURCE-{uuid4()}"
            if source.get("duplicate_of_source_id") is None and original_source_id:
                source["duplicate_of_source_id"] = original_source_id
            if source.get("security_status") in {"PARSED", "PARSE_FAILED"}:
                source["security_status"] = "READY_FOR_PARSE"
        decisions = review_decisions if review_decisions is not None else job["review_decisions"]
        quarantined = any(source.get("security_status") == "QUARANTINED" for source in sources)
        ready = any(source.get("security_status") == "READY_FOR_PARSE" for source in sources)
        initial_status = (
            "BLOCKED"
            if not ready
            or (
                quarantined
                and str(job.get("failure_policy") or "ALL_OR_NOTHING") == "ALL_OR_NOTHING"
            )
            else "QUEUED"
        )
        retry_id, _ = self.create_conversion_job(
            dedupe_key=str(job["dedupe_key"]),
            profile_id=str(job["profile_id"]),
            profile_version=str(job["profile_version"]),
            profile_sha256=str(job["profile_sha256"]),
            profile_path=str(job["profile_path"]),
            contract_id=str(job["contract_id"]),
            contract_version=str(job["contract_version"]),
            contract_sha256=str(job["contract_sha256"]),
            contract_path=str(job["contract_path"]),
            failure_policy=str(job.get("failure_policy") or "ALL_OR_NOTHING"),
            intake_rules_version=job.get("intake_rules_version"),
            file_manifest_sha256=job.get("file_manifest_sha256"),
            intake_issues=job.get("intake_issues") or [],
            converter_version=str(job["converter_version"]),
            sources=sources,
            case_id=job.get("case_id"),
            project_name=job.get("project_name"),
            review_decisions=decisions,
            batch_id=job.get("batch_id"),
            parent_job_id=job_id,
            retry_count=int(job["retry_count"]) + 1,
            actor=actor,
            force=True,
            initial_status=initial_status,
        )
        self.record_event(
            event_type="CONVERSION_RETRIED",
            entity_type="conversion_job",
            entity_id=job_id,
            detail={"retry_job_id": retry_id, "retry_count": int(job["retry_count"]) + 1},
            actor=actor,
        )
        return retry_id

    def requeue_interrupted_conversions(self) -> list[str]:
        """Recover process-local workers that were interrupted by a restart."""
        self.initialize()
        with self.transaction() as connection:
            cancelled_rows = connection.execute(
                """
                SELECT id, cancelled_by FROM conversion_job
                WHERE status = 'RUNNING' AND cancel_requested_at IS NOT NULL
                """
            ).fetchall()
            cancelled_ids = {str(row["id"]) for row in cancelled_rows}
            now = utc_now()
            for row in cancelled_rows:
                job_id = str(row["id"])
                connection.execute(
                    """
                    UPDATE conversion_job
                    SET status = 'CANCELLED', progress = 100,
                        status_message = '服务重启时完成取消', cancelled_at = ?,
                        finished_at = ?, revision = revision + 1
                    WHERE id = ? AND status = 'RUNNING'
                    """,
                    (now, now, job_id),
                )
                self._record_event_in_connection(
                    connection,
                    event_type="CONVERSION_CANCELLED",
                    entity_type="conversion_job",
                    entity_id=job_id,
                    detail={"reason": "service_restart"},
                    actor=str(row["cancelled_by"] or "local-admin"),
                )
            rows = connection.execute(
                """
                SELECT id FROM conversion_job
                WHERE status IN ('QUEUED', 'RUNNING') AND cancel_requested_at IS NULL
                """
            ).fetchall()
            job_ids = [str(row["id"]) for row in rows if str(row["id"]) not in cancelled_ids]
            if job_ids:
                connection.execute(
                    """
                    UPDATE conversion_job
                    SET status = 'QUEUED', progress = 0,
                        status_message = '服务重启后重新排队', started_at = NULL,
                        revision = revision + 1
                    WHERE status IN ('QUEUED', 'RUNNING') AND cancel_requested_at IS NULL
                    """
                )
                for job_id in job_ids:
                    self._record_event_in_connection(
                        connection,
                        event_type="CONVERSION_REQUEUED",
                        entity_type="conversion_job",
                        entity_id=job_id,
                        detail={"reason": "service_restart"},
                    )
        return job_ids

    def confirm_conversion(
        self,
        job_id: str,
        *,
        name: str,
        reviewer: str,
        reason: str,
    ) -> tuple[str, bool]:
        """Atomically confirm a preview and create/link its immutable snapshot."""
        self.initialize()
        confirmed_at = utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM conversion_job WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"转换任务不存在：{job_id}")
            if row["status"] == "CONFIRMED" and row["snapshot_id"]:
                return str(row["snapshot_id"]), False
            if row["status"] != "READY_FOR_CONFIRMATION":
                raise ValueError("转换任务尚未达到可确认状态")
            if not row["payload_json"] or not row["case_sha256"]:
                raise ValueError("转换任务缺少待确认JSON")
            payload_text = str(row["payload_json"])
            case = json.loads(payload_text)
            payload_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
            if payload_hash != str(row["case_sha256"]):
                raise ValueError("转换JSON哈希与任务记录不一致")
            existing = connection.execute(
                "SELECT id FROM input_snapshot WHERE payload_sha256 = ?",
                (payload_hash,),
            ).fetchone()
            created = existing is None
            if existing is None:
                snapshot_id = self._insert_snapshot_in_connection(
                    connection,
                    case,
                    name=name,
                    source_path=f"conversion://{job_id}",
                    payload_text=payload_text,
                    payload_hash=payload_hash,
                    actor=reviewer,
                )
            else:
                snapshot_id = str(existing["id"])
            source_manifest_text = str(row["source_manifest_json"] or "{}")
            connection.execute(
                """
                INSERT OR IGNORE INTO input_snapshot_provenance(
                    snapshot_id, conversion_job_id, converter_version,
                    mapping_profile_id, mapping_version, mapping_sha256,
                    contract_id, contract_version, contract_sha256,
                    source_manifest_json, case_sha256, confirmed_by, confirmed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    job_id,
                    str(row["converter_version"]),
                    str(row["profile_id"]),
                    str(row["profile_version"]),
                    str(row["profile_sha256"]),
                    str(row["contract_id"]),
                    str(row["contract_version"]),
                    str(row["contract_sha256"]),
                    source_manifest_text,
                    payload_hash,
                    reviewer,
                    confirmed_at,
                ),
            )
            connection.execute(
                """
                UPDATE conversion_job
                SET status = 'CONFIRMED', status_message = '已确认并写入不可变快照',
                    snapshot_id = ?, confirmed_by = ?, confirmed_at = ?,
                    revision = revision + 1
                WHERE id = ? AND status = 'READY_FOR_CONFIRMATION'
                    AND cancel_requested_at IS NULL
                """,
                (snapshot_id, reviewer, confirmed_at, job_id),
            )
            self._record_event_in_connection(
                connection,
                event_type="CONVERSION_CONFIRMED",
                entity_type="conversion_job",
                entity_id=job_id,
                detail={
                    "snapshot_id": snapshot_id,
                    "snapshot_created": created,
                    "case_sha256": payload_hash,
                    "reason": reason,
                },
                actor=reviewer,
            )
        return snapshot_id, created

    def load_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        self.initialize()
        with self.session() as connection:
            row = connection.execute(
                "SELECT payload_json FROM input_snapshot WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"输入快照不存在：{snapshot_id}")
        return json.loads(str(row["payload_json"]))

    def snapshot_document(self, snapshot_id: str) -> dict[str, Any]:
        metadata = self.snapshot_metadata(snapshot_id)
        metadata["payload"] = self.load_snapshot(snapshot_id)
        return metadata

    def latest_snapshot_id(self) -> str:
        self.initialize()
        with self.session() as connection:
            row = connection.execute(
                "SELECT id FROM input_snapshot ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            raise KeyError("数据库中还没有输入快照")
        return str(row["id"])

    def snapshot_metadata(self, snapshot_id: str) -> dict[str, Any]:
        self.initialize()
        with self.session() as connection:
            row = connection.execute(
                "SELECT * FROM input_snapshot WHERE id = ?", (snapshot_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"输入快照不存在：{snapshot_id}")
            counts = {
                "segments": connection.execute(
                    "SELECT count(*) FROM input_segment WHERE snapshot_id = ?",
                    (snapshot_id,),
                ).fetchone()[0],
                "indicators": connection.execute(
                    "SELECT count(*) FROM input_indicator_observation WHERE snapshot_id = ?",
                    (snapshot_id,),
                ).fetchone()[0],
                "population_receptors": connection.execute(
                    "SELECT count(*) FROM input_population_receptor WHERE snapshot_id = ?",
                    (snapshot_id,),
                ).fetchone()[0],
                "raw_records": connection.execute(
                    "SELECT count(*) FROM input_raw_record WHERE snapshot_id = ?",
                    (snapshot_id,),
                ).fetchone()[0],
            }
            provenance = connection.execute(
                """
                SELECT conversion_job_id, converter_version, mapping_profile_id,
                       mapping_version, mapping_sha256, source_manifest_json,
                       case_sha256, confirmed_by, confirmed_at
                FROM input_snapshot_provenance WHERE snapshot_id = ?
                """,
                (snapshot_id,),
            ).fetchone()
        result = dict(row)
        result.pop("payload_json", None)
        result["counts"] = counts
        result["conversion"] = None
        if provenance is not None:
            result["conversion"] = dict(provenance)
            result["conversion"]["source_manifest"] = json.loads(
                result["conversion"].pop("source_manifest_json")
            )
        return result

    def list_snapshots(self) -> list[dict[str, Any]]:
        self.initialize()
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT
                    s.id, s.name, s.source_path, s.schema_version,
                    s.payload_sha256, s.created_at,
                    p.conversion_job_id, p.converter_version,
                    p.mapping_profile_id, p.mapping_version, p.mapping_sha256,
                    (SELECT count(*) FROM input_segment x
                     WHERE x.snapshot_id = s.id) AS segment_count,
                    (SELECT count(*) FROM input_indicator_observation x
                     WHERE x.snapshot_id = s.id) AS indicator_count,
                    (SELECT count(*) FROM input_population_receptor x
                     WHERE x.snapshot_id = s.id) AS receptor_count,
                    (SELECT count(*) FROM input_raw_record x
                     WHERE x.snapshot_id = s.id) AS raw_record_count
                FROM input_snapshot s
                LEFT JOIN input_snapshot_provenance p ON p.snapshot_id = s.id
                ORDER BY s.created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_snapshot(self, snapshot_id: str) -> None:
        """Delete an unused input snapshot; calculated history stays immutable."""
        self.initialize()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT name FROM input_snapshot WHERE id = ?", (snapshot_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"输入快照不存在：{snapshot_id}")
            run_count = int(
                connection.execute(
                    "SELECT count(*) FROM calculation_run WHERE snapshot_id = ?",
                    (snapshot_id,),
                ).fetchone()[0]
            )
            if run_count:
                raise ValueError("该输入快照已有计算记录，为保证审计链不能删除")
            conversion_count = int(
                connection.execute(
                    "SELECT count(*) FROM input_snapshot_provenance WHERE snapshot_id = ?",
                    (snapshot_id,),
                ).fetchone()[0]
            )
            if conversion_count:
                raise ValueError("该输入快照来自已确认转换，为保证审计链不能删除")
            connection.execute("DELETE FROM input_snapshot WHERE id = ?", (snapshot_id,))
            self._record_event_in_connection(
                connection,
                event_type="SNAPSHOT_DELETED",
                entity_type="input_snapshot",
                entity_id=snapshot_id,
                detail={"name": str(row["name"])},
            )

    def create_run(self, snapshot_id: str, input_sha256: str) -> str:
        run_id = f"RUN-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO calculation_run(
                    id, snapshot_id, status, input_sha256, created_at
                ) VALUES (?, ?, 'QUEUED', ?, ?)
                """,
                (run_id, snapshot_id, input_sha256, utc_now()),
            )
            self._record_event_in_connection(
                connection,
                event_type="RUN_QUEUED",
                entity_type="calculation_run",
                entity_id=run_id,
                detail={"snapshot_id": snapshot_id},
            )
        return run_id

    def set_run_running(self, run_id: str, engine_version: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE calculation_run
                SET status = 'RUNNING', engine_version = ?, started_at = ?
                WHERE id = ?
                """,
                (engine_version, utc_now(), run_id),
            )
            self._record_event_in_connection(
                connection,
                event_type="RUN_STARTED",
                entity_type="calculation_run",
                entity_id=run_id,
                detail={"engine_version": engine_version},
            )

    def fail_run(self, run_id: str, message: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE calculation_run
                SET status = 'FAILED', error_message = ?, finished_at = ?
                WHERE id = ?
                """,
                (message, utc_now(), run_id),
            )
            self._record_event_in_connection(
                connection,
                event_type="RUN_FAILED",
                entity_type="calculation_run",
                entity_id=run_id,
                detail={"error": message},
            )

    @staticmethod
    def _content_type(path: Path) -> str:
        return {
            ".json": "application/json; charset=utf-8",
            ".csv": "text/csv; charset=utf-8",
            ".html": "text/html; charset=utf-8",
            ".svg": "image/svg+xml; charset=utf-8",
            ".txt": "text/plain; charset=utf-8",
        }.get(path.suffix.lower(), "application/octet-stream")

    @staticmethod
    def _json_file(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _source_ranking(output_dir: Path, capability: dict[str, Any]) -> dict[str, dict[str, Any]]:
        source_node_id = capability.get("risk_result", {}).get("source_node_id")
        if not source_node_id:
            return {}
        source_path = output_dir / "nodes" / f"{source_node_id}.json"
        if not source_path.exists():
            return {}
        source = QraDatabase._json_file(source_path)
        ranking = source.get("human_risk", {}).get("segment_risk", {}).get("ranking", [])
        return {
            str(row["segment_id"]): row
            for row in ranking
            if isinstance(row, dict) and row.get("segment_id") is not None
        }

    def complete_run(
        self,
        run_id: str,
        *,
        output_dir: Path | str,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically persist node results, normalized risks, and every artifact."""
        root = Path(output_dir).resolve()
        capability_path = root / "capability_report.json"
        capability = self._json_file(capability_path) if capability_path.exists() else {}
        risk_matrix_path = root / "nodes" / "risk_matrix.json"
        risk_matrix = self._json_file(risk_matrix_path) if risk_matrix_path.exists() else {}
        source_ranking = self._source_ranking(root, capability)
        artifacts = [path for path in root.rglob("*") if path.is_file()]
        total_pll = math.fsum(
            float(row.get("pll_per_year", 0.0))
            for row in sorted(
                risk_matrix.get("segments", []),
                key=lambda item: str(item.get("segment_id", "")),
            )
        )
        summary = {
            "dynamic_status": manifest.get("status"),
            "result_tier": capability.get("risk_result", {}).get("result_tier"),
            "risk_result_available": bool(
                capability.get("risk_result", {}).get("available", False)
            ),
            "formal_acceptance_judgement_allowed": bool(
                capability.get("risk_result", {}).get("formal_acceptance_judgement_allowed", False)
            ),
            "completed_node_count": len(capability.get("completed_node_ids", [])),
            "skipped_node_count": len(capability.get("skipped_node_ids", [])),
            "failed_node_count": len(capability.get("failed_node_ids", [])),
            "segment_result_count": len(risk_matrix.get("segments", [])),
            "total_pll_per_year": total_pll,
            "dashboard_artifact": manifest.get("dashboard", "report_dashboard.html"),
            "numerical_result_sha256": manifest.get("numerical_result_sha256"),
            "audit_manifest_sha256": manifest.get("audit_manifest_sha256"),
        }
        result_hash = manifest.get("numerical_result_sha256")
        if not result_hash:
            node_documents = {
                path.stem: self._json_file(path) for path in sorted((root / "nodes").glob("*.json"))
            }
            result_hash = json_sha256(
                {
                    "schema_version": manifest.get("schema_version"),
                    "node_results": node_documents,
                }
            )
            summary["numerical_result_sha256"] = result_hash

        with self.transaction() as connection:
            exists = connection.execute(
                "SELECT 1 FROM calculation_run WHERE id = ?", (run_id,)
            ).fetchone()
            if exists is None:
                raise KeyError(f"计算任务不存在：{run_id}")

            connection.execute("DELETE FROM calculation_node WHERE run_id = ?", (run_id,))
            connection.execute(
                "DELETE FROM calculation_result_document WHERE run_id = ?",
                (run_id,),
            )
            connection.execute(
                "DELETE FROM calculation_segment_result WHERE run_id = ?",
                (run_id,),
            )
            connection.execute("DELETE FROM calculation_artifact WHERE run_id = ?", (run_id,))

            for node in manifest.get("nodes", []):
                connection.execute(
                    """
                    INSERT INTO calculation_node(
                        run_id, node_id, sequence_no, label_zh, standard_ref,
                        status, missing_inputs_json, blocked_dependencies_json,
                        result_path, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        str(node["node_id"]),
                        int(node.get("sequence", 0)),
                        node.get("label_zh"),
                        node.get("standard"),
                        str(node.get("status", "UNKNOWN")),
                        canonical_json(node.get("missing_inputs", [])),
                        canonical_json(node.get("blocked_dependencies", [])),
                        node.get("result"),
                        node.get("runtime_error"),
                    ),
                )

            node_dir = root / "nodes"
            if node_dir.exists():
                for result_path in sorted(node_dir.glob("*.json")):
                    result = self._json_file(result_path)
                    connection.execute(
                        """
                        INSERT INTO calculation_result_document(
                            run_id, node_id, schema_version, result_json,
                            result_sha256
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            result_path.stem,
                            result.get("schema_version"),
                            canonical_json(result),
                            json_sha256(result),
                        ),
                    )

            ranked_segments = sorted(
                risk_matrix.get("segments", []),
                key=lambda row: (
                    -float(row.get("pll_per_year", 0.0)),
                    str(row.get("segment_id", "")),
                ),
            )
            for rank, matrix_row in enumerate(ranked_segments, start=1):
                segment_code = str(matrix_row["segment_id"])
                source_row = source_ranking.get(segment_code, {})
                diagnostics = source_row.get("evidence_diagnostics", {}) or {}
                connection.execute(
                    """
                    INSERT INTO calculation_segment_result(
                        run_id, segment_code, risk_rank, pll_per_year,
                        risk_lower_bound, risk_upper_bound,
                        risk_density_per_km_year,
                        initiating_frequency_per_year, maximum_ir_per_year,
                        evidence_factor, evidence_coverage, uncertainty_factor,
                        display_risk_band, dominant_scenario_json, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        segment_code,
                        rank,
                        float(matrix_row.get("pll_per_year", 0.0)),
                        source_row.get("risk_value_lower_screening_bound"),
                        source_row.get("risk_value_upper_screening_bound"),
                        source_row.get("risk_density_fatalities_per_km_year"),
                        matrix_row.get("initiating_failure_frequency_per_year"),
                        matrix_row.get("maximum_individual_risk_per_year"),
                        source_row.get("evidence_factor"),
                        diagnostics.get("coverage_fraction"),
                        source_row.get("uncertainty_factor"),
                        matrix_row.get("display_risk_band"),
                        canonical_json(source_row.get("dominant_risk_scenario")),
                        canonical_json({"risk_matrix": matrix_row, "source_result": source_row}),
                    ),
                )

            for artifact_path in sorted(artifacts):
                relative = artifact_path.relative_to(root).as_posix()
                content = artifact_path.read_bytes()
                connection.execute(
                    """
                    INSERT INTO calculation_artifact(
                        run_id, path, content_type, content, byte_count, sha256
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        relative,
                        self._content_type(artifact_path),
                        content,
                        len(content),
                        bytes_sha256(content),
                    ),
                )

            connection.execute(
                """
                UPDATE calculation_run
                SET status = 'COMPLETED', result_tier = ?, result_sha256 = ?,
                    summary_json = ?, error_message = NULL, finished_at = ?
                WHERE id = ?
                """,
                (
                    capability.get("risk_result", {}).get("result_tier"),
                    result_hash,
                    canonical_json(summary),
                    utc_now(),
                    run_id,
                ),
            )
            self._record_event_in_connection(
                connection,
                event_type="RUN_COMPLETED",
                entity_type="calculation_run",
                entity_id=run_id,
                detail=summary,
            )
        return summary

    def list_runs(self) -> list[dict[str, Any]]:
        self.initialize()
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT r.*, s.name AS snapshot_name
                FROM calculation_run r
                JOIN input_snapshot s ON s.id = r.snapshot_id
                ORDER BY r.created_at DESC
                """
            ).fetchall()
        return [self._decode_run_row(row) for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any]:
        self.initialize()
        with self.session() as connection:
            row = connection.execute(
                """
                SELECT r.*, s.name AS snapshot_name
                FROM calculation_run r
                JOIN input_snapshot s ON s.id = r.snapshot_id
                WHERE r.id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"计算任务不存在：{run_id}")
        return self._decode_run_row(row)

    @staticmethod
    def _decode_run_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        if result.get("summary_json"):
            result["summary"] = json.loads(result.pop("summary_json"))
        else:
            result.pop("summary_json", None)
            result["summary"] = None
        return result

    def latest_run_id(self) -> str:
        self.initialize()
        with self.session() as connection:
            row = connection.execute(
                "SELECT id FROM calculation_run ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            raise KeyError("数据库中还没有计算任务")
        return str(row["id"])

    def list_nodes(self, run_id: str) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT * FROM calculation_node
                WHERE run_id = ? ORDER BY sequence_no, node_id
                """,
                (run_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["missing_inputs"] = json.loads(item.pop("missing_inputs_json"))
            item["blocked_dependencies"] = json.loads(item.pop("blocked_dependencies_json"))
            result.append(item)
        return result

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT path, content_type, byte_count, sha256
                FROM calculation_artifact
                WHERE run_id = ? ORDER BY path
                """,
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_artifact(self, run_id: str, path: str) -> tuple[str, bytes] | None:
        with self.session() as connection:
            row = connection.execute(
                """
                SELECT content_type, content
                FROM calculation_artifact
                WHERE run_id = ? AND path = ?
                """,
                (run_id, path),
            ).fetchone()
        if row is None:
            return None
        return str(row["content_type"]), bytes(row["content"])

    def get_segment_results(self, run_id: str) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT * FROM calculation_segment_result
                WHERE run_id = ?
                ORDER BY risk_rank IS NULL, risk_rank, segment_code
                """,
                (run_id,),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["dominant_scenario"] = json.loads(item.pop("dominant_scenario_json") or "null")
            item.pop("payload_json", None)
            results.append(item)
        return results

    def get_result_document(self, run_id: str, node_id: str) -> dict[str, Any]:
        with self.session() as connection:
            row = connection.execute(
                """
                SELECT result_json FROM calculation_result_document
                WHERE run_id = ? AND node_id = ?
                """,
                (run_id, node_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"节点结果不存在：{run_id}/{node_id}")
        return json.loads(str(row["result_json"]))

    def overview(self) -> dict[str, Any]:
        self.initialize()
        with self.session() as connection:
            counts = {
                "conversion_count": int(
                    connection.execute("SELECT count(*) FROM conversion_job").fetchone()[0]
                ),
                "active_conversion_count": int(
                    connection.execute(
                        "SELECT count(*) FROM conversion_job WHERE status IN ('QUEUED','RUNNING')"
                    ).fetchone()[0]
                ),
                "blocked_conversion_count": int(
                    connection.execute(
                        "SELECT count(*) FROM conversion_job WHERE status IN ('BLOCKED','FAILED')"
                    ).fetchone()[0]
                ),
                "snapshot_count": int(
                    connection.execute("SELECT count(*) FROM input_snapshot").fetchone()[0]
                ),
                "run_count": int(
                    connection.execute("SELECT count(*) FROM calculation_run").fetchone()[0]
                ),
                "completed_run_count": int(
                    connection.execute(
                        "SELECT count(*) FROM calculation_run WHERE status = 'COMPLETED'"
                    ).fetchone()[0]
                ),
                "active_run_count": int(
                    connection.execute(
                        "SELECT count(*) FROM calculation_run WHERE status IN ('QUEUED','RUNNING')"
                    ).fetchone()[0]
                ),
                "failed_run_count": int(
                    connection.execute(
                        "SELECT count(*) FROM calculation_run WHERE status = 'FAILED'"
                    ).fetchone()[0]
                ),
                "stored_artifact_bytes": int(
                    connection.execute(
                        "SELECT coalesce(sum(byte_count), 0) FROM calculation_artifact"
                    ).fetchone()[0]
                ),
            }
            band_rows = connection.execute(
                """
                SELECT display_risk_band, count(*) AS count
                FROM calculation_segment_result
                WHERE run_id = (
                    SELECT id FROM calculation_run
                    WHERE status = 'COMPLETED'
                    ORDER BY created_at DESC LIMIT 1
                )
                GROUP BY display_risk_band
                """
            ).fetchall()
        counts["database_bytes"] = self.path.stat().st_size if self.path.exists() else 0
        counts["latest_risk_band_counts"] = {
            str(row["display_risk_band"] or "UNKNOWN"): int(row["count"]) for row in band_rows
        }
        return counts

    def list_audit_events(self, limit: int = 100) -> list[dict[str, Any]]:
        self.initialize()
        safe_limit = max(1, min(int(limit), 500))
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT * FROM audit_event
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["detail"] = json.loads(item.pop("detail_json"))
            result.append(item)
        return result

    def table_overview(self) -> list[dict[str, Any]]:
        self.initialize()
        with self.session() as connection:
            rows = []
            for table in ADMIN_BROWSABLE_TABLES:
                count = int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
                columns = [
                    str(column["name"])
                    for column in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
                ]
                rows.append({"table": table, "row_count": count, "columns": columns})
        return rows

    def browse_table(self, table: str, limit: int = 100) -> dict[str, Any]:
        if table not in ADMIN_BROWSABLE_TABLES:
            raise ValueError("该数据表不允许通过管理页面访问")
        safe_limit = max(1, min(int(limit), 500))
        self.initialize()
        with self.session() as connection:
            columns = [
                str(column["name"])
                for column in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            ]
            rows = connection.execute(
                f'SELECT * FROM "{table}" ORDER BY rowid DESC LIMIT ?',
                (safe_limit,),
            ).fetchall()
        hidden_suffixes = ("_json",)
        hidden_names = {"payload_json", "content", "result_json"}
        visible_columns = [
            column
            for column in columns
            if column not in hidden_names and not column.endswith(hidden_suffixes)
        ]
        return {
            "table": table,
            "columns": visible_columns,
            "rows": [{column: row[column] for column in visible_columns} for row in rows],
            "limit": safe_limit,
            "sensitive_or_large_fields_hidden": True,
        }


__all__ = [
    "ADMIN_BROWSABLE_TABLES",
    "QraDatabase",
    "SCHEMA_VERSION",
    "bytes_sha256",
    "canonical_json",
    "json_sha256",
    "utc_now",
]
