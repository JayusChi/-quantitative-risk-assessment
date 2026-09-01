from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = "2.1.0"

ADMIN_BROWSABLE_TABLES = (
    "conversion_job",
    "conversion_source",
    "conversion_parse_artifact",
    "extraction_run",
    "model_call_audit",
    "extracted_entity",
    "candidate_field",
    "candidate_evidence_link",
    "candidate_relationship",
    "quality_issue",
    "fusion_group",
    "fusion_group_member",
    "review_session",
    "review_decision",
    "review_gate_run",
    "reextraction_request",
    "input_snapshot_review_provenance",
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


def normalize_artifact_path(value: str) -> str:
    raw = str(value).replace("\\", "/")
    path = PurePosixPath(raw)
    if (
        not raw
        or raw.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(":" in part or any(ord(character) < 32 for character in part) for part in path.parts)
    ):
        raise ValueError("报告资源路径无效")
    return path.as_posix()


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
            external_sharing_allowed INTEGER NOT NULL DEFAULT 0,
            ocr_provider_id TEXT,
            ocr_model_version TEXT,
            extraction_provider_id TEXT,
            extraction_model_version TEXT,
            pilot_id TEXT,
            pilot_version TEXT,
            pilot_manifest_sha256 TEXT,
            target_node_ids_json TEXT,
            source_count INTEGER NOT NULL,
            source_bytes INTEGER NOT NULL,
            review_decisions_json TEXT,
            payload_json TEXT,
            case_sha256 TEXT,
            source_manifest_json TEXT,
            conversion_report_json TEXT,
            preview_json TEXT,
            review_audit_json TEXT,
            stage4_status TEXT,
            stage4_result_sha256 TEXT,
            stage4_metrics_json TEXT,
            stage4_capability_json TEXT,
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

        CREATE TABLE IF NOT EXISTS extraction_run (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES conversion_job(id) ON DELETE CASCADE,
            step TEXT NOT NULL,
            task_type TEXT,
            status TEXT NOT NULL,
            provider_id TEXT,
            model_id TEXT,
            model_version TEXT,
            prompt_template_version TEXT,
            schema_sha256 TEXT,
            input_sha256 TEXT NOT NULL,
            output_sha256 TEXT NOT NULL,
            raw_response_sha256 TEXT,
            provider_request_id TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            input_json TEXT,
            output_json TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            UNIQUE(job_id, step)
        );

        CREATE TABLE IF NOT EXISTS model_call_audit (
            id TEXT PRIMARY KEY,
            job_id TEXT REFERENCES conversion_job(id) ON DELETE CASCADE,
            source_id TEXT,
            call_kind TEXT NOT NULL,
            task_type TEXT NOT NULL,
            logical_request_id TEXT NOT NULL,
            parent_call_id TEXT,
            page_number INTEGER,
            region_id TEXT,
            tile_id TEXT,
            attempt_number INTEGER NOT NULL,
            provider_id TEXT,
            model_version TEXT,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            elapsed_ms INTEGER,
            input_sha256 TEXT NOT NULL,
            input_byte_count INTEGER NOT NULL,
            media_sha256 TEXT,
            media_content_type TEXT,
            width INTEGER,
            height INTEGER,
            payload_policy_version TEXT,
            provider_request_id TEXT,
            raw_response_sha256 TEXT,
            retryable INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            sanitized_error_message TEXT,
            usage_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS extracted_entity (
            job_id TEXT NOT NULL REFERENCES conversion_job(id) ON DELETE CASCADE,
            entity_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            business_key TEXT,
            normalized_name TEXT,
            confidence REAL NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (job_id, entity_id)
        );

        CREATE TABLE IF NOT EXISTS candidate_field (
            job_id TEXT NOT NULL REFERENCES conversion_job(id) ON DELETE CASCADE,
            candidate_id TEXT NOT NULL,
            field_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            extraction_method TEXT NOT NULL,
            confidence REAL NOT NULL,
            quality_status TEXT NOT NULL,
            review_status TEXT NOT NULL,
            source_unit TEXT,
            canonical_unit TEXT,
            normalized_value_json TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (job_id, candidate_id)
        );

        CREATE TABLE IF NOT EXISTS candidate_evidence_link (
            job_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            PRIMARY KEY (job_id, candidate_id, evidence_id),
            FOREIGN KEY (job_id, candidate_id)
                REFERENCES candidate_field(job_id, candidate_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS candidate_relationship (
            job_id TEXT NOT NULL REFERENCES conversion_job(id) ON DELETE CASCADE,
            relationship_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            source_entity_id TEXT NOT NULL,
            target_entity_id TEXT NOT NULL,
            confidence REAL NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (job_id, relationship_id)
        );

        CREATE TABLE IF NOT EXISTS quality_issue (
            job_id TEXT NOT NULL REFERENCES conversion_job(id) ON DELETE CASCADE,
            issue_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            code TEXT NOT NULL,
            blocking INTEGER NOT NULL,
            field_id TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (job_id, issue_id)
        );

        CREATE TABLE IF NOT EXISTS fusion_group (
            job_id TEXT NOT NULL REFERENCES conversion_job(id) ON DELETE CASCADE,
            fusion_group_id TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            field_id TEXT,
            group_type TEXT NOT NULL,
            candidate_set_sha256 TEXT NOT NULL,
            proposed_candidate_id TEXT,
            confirmed_candidate_id TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (job_id, fusion_group_id)
        );

        CREATE TABLE IF NOT EXISTS fusion_group_member (
            job_id TEXT NOT NULL,
            fusion_group_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            PRIMARY KEY (job_id, fusion_group_id, candidate_id),
            FOREIGN KEY (job_id, fusion_group_id)
                REFERENCES fusion_group(job_id, fusion_group_id) ON DELETE CASCADE,
            FOREIGN KEY (job_id, candidate_id)
                REFERENCES candidate_field(job_id, candidate_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS review_session (
            id TEXT PRIMARY KEY,
            conversion_job_id TEXT NOT NULL REFERENCES conversion_job(id),
            status TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            candidate_set_hash TEXT NOT NULL,
            source_manifest_hash TEXT NOT NULL,
            target_node_ids_json TEXT NOT NULL,
            owner TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            gate_status TEXT,
            gate_result_hash TEXT,
            decision_set_hash TEXT,
            confirmed_snapshot_id TEXT REFERENCES input_snapshot(id),
            confirmed_run_id TEXT REFERENCES calculation_run(id),
            confirmed_at TEXT,
            superseded_by_session_id TEXT REFERENCES review_session(id)
        );

        CREATE TABLE IF NOT EXISTS review_decision (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES review_session(id),
            review_item_key TEXT NOT NULL,
            entity_id TEXT,
            field_id TEXT NOT NULL,
            action TEXT NOT NULL,
            selected_candidate_id TEXT,
            override_raw_value_json TEXT,
            override_normalized_value_json TEXT,
            override_unit TEXT,
            applicability_reason TEXT,
            reason TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            session_revision INTEGER NOT NULL,
            candidate_set_hash TEXT NOT NULL,
            decision_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            supersedes_decision_id TEXT REFERENCES review_decision(id),
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS review_gate_run (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES review_session(id),
            session_revision INTEGER NOT NULL,
            candidate_set_hash TEXT NOT NULL,
            decision_set_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            blocking_issue_count INTEGER NOT NULL,
            warning_count INTEGER NOT NULL,
            unresolved_field_count INTEGER NOT NULL,
            accepted_field_count INTEGER NOT NULL,
            overridden_field_count INTEGER NOT NULL,
            rejected_field_count INTEGER NOT NULL,
            not_applicable_count INTEGER NOT NULL,
            runnable_node_ids_json TEXT NOT NULL,
            blocked_node_ids_json TEXT NOT NULL,
            missing_inputs_json TEXT NOT NULL,
            assembled_payload_hash TEXT,
            result_hash TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reextraction_request (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES review_session(id),
            conversion_job_id TEXT NOT NULL REFERENCES conversion_job(id),
            scope TEXT NOT NULL DEFAULT 'FIELD',
            source_id TEXT,
            field_id TEXT NOT NULL,
            entity_id TEXT,
            page_number INTEGER,
            evidence_id TEXT,
            requested_parameters_json TEXT NOT NULL DEFAULT '{}',
            requested_by TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            error_json TEXT,
            base_parse_sha256 TEXT,
            result_parse_sha256 TEXT,
            replacement_extraction_run_id TEXT REFERENCES extraction_run(id)
        );

        CREATE TABLE IF NOT EXISTS conversion_parse_version (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES conversion_job(id) ON DELETE CASCADE,
            source_id TEXT NOT NULL,
            reextraction_request_id TEXT REFERENCES reextraction_request(id),
            scope TEXT NOT NULL,
            page_number INTEGER,
            parser_id TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            parse_sha256 TEXT NOT NULL,
            active INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS conversion_parse_artifact_version (
            version_id TEXT NOT NULL REFERENCES conversion_parse_version(id) ON DELETE CASCADE,
            path TEXT NOT NULL,
            artifact_kind TEXT NOT NULL,
            content_type TEXT NOT NULL,
            content BLOB NOT NULL,
            byte_count INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            PRIMARY KEY(version_id, path)
        );

        CREATE TABLE IF NOT EXISTS candidate_set_version (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES conversion_job(id) ON DELETE CASCADE,
            reextraction_request_id TEXT REFERENCES reextraction_request(id),
            candidate_set_sha256 TEXT NOT NULL,
            candidates_json TEXT NOT NULL,
            active INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS input_snapshot_review_provenance (
            id TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL REFERENCES input_snapshot(id),
            conversion_job_id TEXT NOT NULL REFERENCES conversion_job(id),
            review_session_id TEXT NOT NULL REFERENCES review_session(id),
            review_gate_run_id TEXT NOT NULL REFERENCES review_gate_run(id),
            source_manifest_hash TEXT NOT NULL,
            candidate_set_hash TEXT NOT NULL,
            decision_set_hash TEXT NOT NULL,
            mapping_version TEXT NOT NULL,
            mapping_sha256 TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            contract_sha256 TEXT NOT NULL,
            extraction_model_version TEXT,
            ocr_model_version TEXT,
            prompt_version TEXT,
            rule_version TEXT NOT NULL,
            decision_summary_json TEXT NOT NULL,
            confirmed_by TEXT NOT NULL,
            confirmation_reason TEXT NOT NULL,
            confirmed_at TEXT NOT NULL
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
            review_session_id TEXT REFERENCES review_session(id),
            review_provenance_id TEXT REFERENCES input_snapshot_review_provenance(id),
            target_node_ids_json TEXT,
            generate_charts INTEGER NOT NULL DEFAULT 1,
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
        CREATE INDEX IF NOT EXISTS idx_extraction_run_job
            ON extraction_run(job_id, step);
        CREATE INDEX IF NOT EXISTS idx_model_call_job_started
            ON model_call_audit(job_id, started_at);
        CREATE INDEX IF NOT EXISTS idx_model_call_logical
            ON model_call_audit(logical_request_id, attempt_number);
        CREATE INDEX IF NOT EXISTS idx_model_call_status
            ON model_call_audit(status, started_at);
        CREATE INDEX IF NOT EXISTS idx_candidate_filter
            ON candidate_field(job_id, quality_status, field_id, entity_key, candidate_id);
        CREATE INDEX IF NOT EXISTS idx_quality_issue_filter
            ON quality_issue(job_id, severity, code, issue_id);
        CREATE INDEX IF NOT EXISTS idx_fusion_group_job
            ON fusion_group(job_id, group_type, fusion_group_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_review_session_editable
            ON review_session(conversion_job_id)
            WHERE status IN ('OPEN', 'IN_REVIEW', 'READY_TO_CONFIRM');
        CREATE INDEX IF NOT EXISTS idx_review_session_job
            ON review_session(conversion_job_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_review_decision_session
            ON review_decision(session_id, review_item_key, created_at, id);
        CREATE INDEX IF NOT EXISTS idx_review_gate_session
            ON review_gate_run(session_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_reextraction_session
            ON reextraction_request(session_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_reextraction_status
            ON reextraction_request(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_parse_version_source
            ON conversion_parse_version(job_id, source_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_candidate_set_version_job
            ON candidate_set_version(job_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_snapshot_review_provenance
            ON input_snapshot_review_provenance(snapshot_id, confirmed_at DESC);
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

        CREATE TRIGGER IF NOT EXISTS trg_input_snapshot_business_immutable
        BEFORE UPDATE OF schema_version, payload_json, payload_sha256 ON input_snapshot
        BEGIN
            SELECT RAISE(ABORT, 'IMMUTABLE_INPUT_SNAPSHOT');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_review_decision_immutable_update
        BEFORE UPDATE ON review_decision
        BEGIN
            SELECT RAISE(ABORT, 'IMMUTABLE_REVIEW_DECISION');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_review_decision_immutable_delete
        BEFORE DELETE ON review_decision
        BEGIN
            SELECT RAISE(ABORT, 'IMMUTABLE_REVIEW_DECISION');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_review_gate_immutable_update
        BEFORE UPDATE ON review_gate_run
        BEGIN
            SELECT RAISE(ABORT, 'IMMUTABLE_REVIEW_GATE_RUN');
        END;
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
                    "external_sharing_allowed": "INTEGER NOT NULL DEFAULT 0",
                    "ocr_provider_id": "TEXT",
                    "ocr_model_version": "TEXT",
                    "extraction_provider_id": "TEXT",
                    "extraction_model_version": "TEXT",
                    "pilot_id": "TEXT",
                    "pilot_version": "TEXT",
                    "pilot_manifest_sha256": "TEXT",
                    "target_node_ids_json": "TEXT",
                    "stage4_status": "TEXT",
                    "stage4_result_sha256": "TEXT",
                    "stage4_metrics_json": "TEXT",
                    "stage4_capability_json": "TEXT",
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
                existing_reextraction_columns = {
                    str(row["name"])
                    for row in connection.execute(
                        'PRAGMA table_info("reextraction_request")'
                    ).fetchall()
                }
                reextraction_migration_columns = {
                    "scope": "TEXT NOT NULL DEFAULT 'FIELD'",
                    "page_number": "INTEGER",
                    "requested_parameters_json": "TEXT NOT NULL DEFAULT '{}'",
                    "started_at": "TEXT",
                    "error_json": "TEXT",
                    "base_parse_sha256": "TEXT",
                    "result_parse_sha256": "TEXT",
                }
                for column, declaration in reextraction_migration_columns.items():
                    if column not in existing_reextraction_columns:
                        connection.execute(
                            f'ALTER TABLE reextraction_request ADD COLUMN "{column}" {declaration}'
                        )
                existing_review_columns = {
                    str(row["name"])
                    for row in connection.execute(
                        'PRAGMA table_info("review_session")'
                    ).fetchall()
                }
                if "confirmed_run_id" not in existing_review_columns:
                    connection.execute(
                        'ALTER TABLE review_session ADD COLUMN "confirmed_run_id" TEXT'
                    )
                existing_run_columns = {
                    str(row["name"])
                    for row in connection.execute(
                        'PRAGMA table_info("calculation_run")'
                    ).fetchall()
                }
                run_migration_columns = {
                    "review_session_id": "TEXT",
                    "review_provenance_id": "TEXT",
                    "target_node_ids_json": "TEXT",
                    "generate_charts": "INTEGER NOT NULL DEFAULT 1",
                }
                for column, declaration in run_migration_columns.items():
                    if column not in existing_run_columns:
                        connection.execute(
                            f'ALTER TABLE calculation_run ADD COLUMN "{column}" {declaration}'
                        )
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_run_review_confirmation "
                    "ON calculation_run(review_session_id) WHERE review_session_id IS NOT NULL"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO db_schema(version, applied_at) VALUES (?, ?)",
                    (SCHEMA_VERSION, utc_now()),
                )
                connection.execute(
                    """
                    UPDATE model_call_audit
                    SET status = 'INTERRUPTED', finished_at = ?, retryable = 1,
                        error_code = coalesce(error_code, 'MODEL_CALL_PROCESS_INTERRUPTED'),
                        sanitized_error_message = coalesce(
                            sanitized_error_message, '进程恢复时发现未完成的模型调用'
                        )
                    WHERE status = 'STARTED'
                    """,
                    (utc_now(),),
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
        external_sharing_allowed: bool = False,
        ocr_provider_id: str | None = None,
        ocr_model_version: str | None = None,
        extraction_provider_id: str | None = None,
        extraction_model_version: str | None = None,
        pilot_id: str | None = None,
        pilot_version: str | None = None,
        pilot_manifest_sha256: str | None = None,
        target_node_ids: list[str] | None = None,
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
                    case_id, project_name, external_sharing_allowed,
                    ocr_provider_id, ocr_model_version,
                    extraction_provider_id, extraction_model_version,
                    pilot_id, pilot_version, pilot_manifest_sha256,
                    target_node_ids_json,
                    source_count, source_bytes,
                    review_decisions_json, retry_count, created_by, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, 0,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
                    int(bool(external_sharing_allowed)),
                    ocr_provider_id,
                    ocr_model_version,
                    extraction_provider_id,
                    extraction_model_version,
                    pilot_id,
                    pilot_version,
                    pilot_manifest_sha256,
                    canonical_json(target_node_ids) if target_node_ids else None,
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
                    "external_sharing_allowed": bool(external_sharing_allowed),
                    "ocr_provider_id": ocr_provider_id,
                    "ocr_model_version": ocr_model_version,
                    "extraction_provider_id": extraction_provider_id,
                    "extraction_model_version": extraction_model_version,
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
            "stage4_metrics_json",
            "stage4_capability_json",
            "target_node_ids_json",
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
                       external_sharing_allowed, ocr_provider_id, ocr_model_version,
                       extraction_provider_id,
                       extraction_model_version, pilot_id, pilot_version,
                       pilot_manifest_sha256, target_node_ids_json,
                       source_bytes, case_sha256,
                       snapshot_id, retry_count,
                       stage4_status, stage4_result_sha256,
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
            targets = item.pop("target_node_ids_json", None)
            item["target_node_ids"] = json.loads(str(targets)) if targets else []
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

    def get_extraction_step(
        self, job_id: str, step: str, input_sha256: str
    ) -> dict[str, Any] | None:
        self.initialize()
        with self.session() as connection:
            row = connection.execute(
                """
                SELECT step, status, input_sha256, output_sha256, output_json,
                       started_at, finished_at, retry_count, error_code
                FROM extraction_run
                WHERE job_id = ? AND step = ? AND input_sha256 = ?
                """,
                (job_id, step, input_sha256),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["output"] = json.loads(item.pop("output_json"))
        return item

    def record_model_call_audit(self, event: dict[str, Any]) -> None:
        """Upsert one sanitized attempt state; request/response bodies are never accepted."""

        self.initialize()
        call_id = str(event.get("id") or "")
        if not call_id or len(call_id) > 160:
            raise ValueError("模型调用审计ID无效")
        status = str(event.get("status") or "")
        if status not in {
            "STARTED",
            "COMPLETED",
            "FAILED",
            "INTERRUPTED",
            "CACHED",
            "SKIPPED",
        }:
            raise ValueError("模型调用审计状态无效")
        call_kind = str(event.get("call_kind") or "")
        if call_kind not in {"OCR", "EXTRACTION"}:
            raise ValueError("模型调用审计类型无效")
        message = str(event.get("sanitized_error_message") or "")
        message = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED_SECRET]", message)
        message = re.sub(r"https?://\S+", "[REDACTED_URL]", message)
        message = re.sub(
            r"(?:[A-Za-z]:[\\/]|/)(?:[^\s:]+[\\/])*[^\s:]*",
            "[REDACTED_PATH]",
            message,
        )[:300]
        usage_value = event.get("usage")
        usage = (
            {
                str(key)[:80]: value
                for key, value in usage_value.items()
                if isinstance(value, int | float) and not isinstance(value, bool)
            }
            if isinstance(usage_value, dict)
            else {}
        )
        input_hash = str(event.get("input_sha256") or "")
        if len(input_hash) != 64:
            input_hash = hashlib.sha256(input_hash.encode()).hexdigest()
        values = (
            call_id,
            event.get("job_id"),
            event.get("source_id"),
            call_kind,
            str(event.get("task_type") or call_kind)[:100],
            str(event.get("logical_request_id") or call_id)[:300],
            event.get("parent_call_id"),
            event.get("page_number"),
            event.get("region_id"),
            event.get("tile_id"),
            max(0, int(event.get("attempt_number", 0) or 0)),
            event.get("provider_id"),
            event.get("model_version"),
            status,
            str(event.get("started_at") or utc_now()),
            event.get("finished_at"),
            event.get("elapsed_ms"),
            input_hash,
            max(0, int(event.get("input_byte_count", 0) or 0)),
            event.get("media_sha256"),
            event.get("media_content_type"),
            event.get("width"),
            event.get("height"),
            event.get("payload_policy_version"),
            event.get("provider_request_id"),
            event.get("raw_response_sha256"),
            int(bool(event.get("retryable"))),
            event.get("error_code"),
            message or None,
            canonical_json(usage),
        )
        with self.session() as connection:
            connection.execute(
                """
                INSERT INTO model_call_audit(
                    id, job_id, source_id, call_kind, task_type,
                    logical_request_id, parent_call_id, page_number, region_id,
                    tile_id, attempt_number, provider_id, model_version, status,
                    started_at, finished_at, elapsed_ms, input_sha256,
                    input_byte_count, media_sha256, media_content_type, width,
                    height, payload_policy_version, provider_request_id,
                    raw_response_sha256, retryable, error_code,
                    sanitized_error_message, usage_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    finished_at = excluded.finished_at,
                    elapsed_ms = excluded.elapsed_ms,
                    provider_id = coalesce(excluded.provider_id, model_call_audit.provider_id),
                    model_version = coalesce(
                        excluded.model_version,
                        model_call_audit.model_version
                    ),
                    provider_request_id = excluded.provider_request_id,
                    raw_response_sha256 = excluded.raw_response_sha256,
                    retryable = excluded.retryable,
                    error_code = excluded.error_code,
                    sanitized_error_message = excluded.sanitized_error_message,
                    usage_json = excluded.usage_json
                """,
                values,
            )

    def list_conversion_model_calls(self, job_id: str) -> dict[str, Any]:
        """Return a text-free audit view of OCR and extraction provider calls."""

        self.initialize()
        with self.session() as connection:
            job = connection.execute(
                """
                SELECT external_sharing_allowed, ocr_provider_id, ocr_model_version,
                       extraction_provider_id, extraction_model_version
                FROM conversion_job WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if job is None:
                raise KeyError(f"转换任务不存在：{job_id}")
            extraction_rows = connection.execute(
                """
                SELECT id, step, status, output_json, started_at, finished_at,
                       retry_count, error_code
                FROM extraction_run
                WHERE job_id = ?
                ORDER BY started_at, step, id
                """,
                (job_id,),
            ).fetchall()
            ocr_rows = connection.execute(
                """
                SELECT artifact.source_id, artifact.content, artifact.created_at,
                       source.relative_path, source.parse_quality_json
                FROM conversion_parse_artifact AS artifact
                LEFT JOIN conversion_source AS source
                  ON source.job_id = artifact.job_id
                 AND source.id = artifact.source_id
                WHERE artifact.job_id = ? AND artifact.path = 'parsed_document.json'
                ORDER BY artifact.source_id
                """,
                (job_id,),
            ).fetchall()
            candidate_count = int(
                connection.execute(
                    "SELECT count(*) FROM candidate_field WHERE job_id = ?", (job_id,)
                ).fetchone()[0]
            )
            audit_rows = connection.execute(
                """
                SELECT audit.*, source.relative_path AS source_path
                FROM model_call_audit AS audit
                LEFT JOIN conversion_source AS source
                  ON source.job_id = audit.job_id AND source.id = audit.source_id
                WHERE audit.job_id = ?
                ORDER BY audit.started_at, audit.attempt_number, audit.id
                """,
                (job_id,),
            ).fetchall()

        def safe_text(value: object, limit: int = 240) -> str | None:
            if value is None:
                return None
            cleaned = "".join(character for character in str(value) if ord(character) >= 32)
            return cleaned[:limit] or None

        items: list[dict[str, Any]] = []
        for row in extraction_rows:
            try:
                output = json.loads(str(row["output_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                output = {}
            audits = output.get("model_calls", []) if isinstance(output, dict) else []
            for index, audit_value in enumerate(audits, start=1):
                if not isinstance(audit_value, dict):
                    continue
                usage_value = audit_value.get("usage")
                usage = (
                    {
                        str(key)[:80]: value
                        for key, value in usage_value.items()
                        if isinstance(value, int | float) and not isinstance(value, bool)
                    }
                    if isinstance(usage_value, dict)
                    else {}
                )
                status = safe_text(audit_value.get("status"), 80) or (
                    "FAILED" if row["error_code"] else "COMPLETED"
                )
                items.append(
                    {
                        "call_id": f"{row['id']}-{index}",
                        "call_kind": "EXTRACTION",
                        "step": str(row["step"]),
                        "task_type": safe_text(audit_value.get("task_type"), 80),
                        "status": status,
                        "workflow_status": str(row["status"]),
                        "provider_id": safe_text(
                            audit_value.get("provider_id") or job["extraction_provider_id"]
                        ),
                        "model_version": safe_text(
                            audit_value.get("model_version") or job["extraction_model_version"]
                        ),
                        "provider_request_id": safe_text(audit_value.get("provider_request_id")),
                        "retry_count": int(audit_value.get("retry_count", 0) or 0),
                        "repair_count": int(audit_value.get("repair_count", 0) or 0),
                        "error_code": safe_text(
                            audit_value.get("error_code") or row["error_code"], 120
                        ),
                        "finish_reason": safe_text(audit_value.get("finish_reason"), 80),
                        "prompt_template_version": safe_text(
                            audit_value.get("prompt_template_version"), 120
                        ),
                        "raw_response_sha256": safe_text(
                            audit_value.get("raw_response_sha256"), 64
                        ),
                        "usage": usage,
                        "source_id": None,
                        "source_path": None,
                        "cache_hit": False,
                        "started_at": str(row["started_at"]),
                        "finished_at": str(row["finished_at"]),
                    }
                )

        for row in ocr_rows:
            try:
                document = json.loads(bytes(row["content"]).decode("utf-8"))
            except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            metadata = document.get("metadata", {}) if isinstance(document, dict) else {}
            if not isinstance(metadata, dict):
                continue
            raw_calls: list[dict[str, Any]] = []
            single = metadata.get("ocr")
            if isinstance(single, dict) and single.get("provider_id"):
                raw_calls.append(single)
            multiple = metadata.get("ocr_calls")
            if isinstance(multiple, list):
                raw_calls.extend(item for item in multiple if isinstance(item, dict))
            try:
                quality = json.loads(str(row["parse_quality_json"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                quality = {}
            cache_hit = bool(quality.get("cache_hit")) if isinstance(quality, dict) else False
            for index, audit_value in enumerate(raw_calls, start=1):
                provider_id = safe_text(audit_value.get("provider_id") or job["ocr_provider_id"])
                model_version = safe_text(
                    audit_value.get("model_version") or job["ocr_model_version"]
                )
                if not provider_id and not model_version:
                    continue
                fingerprint = canonical_json(
                    {
                        "job_id": job_id,
                        "source_id": str(row["source_id"]),
                        "index": index,
                        "provider_request_id": audit_value.get("provider_request_id"),
                        "raw_response_sha256": audit_value.get("raw_response_sha256"),
                    }
                )
                items.append(
                    {
                        "call_id": "OCR-"
                        + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24],
                        "call_kind": "OCR",
                        "step": "OCR",
                        "task_type": "OCR",
                        "status": "CACHED" if cache_hit else "COMPLETED",
                        "workflow_status": "PARSED",
                        "provider_id": provider_id,
                        "model_version": model_version,
                        "provider_request_id": safe_text(audit_value.get("provider_request_id")),
                        "retry_count": 0,
                        "repair_count": 0,
                        "error_code": None,
                        "finish_reason": None,
                        "prompt_template_version": None,
                        "raw_response_sha256": safe_text(
                            audit_value.get("raw_response_sha256"), 64
                        ),
                        "usage": {},
                        "source_id": str(row["source_id"]),
                        "source_path": safe_text(row["relative_path"], 500),
                        "page_number": audit_value.get("page_number"),
                        "image_id": safe_text(audit_value.get("image_id"), 160),
                        "cache_hit": cache_hit,
                        "started_at": None,
                        "finished_at": str(row["created_at"]),
                    }
                )

        if audit_rows:
            items = []
            for row in audit_rows:
                try:
                    usage = json.loads(str(row["usage_json"] or "{}"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    usage = {}
                items.append(
                    {
                        "call_id": str(row["id"]),
                        "call_kind": str(row["call_kind"]),
                        "step": str(row["task_type"]),
                        "task_type": str(row["task_type"]),
                        "logical_request_id": str(row["logical_request_id"]),
                        "parent_call_id": row["parent_call_id"],
                        "status": str(row["status"]),
                        "workflow_status": None,
                        "provider_id": row["provider_id"],
                        "model_version": row["model_version"],
                        "provider_request_id": row["provider_request_id"],
                        "attempt_number": int(row["attempt_number"]),
                        "retry_count": max(0, int(row["attempt_number"]) - 1),
                        "repair_count": 0,
                        "error_code": row["error_code"],
                        "sanitized_error_message": row["sanitized_error_message"],
                        "finish_reason": None,
                        "prompt_template_version": None,
                        "raw_response_sha256": row["raw_response_sha256"],
                        "usage": usage if isinstance(usage, dict) else {},
                        "source_id": row["source_id"],
                        "source_path": safe_text(row["source_path"], 500),
                        "page_number": row["page_number"],
                        "region_id": row["region_id"],
                        "tile_id": row["tile_id"],
                        "input_byte_count": int(row["input_byte_count"]),
                        "media_sha256": row["media_sha256"],
                        "media_content_type": row["media_content_type"],
                        "width": row["width"],
                        "height": row["height"],
                        "payload_policy_version": row["payload_policy_version"],
                        "retryable": bool(row["retryable"]),
                        "cache_hit": str(row["status"]) == "CACHED",
                        "started_at": str(row["started_at"]),
                        "finished_at": (
                            str(row["finished_at"]) if row["finished_at"] else None
                        ),
                        "elapsed_ms": row["elapsed_ms"],
                    }
                )

        items.sort(
            key=lambda item: (
                str(item.get("finished_at") or item.get("started_at") or ""),
                str(item["call_kind"]),
                str(item["call_id"]),
            )
        )
        return {
            "conversion_id": job_id,
            "summary": {
                "record_count": len(items),
                "ocr_record_count": sum(item["call_kind"] == "OCR" for item in items),
                "extraction_record_count": sum(item["call_kind"] == "EXTRACTION" for item in items),
                "failed_record_count": sum(
                    item["status"] in {"FAILED", "INTERRUPTED", "STRUCTURE_FAILED"}
                    for item in items
                ),
                "cached_record_count": sum(bool(item.get("cache_hit")) for item in items),
                "candidate_count": candidate_count,
                "external_sharing_allowed": bool(job["external_sharing_allowed"]),
                "ocr_provider_id": job["ocr_provider_id"],
                "ocr_model_version": job["ocr_model_version"],
                "extraction_provider_id": job["extraction_provider_id"],
                "extraction_model_version": job["extraction_model_version"],
            },
            "items": items,
        }

    def save_extraction_step(self, job_id: str, result: dict[str, Any]) -> None:
        """Persist one immutable step output while the conversion remains runnable."""

        step = str(result["step"])
        output = dict(result["output"])
        calls = [item for item in output.get("model_calls", []) if isinstance(item, dict)]
        call = calls[0] if calls else {}
        run_id = "XRUN-" + hashlib.sha256(f"{job_id}\0{step}".encode()).hexdigest()[:24]
        with self.transaction() as connection:
            job = connection.execute(
                "SELECT status, cancel_requested_at FROM conversion_job WHERE id = ?",
                (job_id,),
            ).fetchone()
            if job is None:
                raise KeyError(f"转换任务不存在：{job_id}")
            if job["status"] != "RUNNING" or job["cancel_requested_at"]:
                raise ValueError("转换任务当前不允许固化提取步骤")
            existing = connection.execute(
                """
                SELECT input_sha256, output_sha256 FROM extraction_run
                WHERE job_id = ? AND step = ?
                """,
                (job_id, step),
            ).fetchone()
            if existing is not None:
                if str(existing["input_sha256"]) == str(result["input_sha256"]) and str(
                    existing["output_sha256"]
                ) == str(result["output_sha256"]):
                    return
                raise ValueError("已固化步骤不能写入不同输入或输出")
            connection.execute(
                """
                INSERT INTO extraction_run(
                    id, job_id, step, task_type, status, provider_id, model_id,
                    model_version, prompt_template_version, schema_sha256,
                    input_sha256, output_sha256, raw_response_sha256,
                    provider_request_id, retry_count, error_code, input_json,
                    output_json, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    job_id,
                    step,
                    call.get("task_type"),
                    str(result["status"]),
                    call.get("provider_id"),
                    call.get("model_id"),
                    call.get("model_version"),
                    call.get("prompt_template_version"),
                    call.get("schema_sha256"),
                    str(result["input_sha256"]),
                    str(result["output_sha256"]),
                    call.get("raw_response_sha256"),
                    call.get("provider_request_id"),
                    int(result.get("retry_count", 0)),
                    result.get("error_code") or call.get("error_code"),
                    None,
                    canonical_json(output),
                    str(result["started_at"]),
                    str(result["finished_at"]),
                ),
            )
            self._record_event_in_connection(
                connection,
                event_type="EXTRACTION_STEP_COMPLETED",
                entity_type="conversion_job",
                entity_id=job_id,
                detail={
                    "step": step,
                    "input_sha256": result["input_sha256"],
                    "output_sha256": result["output_sha256"],
                    "provider_id": call.get("provider_id"),
                    "model_version": call.get("model_version"),
                },
            )

    def apply_reextraction_result(
        self,
        request_id: str,
        *,
        document: dict[str, Any] | None,
        artifacts: list[dict[str, Any]],
        stage4_result: dict[str, Any],
        actor: str,
        partial: bool = False,
        replace_candidates: bool = True,
    ) -> dict[str, Any]:
        """Atomically version artifacts and replace only candidates inside the request scope."""

        def candidate_snapshot(connection: sqlite3.Connection, job_id: str) -> list[dict[str, Any]]:
            rows = connection.execute(
                "SELECT candidate_id, payload_json FROM candidate_field "
                "WHERE job_id = ? ORDER BY candidate_id",
                (job_id,),
            ).fetchall()
            result = []
            for row in rows:
                evidence = [
                    json.loads(str(link["evidence_json"]))
                    for link in connection.execute(
                        """
                        SELECT evidence_json FROM candidate_evidence_link
                        WHERE job_id = ? AND candidate_id = ? ORDER BY evidence_id
                        """,
                        (job_id, row["candidate_id"]),
                    ).fetchall()
                ]
                result.append(
                    {
                        "candidate": json.loads(str(row["payload_json"])),
                        "evidence": evidence,
                    }
                )
            return result

        safe_actor = str(actor).strip() or "reextraction-worker"
        with self.transaction() as connection:
            request = connection.execute(
                "SELECT * FROM reextraction_request WHERE id = ?", (request_id,)
            ).fetchone()
            if request is None:
                raise KeyError(f"重新提取请求不存在：{request_id}")
            if str(request["status"]) != "RUNNING":
                raise ValueError("重新提取请求不在运行中")
            job_id = str(request["conversion_job_id"])
            scope = str(request["scope"])
            source_id = str(request["source_id"] or "")
            page_number = int(request["page_number"] or 0)
            field_id = str(request["field_id"] or "")
            entity_id = str(request["entity_id"] or "")
            before = candidate_snapshot(connection, job_id)
            before_hash = json_sha256(before)
            connection.execute(
                "UPDATE candidate_set_version SET active = 0 WHERE job_id = ?",
                (job_id,),
            )
            connection.execute(
                """
                INSERT INTO candidate_set_version(
                    id, job_id, reextraction_request_id, candidate_set_sha256,
                    candidates_json, active, created_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    f"CSET-{uuid4()}",
                    job_id,
                    request_id,
                    before_hash,
                    canonical_json(before),
                    utc_now(),
                ),
            )

            entity_key = None
            if entity_id:
                entity_row = connection.execute(
                    "SELECT business_key FROM extracted_entity WHERE job_id = ? AND entity_id = ?",
                    (job_id, entity_id),
                ).fetchone()
                entity_key = (
                    str(entity_row["business_key"] or "") if entity_row else entity_id
                )

            def evidence_matches(value: dict[str, Any]) -> bool:
                location = value.get("location") or {}
                if str(location.get("file_id") or "") != source_id:
                    return False
                return scope != "PAGE" or int(location.get("page") or 0) == page_number

            target_ids: set[str] = set()
            candidate_rows = connection.execute(
                "SELECT candidate_id, field_id, entity_key FROM candidate_field WHERE job_id = ?",
                (job_id,),
            ).fetchall()
            for row in candidate_rows:
                candidate_id = str(row["candidate_id"])
                if scope == "FIELD":
                    if str(row["field_id"]) == field_id and (
                        not entity_key or str(row["entity_key"]) == entity_key
                    ):
                        target_ids.add(candidate_id)
                    continue
                links = connection.execute(
                    """
                    SELECT evidence_json FROM candidate_evidence_link
                    WHERE job_id = ? AND candidate_id = ?
                    """,
                    (job_id, candidate_id),
                ).fetchall()
                if any(evidence_matches(json.loads(str(link["evidence_json"]))) for link in links):
                    target_ids.add(candidate_id)

            if target_ids and replace_candidates:
                placeholders = ",".join("?" for _ in target_ids)
                parameters = (job_id, *sorted(target_ids))
                connection.execute(
                    "DELETE FROM fusion_group_member WHERE job_id = ? "
                    f"AND candidate_id IN ({placeholders})",
                    parameters,
                )
                connection.execute(
                    "DELETE FROM candidate_evidence_link WHERE job_id = ? "
                    f"AND candidate_id IN ({placeholders})",
                    parameters,
                )
                connection.execute(
                    "DELETE FROM candidate_field WHERE job_id = ? "
                    f"AND candidate_id IN ({placeholders})",
                    parameters,
                )
                connection.execute(
                    """
                    DELETE FROM fusion_group
                    WHERE job_id = ? AND NOT EXISTS(
                        SELECT 1 FROM fusion_group_member member
                        WHERE member.job_id = fusion_group.job_id
                          AND member.fusion_group_id = fusion_group.fusion_group_id
                    )
                    """,
                    (job_id,),
                )

            evidence = {
                str(item["evidence_id"]): dict(item)
                for item in stage4_result.get("evidence", [])
            }
            new_candidates = []
            for item_value in stage4_result.get("candidates", []):
                item = dict(item_value)
                if not item.get("evidence_ids"):
                    continue
                if scope == "FIELD":
                    matches = str(item.get("field_id")) == field_id and (
                        not entity_key
                        or str((item.get("entity") or {}).get("entity_key") or "") == entity_key
                    )
                else:
                    matches = any(
                        evidence_id in evidence and evidence_matches(evidence[evidence_id])
                        for evidence_id in item.get("evidence_ids", [])
                    )
                if matches:
                    new_candidates.append(item)
            if not replace_candidates:
                new_candidates = []
            for item in new_candidates:
                candidate_id = str(item["candidate_id"])
                connection.execute(
                    """
                    INSERT INTO candidate_field(
                        job_id, candidate_id, field_id, entity_type, entity_key,
                        extraction_method, confidence, quality_status, review_status,
                        source_unit, canonical_unit, normalized_value_json, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        candidate_id,
                        str(item["field_id"]),
                        str((item.get("entity") or {})["entity_type"]),
                        str((item.get("entity") or {})["entity_key"]),
                        str(item["extraction_method"]),
                        float(item["confidence"]),
                        str(item["quality_status"]),
                        str(item["review_status"]),
                        item.get("source_unit"),
                        item.get("canonical_unit"),
                        canonical_json(item.get("normalized_value")),
                        canonical_json(item),
                    ),
                )
                for evidence_id_value in sorted(set(item.get("evidence_ids") or [])):
                    evidence_id_value = str(evidence_id_value)
                    if evidence_id_value not in evidence:
                        raise ValueError("重提取候选引用了未持久化证据")
                    connection.execute(
                        """
                        INSERT INTO candidate_evidence_link(
                            job_id, candidate_id, evidence_id, evidence_json
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            job_id,
                            candidate_id,
                            evidence_id_value,
                            canonical_json(evidence[evidence_id_value]),
                        ),
                    )

            result_parse_sha256 = None
            if document is not None and source_id:
                result_parse_sha256 = str(document.get("parse_sha256") or "")
                if len(result_parse_sha256) != 64:
                    raise ValueError("重提取解析哈希无效")
                current_artifacts = {
                    str(row["path"]): dict(row)
                    for row in connection.execute(
                        """
                        SELECT path, artifact_kind, content_type, content, byte_count, sha256
                        FROM conversion_parse_artifact WHERE job_id = ? AND source_id = ?
                        """,
                        (job_id, source_id),
                    ).fetchall()
                }
                base_version_id = f"PVER-{uuid4()}"
                source_row = connection.execute(
                    """
                    SELECT parser_id, parser_version, parse_sha256
                    FROM conversion_source WHERE job_id = ? AND id = ?
                    """,
                    (job_id, source_id),
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO conversion_parse_version(
                        id, job_id, source_id, reextraction_request_id, scope,
                        page_number, parser_id, parser_version, parse_sha256, active, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        base_version_id,
                        job_id,
                        source_id,
                        request_id,
                        scope,
                        page_number or None,
                        str(source_row["parser_id"] or "unknown"),
                        str(source_row["parser_version"] or "unknown"),
                        str(source_row["parse_sha256"] or request["base_parse_sha256"] or "0" * 64),
                        utc_now(),
                    ),
                )
                for row in current_artifacts.values():
                    connection.execute(
                        """
                        INSERT INTO conversion_parse_artifact_version(
                            version_id, path, artifact_kind, content_type,
                            content, byte_count, sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            base_version_id,
                            row["path"],
                            row["artifact_kind"],
                            row["content_type"],
                            row["content"],
                            row["byte_count"],
                            row["sha256"],
                        ),
                    )
                for artifact in artifacts:
                    content = bytes(artifact["content"])
                    current_artifacts[str(artifact["path"])] = {
                        "path": str(artifact["path"]),
                        "artifact_kind": str(artifact.get("artifact_kind") or "RESOURCE"),
                        "content_type": str(
                            artifact.get("content_type") or "application/octet-stream"
                        ),
                        "content": content,
                        "byte_count": len(content),
                        "sha256": bytes_sha256(content),
                    }
                connection.execute(
                    "UPDATE conversion_parse_version SET active = 0 "
                    "WHERE job_id = ? AND source_id = ?",
                    (job_id, source_id),
                )
                new_version_id = f"PVER-{uuid4()}"
                connection.execute(
                    """
                    INSERT INTO conversion_parse_version(
                        id, job_id, source_id, reextraction_request_id, scope,
                        page_number, parser_id, parser_version, parse_sha256, active, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        new_version_id,
                        job_id,
                        source_id,
                        request_id,
                        scope,
                        page_number or None,
                        str(document.get("parser_id") or "unknown"),
                        str(document.get("parser_version") or "unknown"),
                        result_parse_sha256,
                        utc_now(),
                    ),
                )
                connection.execute(
                    "DELETE FROM conversion_parse_artifact WHERE job_id = ? AND source_id = ?",
                    (job_id, source_id),
                )
                for row in current_artifacts.values():
                    connection.execute(
                        """
                        INSERT INTO conversion_parse_artifact_version(
                            version_id, path, artifact_kind, content_type,
                            content, byte_count, sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_version_id,
                            row["path"],
                            row["artifact_kind"],
                            row["content_type"],
                            row["content"],
                            row["byte_count"],
                            row["sha256"],
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO conversion_parse_artifact(
                            job_id, source_id, path, artifact_kind, content_type,
                            content, byte_count, sha256, parse_sha256, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            job_id,
                            source_id,
                            row["path"],
                            row["artifact_kind"],
                            row["content_type"],
                            row["content"],
                            row["byte_count"],
                            row["sha256"],
                            result_parse_sha256,
                            utc_now(),
                        ),
                    )
                connection.execute(
                    """
                    UPDATE conversion_source
                    SET parser_id = ?, parser_version = ?, parse_sha256 = ?, parsed_at = ?,
                        security_status = ?
                    WHERE job_id = ? AND id = ?
                    """,
                    (
                        document.get("parser_id"),
                        document.get("parser_version"),
                        result_parse_sha256,
                        utc_now(),
                        "PARSED" if not partial else "PARSED_PARTIAL",
                        job_id,
                        source_id,
                    ),
                )

            after = candidate_snapshot(connection, job_id)
            after_hash = json_sha256(after)
            connection.execute(
                """
                INSERT INTO candidate_set_version(
                    id, job_id, reextraction_request_id, candidate_set_sha256,
                    candidates_json, active, created_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    f"CSET-{uuid4()}",
                    job_id,
                    request_id,
                    after_hash,
                    canonical_json(after),
                    utc_now(),
                ),
            )
            now = utc_now()
            run_id = f"XRUN-{uuid4()}"
            output = {
                "request_id": request_id,
                "scope": scope,
                "removed_candidate_count": len(target_ids),
                "new_candidate_count": len(new_candidates),
                "candidate_set_sha256": after_hash,
            }
            connection.execute(
                """
                INSERT INTO extraction_run(
                    id, job_id, step, task_type, status, input_sha256, output_sha256,
                    retry_count, input_json, output_json, started_at, finished_at
                ) VALUES (?, ?, ?, 'REEXTRACTION', ?, ?, ?, 0, NULL, ?, ?, ?)
                """,
                (
                    run_id,
                    job_id,
                    f"REEXTRACT:{request_id}",
                    "PARTIAL" if partial else "COMPLETED",
                    str(request["base_parse_sha256"] or before_hash),
                    json_sha256(output),
                    canonical_json(output),
                    str(request["started_at"] or now),
                    now,
                ),
            )
            changed = before_hash != after_hash
            if changed:
                connection.execute(
                    """
                    UPDATE review_session
                    SET status = 'STALE', gate_status = NULL, gate_result_hash = NULL,
                        updated_at = ?
                    WHERE id = ? AND status IN ('OPEN', 'IN_REVIEW', 'READY_TO_CONFIRM')
                    """,
                    (now, request["session_id"]),
                )
            final_status = "PARTIAL" if partial else "COMPLETED"
            connection.execute(
                """
                UPDATE reextraction_request
                SET status = ?, finished_at = ?, error_json = NULL,
                    result_parse_sha256 = ?, replacement_extraction_run_id = ?
                WHERE id = ?
                """,
                (final_status, now, result_parse_sha256, run_id, request_id),
            )
            self._record_event_in_connection(
                connection,
                event_type="REVIEW_REEXTRACTION_COMPLETED",
                entity_type="reextraction_request",
                entity_id=request_id,
                actor=safe_actor,
                detail={
                    **output,
                    "status": final_status,
                    "candidate_set_changed": changed,
                    "result_parse_sha256": result_parse_sha256,
                },
            )
        return {
            "id": request_id,
            "status": final_status,
            "candidate_set_changed": changed,
            "candidate_set_sha256": after_hash,
            "replacement_extraction_run_id": run_id,
            "result_parse_sha256": result_parse_sha256,
        }

    def save_stage4_result(self, job_id: str, result: dict[str, Any]) -> None:
        """Atomically materialize candidate facts separately from conversion payload JSON."""

        result_hash = str(result.get("result_sha256") or "")
        if len(result_hash) != 64:
            raise ValueError("第四阶段结果哈希无效")
        evidence = {str(item["evidence_id"]): dict(item) for item in result.get("evidence", [])}
        candidates = {
            str(item["candidate_id"]): dict(item) for item in result.get("candidates", [])
        }
        entities = {str(item["entity_id"]): dict(item) for item in result.get("entities", [])}
        relationships = {
            str(item["relationship_id"]): dict(item) for item in result.get("relationships", [])
        }
        issues = {str(item["issue_id"]): dict(item) for item in result.get("issues", [])}
        groups = {
            str(item["fusion_group_id"]): dict(item) for item in result.get("fusion_groups", [])
        }

        def issue_severity(item: dict[str, Any]) -> str:
            status = str(item.get("quality_status") or "INVALID")
            if status == "INFO":
                return "INFO"
            if status in {"WARNING", "LOW_CONFIDENCE", "MISSING", "PENDING_REVIEW"}:
                return "WARNING"
            return "ERROR"

        with self.transaction() as connection:
            job = connection.execute(
                """
                SELECT status, cancel_requested_at, stage4_result_sha256
                FROM conversion_job WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if job is None:
                raise KeyError(f"转换任务不存在：{job_id}")
            if job["stage4_result_sha256"] is not None:
                if str(job["stage4_result_sha256"]) == result_hash:
                    return
                raise ValueError("已固化的第四阶段结果不能被不同结果覆盖")
            if job["status"] != "RUNNING" or job["cancel_requested_at"]:
                raise ValueError("转换任务当前不允许固化第四阶段结果")
            connection.executemany(
                """
                INSERT INTO extracted_entity(
                    job_id, entity_id, entity_type, business_key,
                    normalized_name, confidence, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        job_id,
                        entity_id,
                        str(item["entity_type"]),
                        item.get("business_key"),
                        item.get("normalized_name"),
                        float(item.get("confidence", 0.0)),
                        canonical_json(item),
                    )
                    for entity_id, item in sorted(entities.items())
                ],
            )
            connection.executemany(
                """
                INSERT INTO candidate_field(
                    job_id, candidate_id, field_id, entity_type, entity_key,
                    extraction_method, confidence, quality_status, review_status,
                    source_unit, canonical_unit, normalized_value_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        job_id,
                        candidate_id,
                        str(item["field_id"]),
                        str((item.get("entity") or {})["entity_type"]),
                        str((item.get("entity") or {})["entity_key"]),
                        str(item["extraction_method"]),
                        float(item["confidence"]),
                        str(item["quality_status"]),
                        str(item["review_status"]),
                        item.get("source_unit"),
                        item.get("canonical_unit"),
                        canonical_json(item.get("normalized_value")),
                        canonical_json(item),
                    )
                    for candidate_id, item in sorted(candidates.items())
                ],
            )
            links = []
            for candidate_id, item in sorted(candidates.items()):
                for evidence_id in sorted(set(item.get("evidence_ids") or [])):
                    if evidence_id not in evidence:
                        raise ValueError(f"候选引用未持久化证据：{evidence_id}")
                    links.append(
                        (job_id, candidate_id, evidence_id, canonical_json(evidence[evidence_id]))
                    )
            connection.executemany(
                """
                INSERT INTO candidate_evidence_link(
                    job_id, candidate_id, evidence_id, evidence_json
                ) VALUES (?, ?, ?, ?)
                """,
                links,
            )
            connection.executemany(
                """
                INSERT INTO candidate_relationship(
                    job_id, relationship_id, relation_type, source_entity_id,
                    target_entity_id, confidence, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        job_id,
                        relationship_id,
                        str(item["relation_type"]),
                        str(item["source_entity_id"]),
                        str(item["target_entity_id"]),
                        float(item.get("confidence", 0.0)),
                        canonical_json(item),
                    )
                    for relationship_id, item in sorted(relationships.items())
                ],
            )
            connection.executemany(
                """
                INSERT INTO quality_issue(
                    job_id, issue_id, severity, code, blocking, field_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        job_id,
                        issue_id,
                        issue_severity(item),
                        str(item["code"]),
                        int(bool(item.get("blocking"))),
                        item.get("field_id"),
                        canonical_json(item),
                    )
                    for issue_id, item in sorted(issues.items())
                ],
            )
            connection.executemany(
                """
                INSERT INTO fusion_group(
                    job_id, fusion_group_id, entity_key, field_id, group_type,
                    candidate_set_sha256, proposed_candidate_id,
                    confirmed_candidate_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        job_id,
                        group_id,
                        str(item["entity_key"]),
                        item.get("field_id"),
                        str(item["group_type"]),
                        str(item["candidate_set_sha256"]),
                        item.get("proposed_candidate_id"),
                        item.get("confirmed_candidate_id"),
                        canonical_json(item),
                    )
                    for group_id, item in sorted(groups.items())
                ],
            )
            connection.executemany(
                """
                INSERT INTO fusion_group_member(job_id, fusion_group_id, candidate_id)
                VALUES (?, ?, ?)
                """,
                [
                    (job_id, group_id, str(candidate_id))
                    for group_id, item in sorted(groups.items())
                    for candidate_id in item.get("candidate_ids", [])
                    if str(candidate_id) in candidates
                ],
            )
            connection.execute(
                """
                UPDATE conversion_job
                SET stage4_status = ?, stage4_result_sha256 = ?,
                    stage4_metrics_json = ?, stage4_capability_json = ?,
                    revision = revision + 1
                WHERE id = ?
                """,
                (
                    str(result["status"]),
                    result_hash,
                    canonical_json(result.get("metrics") or {}),
                    canonical_json(result.get("capability_plan") or {}),
                    job_id,
                ),
            )
            self._record_event_in_connection(
                connection,
                event_type="STAGE4_RESULT_PERSISTED",
                entity_type="conversion_job",
                entity_id=job_id,
                detail={
                    "status": result["status"],
                    "result_sha256": result_hash,
                    "candidate_count": len(candidates),
                    "issue_count": len(issues),
                    "fusion_group_count": len(groups),
                },
            )

    def conversion_review_summary(self, job_id: str) -> dict[str, Any]:
        self.initialize()
        job = self.get_conversion_job(job_id, detailed=False)
        with self.session() as connection:
            candidate_rows = connection.execute(
                """
                SELECT quality_status, count(*) AS count
                FROM candidate_field WHERE job_id = ? GROUP BY quality_status
                """,
                (job_id,),
            ).fetchall()
            issue_rows = connection.execute(
                """
                SELECT severity, count(*) AS count
                FROM quality_issue WHERE job_id = ? GROUP BY severity
                """,
                (job_id,),
            ).fetchall()
            entity_count = int(
                connection.execute(
                    "SELECT count(*) FROM extracted_entity WHERE job_id = ?", (job_id,)
                ).fetchone()[0]
            )
            relation_count = int(
                connection.execute(
                    "SELECT count(*) FROM candidate_relationship WHERE job_id = ?", (job_id,)
                ).fetchone()[0]
            )
            group_count = int(
                connection.execute(
                    "SELECT count(*) FROM fusion_group WHERE job_id = ?", (job_id,)
                ).fetchone()[0]
            )
        return {
            "conversion_id": job_id,
            "status": job.get("stage4_status") or "NOT_RUN",
            "result_sha256": job.get("stage4_result_sha256"),
            "candidate_counts": {
                str(row["quality_status"]): int(row["count"]) for row in candidate_rows
            },
            "issue_counts": {str(row["severity"]): int(row["count"]) for row in issue_rows},
            "entity_count": entity_count,
            "relationship_count": relation_count,
            "fusion_group_count": group_count,
            "metrics": job.get("stage4_metrics") or {},
        }

    def list_conversion_candidates(
        self,
        job_id: str,
        *,
        status: str | None = None,
        field_id: str | None = None,
        entity: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        self.initialize()
        self.get_conversion_job(job_id, detailed=False)
        filters = ["job_id = ?"]
        parameters: list[Any] = [job_id]
        for column, value in (
            ("quality_status", status),
            ("field_id", field_id),
            ("entity_key", entity),
        ):
            if value:
                filters.append(f"{column} = ?")
                parameters.append(str(value))
        if cursor:
            filters.append("candidate_id > ?")
            parameters.append(str(cursor))
        safe_limit = max(1, min(int(limit), 500))
        with self.session() as connection:
            rows = connection.execute(
                f"""
                SELECT candidate_id, field_id, entity_type, entity_key,
                       extraction_method, confidence, quality_status, review_status,
                       source_unit, canonical_unit, normalized_value_json
                FROM candidate_field
                WHERE {" AND ".join(filters)}
                ORDER BY candidate_id LIMIT ?
                """,
                (*parameters, safe_limit + 1),
            ).fetchall()
        has_more = len(rows) > safe_limit
        rows = rows[:safe_limit]
        items = []
        for row in rows:
            item = dict(row)
            item["normalized_value"] = json.loads(item.pop("normalized_value_json"))
            items.append(item)
        return {
            "items": items,
            "next_cursor": items[-1]["candidate_id"] if has_more and items else None,
        }

    def get_conversion_candidate(self, job_id: str, candidate_id: str) -> dict[str, Any]:
        self.initialize()
        with self.session() as connection:
            row = connection.execute(
                "SELECT payload_json FROM candidate_field WHERE job_id = ? AND candidate_id = ?",
                (job_id, candidate_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"候选不存在：{candidate_id}")
            evidence_rows = connection.execute(
                """
                SELECT evidence_json FROM candidate_evidence_link
                WHERE job_id = ? AND candidate_id = ? ORDER BY evidence_id
                """,
                (job_id, candidate_id),
            ).fetchall()
            group_rows = connection.execute(
                """
                SELECT g.payload_json FROM fusion_group g
                JOIN fusion_group_member m
                  ON m.job_id = g.job_id AND m.fusion_group_id = g.fusion_group_id
                WHERE m.job_id = ? AND m.candidate_id = ?
                ORDER BY g.fusion_group_id
                """,
                (job_id, candidate_id),
            ).fetchall()
        item = json.loads(str(row["payload_json"]))
        item["evidence"] = [json.loads(str(value["evidence_json"])) for value in evidence_rows]
        item["fusion_groups"] = [json.loads(str(value["payload_json"])) for value in group_rows]
        return item

    def list_conversion_quality_issues(
        self,
        job_id: str,
        *,
        severity: str | None = None,
        code: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        self.initialize()
        self.get_conversion_job(job_id, detailed=False)
        filters = ["job_id = ?"]
        parameters: list[Any] = [job_id]
        if severity:
            filters.append("severity = ?")
            parameters.append(str(severity))
        if code:
            filters.append("code = ?")
            parameters.append(str(code))
        if cursor:
            filters.append("issue_id > ?")
            parameters.append(str(cursor))
        safe_limit = max(1, min(int(limit), 500))
        with self.session() as connection:
            rows = connection.execute(
                f"""
                SELECT payload_json FROM quality_issue
                WHERE {" AND ".join(filters)} ORDER BY issue_id LIMIT ?
                """,
                (*parameters, safe_limit + 1),
            ).fetchall()
        has_more = len(rows) > safe_limit
        items = [json.loads(str(row["payload_json"])) for row in rows[:safe_limit]]
        return {
            "items": items,
            "next_cursor": items[-1]["issue_id"] if has_more and items else None,
        }

    def conversion_capability(self, job_id: str) -> dict[str, Any]:
        job = self.get_conversion_job(job_id, detailed=False)
        return {
            "conversion_id": job_id,
            "status": job.get("stage4_status") or "NOT_RUN",
            "capability_plan": job.get("stage4_capability") or {},
        }

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

    def delete_conversion_job(self, job_id: str, *, actor: str = "local-admin") -> dict[str, Any]:
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
        job_id: str | None = None,
        allowed_statuses: set[str] | None = None,
    ) -> dict[str, Any]:
        """Read one protected source by ID for an in-process parser, never by user path."""
        self.initialize()
        statuses = allowed_statuses or {"READY_FOR_PARSE"}
        with self.session() as connection:
            where = "id = ? AND job_id = ?" if job_id is not None else "id = ?"
            parameters = (source_id, job_id) if job_id is not None else (source_id,)
            row = connection.execute(
                f"""
                SELECT job_id, id, relative_path, original_file_name,
                       detected_media_type, byte_count, sha256, source_kind,
                       security_status, archive_name, archive_member_path, content
                FROM conversion_source WHERE {where}
                """,
                parameters,
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
            external_sharing_allowed=bool(job.get("external_sharing_allowed")),
            ocr_provider_id=job.get("ocr_provider_id"),
            ocr_model_version=job.get("ocr_model_version"),
            extraction_provider_id=job.get("extraction_provider_id"),
            extraction_model_version=job.get("extraction_model_version"),
            pilot_id=job.get("pilot_id"),
            pilot_version=job.get("pilot_version"),
            pilot_manifest_sha256=job.get("pilot_manifest_sha256"),
            target_node_ids=job.get("target_node_ids") or None,
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
                placeholders = ",".join("?" for _ in job_ids)
                connection.execute(
                    f"""
                    UPDATE conversion_source
                    SET security_status = 'READY_FOR_PARSE'
                    WHERE job_id IN ({placeholders})
                      AND security_status IN ('PARSED', 'PARSE_FAILED')
                    """,
                    job_ids,
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

    def requeue_interrupted_reextractions(self) -> list[str]:
        """Resume durable reextraction requests after a process restart."""

        self.initialize()
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT id, status FROM reextraction_request
                WHERE status IN ('QUEUED', 'RUNNING')
                ORDER BY created_at, id
                """
            ).fetchall()
            request_ids = [str(row["id"]) for row in rows]
            if request_ids:
                connection.execute(
                    """
                    UPDATE reextraction_request
                    SET status = 'QUEUED', started_at = NULL, finished_at = NULL,
                        error_json = NULL
                    WHERE status IN ('QUEUED', 'RUNNING')
                    """
                )
                for row in rows:
                    if str(row["status"]) == "RUNNING":
                        self._record_event_in_connection(
                            connection,
                            event_type="REVIEW_REEXTRACTION_REQUEUED",
                            entity_type="reextraction_request",
                            entity_id=str(row["id"]),
                            actor="system",
                            detail={"reason": "service_restart"},
                        )
        return request_ids

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
            review_provenance_rows = connection.execute(
                """
                SELECT id, conversion_job_id, review_session_id, review_gate_run_id,
                       source_manifest_hash, candidate_set_hash, decision_set_hash,
                       mapping_version, mapping_sha256, contract_version,
                       contract_sha256, extraction_model_version, ocr_model_version,
                       prompt_version, rule_version, decision_summary_json,
                       confirmed_by, confirmation_reason, confirmed_at
                FROM input_snapshot_review_provenance
                WHERE snapshot_id = ? ORDER BY confirmed_at, id
                """,
                (snapshot_id,),
            ).fetchall()
        result = dict(row)
        result.pop("payload_json", None)
        result["counts"] = counts
        result["conversion"] = None
        if provenance is not None:
            result["conversion"] = dict(provenance)
            result["conversion"]["source_manifest"] = json.loads(
                result["conversion"].pop("source_manifest_json")
            )
        result["review_confirmations"] = []
        for row in review_provenance_rows:
            item = dict(row)
            item["decision_summary"] = json.loads(item.pop("decision_summary_json"))
            result["review_confirmations"].append(item)
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

    def create_run(
        self,
        snapshot_id: str,
        input_sha256: str,
        *,
        targets: list[str] | None = None,
        generate_charts: bool = True,
        review_session_id: str | None = None,
        review_provenance_id: str | None = None,
    ) -> str:
        run_id = f"RUN-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
        with self.transaction() as connection:
            snapshot = connection.execute(
                "SELECT payload_sha256 FROM input_snapshot WHERE id = ?", (snapshot_id,)
            ).fetchone()
            if snapshot is None:
                raise KeyError(f"输入快照不存在：{snapshot_id}")
            if str(snapshot["payload_sha256"]) != str(input_sha256):
                raise ValueError("计算输入哈希必须等于不可变快照哈希")
            connection.execute(
                """
                INSERT INTO calculation_run(
                    id, snapshot_id, status, input_sha256, review_session_id,
                    review_provenance_id, target_node_ids_json, generate_charts, created_at
                ) VALUES (?, ?, 'QUEUED', ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    snapshot_id,
                    input_sha256,
                    review_session_id,
                    review_provenance_id,
                    canonical_json(targets) if targets else None,
                    int(bool(generate_charts)),
                    utc_now(),
                ),
            )
            self._record_event_in_connection(
                connection,
                event_type="RUN_QUEUED",
                entity_type="calculation_run",
                entity_id=run_id,
                detail={
                    "snapshot_id": snapshot_id,
                    "review_session_id": review_session_id,
                    "targets": list(targets or []),
                    "generate_charts": bool(generate_charts),
                },
            )
        return run_id

    def requeue_interrupted_runs(self) -> list[str]:
        """Recover queued/running process-local calculations after a service restart."""

        self.initialize()
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT id FROM calculation_run WHERE status IN ('QUEUED','RUNNING')"
            ).fetchall()
            run_ids = [str(row["id"]) for row in rows]
            if run_ids:
                connection.execute(
                    """
                    UPDATE calculation_run
                    SET status = 'QUEUED', engine_version = NULL, started_at = NULL,
                        error_message = NULL
                    WHERE status IN ('QUEUED','RUNNING')
                    """
                )
                for run_id in run_ids:
                    self._record_event_in_connection(
                        connection,
                        event_type="RUN_RECOVERED",
                        entity_type="calculation_run",
                        entity_id=run_id,
                        detail={"reason": "service_restart"},
                    )
        return run_ids

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
                relative = normalize_artifact_path(artifact_path.relative_to(root).as_posix())
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
        targets = result.pop("target_node_ids_json", None)
        result["target_node_ids"] = json.loads(str(targets)) if targets else []
        result["generate_charts"] = bool(result.get("generate_charts", 1))
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
        safe_path = normalize_artifact_path(path)
        with self.session() as connection:
            row = connection.execute(
                """
                SELECT content_type, content
                FROM calculation_artifact
                WHERE run_id = ? AND path = ?
                """,
                (run_id, safe_path),
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
    "normalize_artifact_path",
    "bytes_sha256",
    "canonical_json",
    "json_sha256",
    "utc_now",
]
