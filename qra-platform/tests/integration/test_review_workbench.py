from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image
from pypdf import PdfWriter

from db_qra.admin_ui import admin_html
from db_qra.conversion_adapter import run_conversion_job, submit_conversion
from db_qra.database import QraDatabase, canonical_json
from db_qra.engine_adapter import execute_run
from db_qra.review_service import ReviewRevisionConflict, ReviewService
from db_qra.review_ui import review_workbench_html
from db_qra.server import QraRequestHandler

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MVP_SOURCES = PROJECT_ROOT / "tests" / "fixtures" / "converter_mvp"


def source_files() -> list[dict[str, object]]:
    return [
        {"file_name": path.name, "content": path.read_bytes()}
        for path in sorted(MVP_SOURCES.glob("*.csv"))
    ]


class ReviewWorkbenchIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = QraDatabase(self.root / "qra.sqlite3")
        self.job_id, _ = submit_conversion(
            self.database,
            profile="generic.structured-mvp.v1",
            files=source_files(),
            project_name=f"复核工作台-{self.id()}",
        )
        run_conversion_job(self.database, self.job_id, runtime_root=self.root / "runtime")
        self.service = ReviewService(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_session(self, *, targets: list[str] | None = None) -> dict[str, object]:
        session, _ = self.service.create_or_resume_session(
            self.job_id,
            actor="reviewer-a",
            target_node_ids=targets or ["segment_geometry"],
        )
        return self.service.get_session(str(session["id"]))

    def item(self, session_id: str, field_id: str) -> dict[str, object]:
        return next(
            item
            for item in self.service.list_items(session_id, limit=500)["items"]
            if item["field_id"] == field_id
        )

    def inject_candidate(
        self,
        *,
        field_id: str,
        normalized_value: object,
        entity_key: str | None = None,
        candidate_id: str = "CAND-TEST-EXTRA",
    ) -> str:
        with self.database.transaction() as connection:
            if entity_key is None:
                original = connection.execute(
                    """
                    SELECT payload_json FROM candidate_field
                    WHERE job_id = ? AND field_id = ? LIMIT 1
                    """,
                    (self.job_id, field_id),
                ).fetchone()
            else:
                original = connection.execute(
                    """
                    SELECT payload_json FROM candidate_field
                    WHERE job_id = ? AND field_id = ? AND entity_key = ? LIMIT 1
                    """,
                    (self.job_id, field_id, entity_key),
                ).fetchone()
            self.assertIsNotNone(original)
            candidate = json.loads(str(original["payload_json"]))
            old_candidate_id = str(candidate["candidate_id"])
            candidate["candidate_id"] = candidate_id
            candidate["raw_value"] = normalized_value
            candidate["parsed_value"] = normalized_value
            candidate["normalized_value"] = normalized_value
            candidate["confidence"] = 0.99
            candidate["quality_status"] = "PASS"
            entity = candidate["entity"]
            connection.execute(
                """
                INSERT INTO candidate_field(
                    job_id, candidate_id, field_id, entity_type, entity_key,
                    extraction_method, confidence, quality_status, review_status,
                    source_unit, canonical_unit, normalized_value_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.job_id,
                    candidate_id,
                    field_id,
                    entity["entity_type"],
                    entity["entity_key"],
                    candidate["extraction_method"],
                    candidate["confidence"],
                    candidate["quality_status"],
                    candidate["review_status"],
                    candidate.get("source_unit"),
                    candidate.get("canonical_unit"),
                    canonical_json(normalized_value),
                    canonical_json(candidate),
                ),
            )
            evidence = connection.execute(
                """
                SELECT evidence_id, evidence_json FROM candidate_evidence_link
                WHERE job_id = ? AND candidate_id = ? ORDER BY evidence_id LIMIT 1
                """,
                (self.job_id, old_candidate_id),
            ).fetchone()
            self.assertIsNotNone(evidence)
            connection.execute(
                """
                INSERT INTO candidate_evidence_link(
                    job_id, candidate_id, evidence_id, evidence_json
                )
                VALUES (?, ?, ?, ?)
                """,
                (self.job_id, candidate_id, evidence["evidence_id"], evidence["evidence_json"]),
            )
        return candidate_id

    def inject_optional_candidate(self) -> None:
        with self.database.transaction() as connection:
            original = connection.execute(
                "SELECT payload_json FROM candidate_field WHERE job_id = ? LIMIT 1",
                (self.job_id,),
            ).fetchone()
            candidate = json.loads(str(original["payload_json"]))
            old_id = str(candidate["candidate_id"])
            candidate.update(
                {
                    "candidate_id": "CAND-OPTIONAL-CLASSIFICATION",
                    "field_id": "metadata.data_classification",
                    "entity": {"entity_type": "PROJECT", "entity_key": "GLOBAL"},
                    "raw_value": "INTERNAL",
                    "parsed_value": "INTERNAL",
                    "normalized_value": "INTERNAL",
                    "source_unit": None,
                    "canonical_unit": None,
                    "quality_status": "PASS",
                }
            )
            connection.execute(
                """
                INSERT INTO candidate_field(
                    job_id, candidate_id, field_id, entity_type, entity_key,
                    extraction_method, confidence, quality_status, review_status,
                    source_unit, canonical_unit, normalized_value_json, payload_json
                ) VALUES (?, ?, ?, 'PROJECT', 'GLOBAL', ?, 1.0, 'PASS', 'PENDING',
                          NULL, NULL, ?, ?)
                """,
                (
                    self.job_id,
                    candidate["candidate_id"],
                    candidate["field_id"],
                    candidate["extraction_method"],
                    canonical_json(candidate["normalized_value"]),
                    canonical_json(candidate),
                ),
            )
            evidence = connection.execute(
                """
                SELECT evidence_id, evidence_json FROM candidate_evidence_link
                WHERE job_id = ? AND candidate_id = ? LIMIT 1
                """,
                (self.job_id, old_id),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO candidate_evidence_link(
                    job_id, candidate_id, evidence_id, evidence_json
                )
                VALUES (?, 'CAND-OPTIONAL-CLASSIFICATION', ?, ?)
                """,
                (self.job_id, evidence["evidence_id"], evidence["evidence_json"]),
            )

    def confirm(self, session_id: str, *, run_after: bool = False) -> dict[str, object]:
        session = self.service.get_session(session_id)
        self.service.run_gate(session_id, actor="approver")
        session = self.service.get_session(session_id)
        return self.service.confirm(
            session_id,
            snapshot_name="复核快照",
            reviewer="approver",
            reason="已逐项核对候选、证据与目标节点",
            expected_revision=int(session["revision"]),
            expected_candidate_set_hash=str(session["candidate_set_hash"]),
            expected_decision_set_hash=str(session["decision_set_hash"]),
            run_after_confirm=run_after,
            generate_charts=False,
        )

    def test_migration_creates_review_tables_and_immutable_triggers(self) -> None:
        with self.database.session() as connection:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            triggers = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                ).fetchall()
            }
        self.assertTrue(
            {
                "review_session",
                "review_decision",
                "review_gate_run",
                "reextraction_request",
                "input_snapshot_review_provenance",
            }.issubset(tables)
        )
        self.assertIn("trg_input_snapshot_business_immutable", triggers)
        self.assertIn("trg_review_decision_immutable_update", triggers)

    def test_session_creation_is_idempotent_and_candidates_are_grouped(self) -> None:
        first, created = self.service.create_or_resume_session(
            self.job_id, actor="reviewer-a", target_node_ids=["segment_geometry"]
        )
        second, created_again = self.service.create_or_resume_session(
            self.job_id, actor="reviewer-b", target_node_ids=["segment_geometry"]
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"], second["id"])
        items = self.service.list_items(str(first["id"]), limit=500)["items"]
        keys = {(item["entity_id"], item["field_id"]) for item in items}
        self.assertEqual(len(keys), len(items))
        self.assertTrue(all(item["evidence_count"] > 0 for item in items))
        self.assertTrue(any(item["resolution_status"] == "AUTO_DETERMINISTIC" for item in items))

    def test_accept_override_validation_and_revision_conflict(self) -> None:
        session = self.create_session()
        session_id = str(session["id"])
        wall = self.item(session_id, "segment.wall_thickness_mm")
        with self.assertRaises(ValueError):
            self.service.save_decision(
                session_id,
                review_item_key=str(wall["review_item_key"]),
                action="ACCEPT_CANDIDATE",
                selected_candidate_id="CAND-NOT-IN-SESSION",
                override_value=None,
                override_unit=None,
                reason="不存在的候选不能接受",
                actor="reviewer-a",
                expected_revision=int(session["revision"]),
            )
        accepted = self.service.save_decision(
            session_id,
            review_item_key=str(wall["review_item_key"]),
            action="ACCEPT_CANDIDATE",
            selected_candidate_id=str(wall["candidates"][0]["candidate_id"]),
            override_value=None,
            override_unit=None,
            reason="与台账单元格一致",
            actor="reviewer-a",
            expected_revision=int(session["revision"]),
        )
        self.assertEqual(accepted["item"]["resolution_status"], "ACCEPTED")
        with self.assertRaises(ReviewRevisionConflict):
            self.service.save_decision(
                session_id,
                review_item_key=str(wall["review_item_key"]),
                action="REJECT_ALL",
                selected_candidate_id=None,
                override_value=None,
                override_unit=None,
                reason="并发旧请求",
                actor="reviewer-b",
                expected_revision=int(session["revision"]),
            )
        current = self.service.get_session(session_id)
        for value, unit, reason in (
            ("abc", "mm", "错误类型"),
            (12, "kg", "错误单位"),
            (12, "mm", ""),
        ):
            with self.assertRaises(ValueError):
                self.service.save_decision(
                    session_id,
                    review_item_key=str(wall["review_item_key"]),
                    action="OVERRIDE_VALUE",
                    selected_candidate_id=None,
                    override_value=value,
                    override_unit=unit,
                    reason=reason,
                    actor="reviewer-a",
                    expected_revision=int(current["revision"]),
                )

    def test_conflict_requires_explicit_choice_and_alternate_candidate_is_assembled(self) -> None:
        with self.database.session() as connection:
            row = connection.execute(
                """
                SELECT entity_key, normalized_value_json FROM candidate_field
                WHERE job_id = ? AND field_id = 'segment.wall_thickness_mm' LIMIT 1
                """,
                (self.job_id,),
            ).fetchone()
        alternate_value = float(json.loads(row["normalized_value_json"])) + 1.25
        alternate_id = self.inject_candidate(
            field_id="segment.wall_thickness_mm",
            entity_key=str(row["entity_key"]),
            normalized_value=alternate_value,
            candidate_id="CAND-CONFLICT-ALTERNATE",
        )
        session = self.create_session()
        session_id = str(session["id"])
        item = next(
            item
            for item in self.service.list_items(session_id, limit=500)["items"]
            if item["field_id"] == "segment.wall_thickness_mm"
            and item["entity_id"] == row["entity_key"]
        )
        self.assertTrue(item["conflict"])
        self.assertTrue(item["requires_resolution"])
        self.assertEqual(self.service.run_gate(session_id, actor="reviewer-a")["status"], "BLOCKED")
        current = self.service.get_session(session_id)
        self.service.save_decision(
            session_id,
            review_item_key=str(item["review_item_key"]),
            action="ACCEPT_CANDIDATE",
            selected_candidate_id=alternate_id,
            override_value=None,
            override_unit=None,
            reason="冲突资料已与批准台账核对",
            actor="reviewer-a",
            expected_revision=int(current["revision"]),
        )
        gate = self.service.run_gate(session_id, actor="reviewer-a")
        self.assertEqual(gate["status"], "PASS")
        result = self.confirm(session_id)
        payload = self.database.load_snapshot(str(result["snapshot_id"]))
        segment_id = str(row["entity_key"]).split(":")[-1]
        segment = next(row for row in payload["segments"] if row["segment_id"] == segment_id)
        self.assertEqual(segment["wall_thickness_mm"], alternate_value)

    def test_low_confidence_key_field_requires_human_acceptance(self) -> None:
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT candidate_id, payload_json FROM candidate_field
                WHERE job_id = ? AND field_id = 'segment.start_km' LIMIT 1
                """,
                (self.job_id,),
            ).fetchone()
            candidate = json.loads(str(row["payload_json"]))
            candidate["confidence"] = 0.45
            candidate["quality_status"] = "LOW_CONFIDENCE"
            connection.execute(
                """
                UPDATE candidate_field SET confidence = 0.45,
                    quality_status = 'LOW_CONFIDENCE', payload_json = ?
                WHERE job_id = ? AND candidate_id = ?
                """,
                (canonical_json(candidate), self.job_id, row["candidate_id"]),
            )
        session = self.create_session()
        item = self.item(str(session["id"]), "segment.start_km")
        self.assertTrue(item["low_confidence"])
        self.assertTrue(item["requires_resolution"])
        self.assertEqual(
            self.service.run_gate(str(session["id"]), actor="reviewer-a")["status"],
            "BLOCKED",
        )
        current = self.service.get_session(str(session["id"]))
        self.service.save_decision(
            str(session["id"]),
            review_item_key=str(item["review_item_key"]),
            action="ACCEPT_CANDIDATE",
            selected_candidate_id=str(item["candidates"][0]["candidate_id"]),
            override_value=None,
            override_unit=None,
            reason="已对照原始单元格人工确认低置信度值",
            actor="reviewer-a",
            expected_revision=int(current["revision"]),
        )
        self.assertEqual(
            self.service.run_gate(str(session["id"]), actor="reviewer-a")["status"],
            "PASS",
        )

    def test_missing_key_field_can_only_be_resolved_by_typed_manual_value(self) -> None:
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT candidate_id, entity_key FROM candidate_field
                WHERE job_id = ? AND field_id = 'segment.wall_thickness_mm' LIMIT 1
                """,
                (self.job_id,),
            ).fetchone()
            connection.execute(
                "DELETE FROM candidate_field WHERE job_id = ? AND candidate_id = ?",
                (self.job_id, row["candidate_id"]),
            )
            issue = {
                "issue_id": "ISS-MISSING-WALL",
                "code": "QUALITY.REQUIRED_FIELD_MISSING",
                "quality_status": "MISSING",
                "field_id": "segment.wall_thickness_mm",
                "entity_key": row["entity_key"],
                "candidate_ids": [],
                "evidence_ids": [],
                "message": "缺少关键壁厚",
                "blocking": True,
            }
            connection.execute(
                """
                INSERT INTO quality_issue(
                    job_id, issue_id, severity, code, blocking, field_id, payload_json
                )
                VALUES (?, ?, 'ERROR', ?, 1, ?, ?)
                """,
                (
                    self.job_id,
                    issue["issue_id"],
                    issue["code"],
                    issue["field_id"],
                    canonical_json(issue),
                ),
            )
        session = self.create_session()
        item = next(
            item
            for item in self.service.list_items(str(session["id"]), limit=500)["items"]
            if item["field_id"] == "segment.wall_thickness_mm"
            and item["entity_id"] == row["entity_key"]
        )
        self.assertTrue(item["missing"])
        self.assertTrue(item["requires_resolution"])
        saved = self.service.save_decision(
            str(session["id"]),
            review_item_key=str(item["review_item_key"]),
            action="OVERRIDE_VALUE",
            selected_candidate_id=None,
            override_value=1.3,
            override_unit="cm",
            reason="现场复测为1.3厘米",
            actor="reviewer-a",
            expected_revision=int(session["revision"]),
        )
        self.assertEqual(saved["item"]["current_decision"]["override_normalized_value"], 13)
        self.assertEqual(
            self.service.run_gate(str(session["id"]), actor="reviewer-a")["status"],
            "PASS",
        )

    def test_reject_reextract_and_not_applicable_rules_affect_gate(self) -> None:
        session = self.create_session()
        session_id = str(session["id"])
        critical = self.item(session_id, "segment.start_km")
        with self.assertRaises(ValueError):
            self.service.save_decision(
                session_id,
                review_item_key=str(critical["review_item_key"]),
                action="MARK_NOT_APPLICABLE",
                selected_candidate_id=None,
                override_value=None,
                override_unit=None,
                reason="错误地标记必填字段",
                actor="reviewer-a",
                expected_revision=int(session["revision"]),
            )
        self.service.save_decision(
            session_id,
            review_item_key=str(critical["review_item_key"]),
            action="REJECT_ALL",
            selected_candidate_id=None,
            override_value=None,
            override_unit=None,
            reason="源资料不可信",
            actor="reviewer-a",
            expected_revision=int(session["revision"]),
        )
        self.assertEqual(self.service.run_gate(session_id, actor="reviewer-a")["status"], "BLOCKED")
        current = self.service.get_session(session_id)
        evidence = critical["candidates"][0]["evidence"][0]
        reextraction = self.service.save_decision(
            session_id,
            review_item_key=str(critical["review_item_key"]),
            action="REQUEST_REEXTRACTION",
            selected_candidate_id=None,
            override_value=None,
            override_unit=None,
            reason="请重新识别该单元格",
            actor="reviewer-a",
            expected_revision=int(current["revision"]),
            source_id=evidence["location"]["file_id"],
            evidence_id=evidence["evidence_id"],
        )
        with self.database.session() as connection:
            request = connection.execute(
                "SELECT status FROM reextraction_request WHERE session_id = ?", (session_id,)
            ).fetchone()
        self.assertEqual(request["status"], "QUEUED")
        self.assertEqual(self.service.run_gate(session_id, actor="reviewer-a")["status"], "BLOCKED")
        self.inject_candidate(
            field_id=str(critical["field_id"]),
            entity_key=str(critical["entity_id"]),
            normalized_value=critical["candidates"][0]["normalized_value"],
            candidate_id="CAND-REEXTRACTED-REPLACEMENT",
        )
        completed = self.service.complete_reextraction(
            str(reextraction["reextraction_request_id"]), actor="extractor-worker"
        )
        self.assertTrue(completed["session_stale"])
        self.assertEqual(self.service.get_session(session_id)["status"], "STALE")

        other_root = self.root / "optional.sqlite3"
        optional_db = QraDatabase(other_root)
        optional_job, _ = submit_conversion(
            optional_db,
            profile="generic.structured-mvp.v1",
            files=source_files(),
            project_name="optional",
        )
        run_conversion_job(optional_db, optional_job, runtime_root=self.root / "optional-runtime")
        self.database, self.job_id, self.service = (
            optional_db,
            optional_job,
            ReviewService(optional_db),
        )
        self.inject_optional_candidate()
        optional_session = self.create_session()
        optional_item = self.item(str(optional_session["id"]), "metadata.data_classification")
        saved = self.service.save_decision(
            str(optional_session["id"]),
            review_item_key=str(optional_item["review_item_key"]),
            action="MARK_NOT_APPLICABLE",
            selected_candidate_id=None,
            override_value=None,
            override_unit=None,
            reason="本试点不使用该分类字段",
            actor="reviewer-a",
            expected_revision=int(optional_session["revision"]),
        )
        self.assertEqual(saved["item"]["resolution_status"], "NOT_APPLICABLE")
        self.assertEqual(
            self.service.run_gate(str(optional_session["id"]), actor="reviewer-a")["status"],
            "PASS",
        )

    def test_candidate_change_marks_session_stale_and_does_not_reuse_decision(self) -> None:
        session = self.create_session()
        session_id = str(session["id"])
        wall = self.item(session_id, "segment.wall_thickness_mm")
        self.service.save_decision(
            session_id,
            review_item_key=str(wall["review_item_key"]),
            action="ACCEPT_CANDIDATE",
            selected_candidate_id=str(wall["candidates"][0]["candidate_id"]),
            override_value=None,
            override_unit=None,
            reason="初次确认",
            actor="reviewer-a",
            expected_revision=int(session["revision"]),
        )
        self.inject_candidate(
            field_id="segment.wall_thickness_mm",
            entity_key=str(wall["entity_id"]),
            normalized_value=99.0,
            candidate_id="CAND-NEW-EXTRACTION",
        )
        stale = self.service.get_session(session_id)
        self.assertEqual(stale["status"], "STALE")
        with self.assertRaises(ValueError):
            self.service.run_gate(session_id, actor="reviewer-a")

    def test_evidence_is_task_scoped_and_table_preview_has_context(self) -> None:
        session = self.create_session()
        item = self.item(str(session["id"]), "segment.start_km")
        evidence_id = str(item["candidates"][0]["evidence"][0]["evidence_id"])
        evidence = self.service.get_evidence(str(session["id"]), evidence_id)
        self.assertTrue(evidence["sanitized_file_name"])
        content_type, content, _ = self.service.evidence_preview(str(session["id"]), evidence_id)
        self.assertIn("application/json", content_type)
        self.assertTrue(json.loads(content.decode("utf-8"))["rows"])
        with self.assertRaises(KeyError):
            self.service.get_evidence(str(session["id"]), "../../workspace/state/qra.sqlite3")
        other_sources = source_files()
        other_sources[0]["content"] = bytes(other_sources[0]["content"]) + b"\n"
        other_job, _ = submit_conversion(
            self.database,
            profile="generic.structured-mvp.v1",
            files=other_sources,
            project_name="另一个证据隔离任务",
            case_id="OTHER-EVIDENCE-JOB",
        )
        run_conversion_job(self.database, other_job, runtime_root=self.root / "other-runtime")
        other_candidate_id = self.database.list_conversion_candidates(other_job, limit=1)["items"][
            0
        ]["candidate_id"]
        with self.database.transaction() as connection:
            other_link = connection.execute(
                """
                SELECT evidence_json FROM candidate_evidence_link
                WHERE job_id = ? AND candidate_id = ? LIMIT 1
                """,
                (other_job, other_candidate_id),
            ).fetchone()
            other_document = json.loads(str(other_link["evidence_json"]))
            other_evidence = "EVD-OTHER-TASK-ONLY"
            other_document["evidence_id"] = other_evidence
            connection.execute(
                """
                INSERT INTO candidate_evidence_link(
                    job_id, candidate_id, evidence_id, evidence_json
                )
                VALUES (?, ?, ?, ?)
                """,
                (other_job, other_candidate_id, other_evidence, canonical_json(other_document)),
            )
        with self.assertRaises(KeyError):
            self.service.get_evidence(str(session["id"]), str(other_evidence))

    def test_image_and_pdf_bbox_evidence_return_highlighted_inline_previews(self) -> None:
        image_output = io.BytesIO()
        Image.new("RGB", (20, 20), "white").save(image_output, format="PNG")
        image_bytes = image_output.getvalue()
        source_id = "SOURCE-IMAGE-EVIDENCE"
        evidence_id = "EVD-IMAGE-BBOX"
        candidate_id = "CAND-IMAGE-COORDINATE-SYSTEM"
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO conversion_source(
                    job_id, id, file_name, media_type, relative_path,
                    original_file_name, declared_media_type, detected_media_type,
                    byte_count, sha256, source_kind, security_status, created_at, content
                ) VALUES (?, ?, 'scan.png', 'image/png', 'scan.png', 'scan.png',
                          'image/png', 'image/png', ?, ?, 'TOP_LEVEL', 'PARSED', ?, ?)
                """,
                (
                    self.job_id,
                    source_id,
                    len(image_bytes),
                    __import__("hashlib").sha256(image_bytes).hexdigest(),
                    "2026-08-31T00:00:00+00:00",
                    image_bytes,
                ),
            )
            candidate = {
                "candidate_id": candidate_id,
                "field_id": "assessment.coordinate_system",
                "entity": {"entity_type": "ASSESSMENT", "entity_key": "GLOBAL"},
                "raw_value": "EPSG:4547",
                "parsed_value": "EPSG:4547",
                "normalized_value": "EPSG:4547",
                "source_unit": None,
                "canonical_unit": None,
                "confidence": 0.91,
                "evidence_ids": [evidence_id],
                "extraction_method": "OCR_MODEL",
                "quality_status": "PASS",
                "review_status": "PENDING",
                "model_or_rule_versions": {"ocr": "fixture/1.0.0"},
            }
            evidence = {
                "evidence_id": evidence_id,
                "source_type": "IMAGE",
                "excerpt": "EPSG:4547",
                "checksum_sha256": __import__("hashlib").sha256(image_bytes).hexdigest(),
                "location": {
                    "kind": "IMAGE",
                    "file_id": source_id,
                    "page_number": 1,
                    "coordinate_system": "PIXEL_TOP_LEFT",
                    "bounding_box": [2, 2, 10, 10],
                    "image_size": [20, 20],
                },
            }
            connection.execute(
                """
                INSERT INTO candidate_field(
                    job_id, candidate_id, field_id, entity_type, entity_key,
                    extraction_method, confidence, quality_status, review_status,
                    source_unit, canonical_unit, normalized_value_json, payload_json
                ) VALUES (?, ?, ?, 'ASSESSMENT', 'GLOBAL', 'OCR_MODEL', 0.91,
                          'PASS', 'PENDING', NULL, NULL, ?, ?)
                """,
                (
                    self.job_id,
                    candidate_id,
                    candidate["field_id"],
                    canonical_json(candidate["normalized_value"]),
                    canonical_json(candidate),
                ),
            )
            connection.execute(
                """
                INSERT INTO candidate_evidence_link(
                    job_id, candidate_id, evidence_id, evidence_json
                )
                VALUES (?, ?, ?, ?)
                """,
                (self.job_id, candidate_id, evidence_id, canonical_json(evidence)),
            )
        session = self.create_session()
        metadata = self.service.get_evidence(str(session["id"]), evidence_id)
        self.assertEqual(metadata["bounding_box"], [2, 2, 10, 10])
        content_type, content, headers = self.service.evidence_preview(
            str(session["id"]), evidence_id
        )
        self.assertEqual(content_type, "image/png")
        self.assertNotEqual(content, image_bytes)
        self.assertEqual(headers["X-QRA-Preview-Source"], "image/png")
        self.assertEqual(headers["Content-Disposition"], "inline")

        pdf_output = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=100)
        writer.write(pdf_output)
        pdf_bytes = pdf_output.getvalue()
        pdf_source_id = "SOURCE-PDF-EVIDENCE"
        pdf_evidence_id = "EVD-PDF-BBOX"
        pdf_candidate_id = "CAND-PDF-COORDINATE-SYSTEM"
        pdf_candidate = {
            **candidate,
            "candidate_id": pdf_candidate_id,
            "evidence_ids": [pdf_evidence_id],
            "extraction_method": "PDF_NATIVE_TEXT",
        }
        pdf_evidence = {
            "evidence_id": pdf_evidence_id,
            "source_type": "PDF",
            "excerpt": "EPSG:4547",
            "checksum_sha256": __import__("hashlib").sha256(pdf_bytes).hexdigest(),
            "location": {
                "kind": "PDF",
                "file_id": pdf_source_id,
                "page": 1,
                "coordinate_system": "PDF_POINTS_TOP_LEFT",
                "bbox": [20, 20, 80, 30],
                "page_size": [200, 100],
            },
        }
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO conversion_source(
                    job_id, id, file_name, media_type, relative_path,
                    original_file_name, declared_media_type, detected_media_type,
                    byte_count, sha256, source_kind, security_status, created_at, content
                ) VALUES (?, ?, 'scan.pdf', 'application/pdf', 'scan.pdf', 'scan.pdf',
                          'application/pdf', 'application/pdf', ?, ?, 'TOP_LEVEL',
                          'PARSED', ?, ?)
                """,
                (
                    self.job_id,
                    pdf_source_id,
                    len(pdf_bytes),
                    __import__("hashlib").sha256(pdf_bytes).hexdigest(),
                    "2026-08-31T00:00:00+00:00",
                    pdf_bytes,
                ),
            )
            connection.execute(
                """
                INSERT INTO candidate_field(
                    job_id, candidate_id, field_id, entity_type, entity_key,
                    extraction_method, confidence, quality_status, review_status,
                    source_unit, canonical_unit, normalized_value_json, payload_json
                ) VALUES (?, ?, ?, 'ASSESSMENT', 'GLOBAL', 'PDF_NATIVE_TEXT', 0.91,
                          'PASS', 'PENDING', NULL, NULL, ?, ?)
                """,
                (
                    self.job_id,
                    pdf_candidate_id,
                    pdf_candidate["field_id"],
                    canonical_json(pdf_candidate["normalized_value"]),
                    canonical_json(pdf_candidate),
                ),
            )
            connection.execute(
                """
                INSERT INTO candidate_evidence_link(
                    job_id, candidate_id, evidence_id, evidence_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    self.job_id,
                    pdf_candidate_id,
                    pdf_evidence_id,
                    canonical_json(pdf_evidence),
                ),
            )
        pdf_metadata = self.service.get_evidence(str(session["id"]), pdf_evidence_id)
        self.assertEqual(pdf_metadata["page_number"], 1)
        pdf_type, pdf_preview, pdf_headers = self.service.evidence_preview(
            str(session["id"]), pdf_evidence_id
        )
        self.assertEqual(pdf_type, "image/png")
        self.assertTrue(pdf_preview.startswith(b"\x89PNG"))
        self.assertEqual(pdf_headers["X-QRA-Preview-Source"], "application/pdf")
        self.assertEqual(pdf_headers["X-QRA-Preview-Page"], "1")

    def test_hashes_ignore_actor_time_and_database_ids(self) -> None:
        session = self.create_session()
        session_id = str(session["id"])
        wall = self.item(session_id, "segment.wall_thickness_mm")
        first = self.service.save_decision(
            session_id,
            review_item_key=str(wall["review_item_key"]),
            action="OVERRIDE_VALUE",
            selected_candidate_id=None,
            override_value=13,
            override_unit="mm",
            reason="稳定业务理由",
            actor="reviewer-a",
            expected_revision=int(session["revision"]),
        )
        gate_one = self.service.run_gate(session_id, actor="reviewer-a")
        current = self.service.get_session(session_id)
        second = self.service.save_decision(
            session_id,
            review_item_key=str(wall["review_item_key"]),
            action="OVERRIDE_VALUE",
            selected_candidate_id=None,
            override_value=13,
            override_unit="mm",
            reason="稳定业务理由",
            actor="different-reviewer",
            expected_revision=int(current["revision"]),
        )
        self.assertNotEqual(first["decision_id"], second["decision_id"])
        self.assertEqual(current["decision_set_hash"], second["session"]["decision_set_hash"])
        gate_two = self.service.run_gate(session_id, actor="different-approver")
        self.assertEqual(gate_one["payload_sha256"], gate_two["payload_sha256"])
        self.assertEqual(gate_one["result_hash"], gate_two["result_hash"])

    def test_confirm_is_atomic_snapshot_is_immutable_and_run_is_queued(self) -> None:
        session = self.create_session()
        session_id = str(session["id"])
        self.service.run_gate(session_id, actor="approver")
        current = self.service.get_session(session_id)
        with patch.object(
            QraDatabase, "_insert_snapshot_in_connection", side_effect=RuntimeError("fault")
        ):
            with self.assertRaises(RuntimeError):
                self.service.confirm(
                    session_id,
                    snapshot_name="失败快照",
                    reviewer="approver",
                    reason="故障注入",
                    expected_revision=int(current["revision"]),
                    expected_candidate_set_hash=str(current["candidate_set_hash"]),
                    expected_decision_set_hash=str(current["decision_set_hash"]),
                    run_after_confirm=False,
                    generate_charts=False,
                )
        self.assertEqual(self.database.list_snapshots(), [])
        self.assertFalse(
            any(
                event["event_type"] == "REVIEW_CONFIRMED"
                for event in self.database.list_audit_events(100)
            )
        )
        confirmed = self.confirm(session_id, run_after=True)
        self.assertIsNotNone(confirmed["run_id"])
        metadata = self.database.snapshot_metadata(str(confirmed["snapshot_id"]))
        self.assertEqual(metadata["payload_sha256"], confirmed["gate"]["payload_sha256"])
        self.assertEqual(len(metadata["review_confirmations"]), 1)
        with self.database.transaction() as connection:
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute(
                    "UPDATE input_snapshot SET payload_json = '{}' WHERE id = ?",
                    (confirmed["snapshot_id"],),
                )
        with self.assertRaises(ValueError):
            self.service.save_decision(
                session_id,
                review_item_key=str(self.item(session_id, "segment.start_km")["review_item_key"]),
                action="REJECT_ALL",
                selected_candidate_id=None,
                override_value=None,
                override_unit=None,
                reason="确认后禁止修改",
                actor="reviewer-a",
                expected_revision=int(self.service.get_session(session_id)["revision"]),
            )

    def test_confirmed_task_can_create_new_session_and_new_snapshot_without_mutating_old(
        self,
    ) -> None:
        first_session = self.create_session()
        first = self.confirm(str(first_session["id"]))
        old_payload = self.database.load_snapshot(str(first["snapshot_id"]))
        revised, created = self.service.create_or_resume_session(
            self.job_id, actor="reviewer-b", target_node_ids=["segment_geometry"]
        )
        self.assertTrue(created)
        item = self.item(str(revised["id"]), "segment.wall_thickness_mm")
        self.service.save_decision(
            str(revised["id"]),
            review_item_key=str(item["review_item_key"]),
            action="OVERRIDE_VALUE",
            selected_candidate_id=None,
            override_value=14,
            override_unit="mm",
            reason="修订版本使用复测壁厚",
            actor="reviewer-b",
            expected_revision=int(revised["revision"]),
        )
        second = self.confirm(str(revised["id"]))
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual(self.database.load_snapshot(str(first["snapshot_id"])), old_payload)

    def test_http_apis_workbench_and_409_are_real(self) -> None:
        handler = type("ReviewHandler", (QraRequestHandler,), {"database": self.database})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            base = f"http://{host}:{port}"

            def request(path: str, body: dict[str, object] | None = None) -> tuple[int, object]:
                data = json.dumps(body).encode("utf-8") if body is not None else None
                req = Request(
                    base + path,
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST" if body is not None else "GET",
                )
                with urlopen(req, timeout=20) as response:
                    raw = response.read()
                    return response.status, json.loads(raw.decode("utf-8"))

            with urlopen(base + f"/admin/reviews/{self.job_id}/", timeout=20) as response:
                page = response.read().decode("utf-8")
            self.assertIn("itemsColumn", page)
            self.assertIn("candidatesColumn", page)
            self.assertIn("evidenceColumn", page)
            self.assertNotIn("review_decisions", page)
            status, created = request(
                f"/admin/api/conversions/{self.job_id}/review-sessions",
                {"actor": "http-reviewer", "target_node_ids": ["segment_geometry"]},
            )
            self.assertEqual(status, 201)
            session = created["session"]
            _, items = request(f"/admin/api/review-sessions/{session['id']}/items?limit=500")
            item = items["items"][0]
            candidate = item["candidates"][0]
            decision_body = {
                "review_item_key": item["review_item_key"],
                "action": "ACCEPT_CANDIDATE",
                "selected_candidate_id": candidate["candidate_id"],
                "reason": "HTTP复核",
                "actor": "http-reviewer",
                "expected_revision": session["revision"],
            }
            status, _ = request(
                f"/admin/api/review-sessions/{session['id']}/decisions", decision_body
            )
            self.assertEqual(status, 201)
            with self.assertRaises(HTTPError) as raised:
                request(f"/admin/api/review-sessions/{session['id']}/decisions", decision_body)
            self.assertEqual(raised.exception.code, 409)
            conflict = json.loads(raised.exception.read().decode("utf-8"))
            self.assertEqual(conflict["error"], "REVIEW_REVISION_CONFLICT")
            evidence_id = candidate["evidence"][0]["evidence_id"]
            _, evidence = request(
                f"/admin/api/review-sessions/{session['id']}/evidence/{evidence_id}"
            )
            self.assertEqual(evidence["evidence_id"], evidence_id)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_deterministic_end_to_end_snapshot_calculation_report_and_trace(self) -> None:
        session = self.create_session()
        result = self.confirm(str(session["id"]), run_after=True)
        run_id = str(result["run_id"])
        execute_run(
            self.database,
            run_id,
            str(result["snapshot_id"]),
            targets=result["targets"],
            generate_charts=False,
            runtime_root=self.root / "calculation-runtime",
        )
        run = self.database.get_run(run_id)
        self.assertEqual(run["status"], "COMPLETED")
        self.assertTrue(self.database.list_nodes(run_id))
        self.assertTrue(
            any(
                row["path"] == "report_dashboard.html"
                for row in self.database.list_artifacts(run_id)
            )
        )
        metadata = self.database.snapshot_metadata(str(result["snapshot_id"]))
        review = metadata["review_confirmations"][0]
        self.assertEqual(review["conversion_job_id"], self.job_id)
        self.assertEqual(review["review_session_id"], session["id"])
        with self.database.session() as connection:
            counts = {
                table: connection.execute(
                    f"SELECT count(*) FROM {table} WHERE job_id = ?", (self.job_id,)
                ).fetchone()[0]
                for table in ("candidate_field", "candidate_evidence_link", "extracted_entity")
            }
        self.assertTrue(all(value > 0 for value in counts.values()))
        events = {row["event_type"] for row in self.database.list_audit_events(500)}
        self.assertTrue(
            {
                "REVIEW_SESSION_CREATED",
                "REVIEW_GATE_PASSED",
                "REVIEW_CONFIRMED",
                "RUN_COMPLETED",
            }.issubset(events)
        )

    def test_business_pages_do_not_require_pasted_decision_json(self) -> None:
        admin = admin_html().decode("utf-8")
        review = review_workbench_html(self.job_id).decode("utf-8")
        self.assertNotIn("review_decisions", admin)
        self.assertNotIn("粘贴复核决定", admin)
        self.assertIn("打开复核工作台", admin)
        self.assertIn("grid-template-columns", review)
        self.assertIn("@media(max-width:900px)", review)
        self.assertIn('aria-label="待复核字段列表"', review)
        self.assertIn('aria-label="候选值比较"', review)
        self.assertIn('aria-label="原始证据"', review)
        self.assertIn('id="problemFilter"', review)
        self.assertIn('id="fieldGroupFilter"', review)
        self.assertIn('id="sourceFilter"', review)
        self.assertIn('id="nodeFilter"', review)
        self.assertIn("context-table", review)


if __name__ == "__main__":
    unittest.main()
