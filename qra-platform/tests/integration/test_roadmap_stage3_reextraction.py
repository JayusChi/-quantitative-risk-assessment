from __future__ import annotations

import base64
import io
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw

from db_qra.conversion_adapter import run_conversion_job, submit_conversion
from db_qra.database import QraDatabase, json_sha256
from db_qra.reextraction_worker import ReextractionWorker
from db_qra.review_service import ReviewService
from db_qra.server import QraRequestHandler
from qra_converter.extraction.ports import ExtractionRequest, ExtractionResponse
from qra_converter.ocr.fixture_provider import FixtureOcrProvider
from qra_converter.ocr.payload_policy import OcrPayloadPolicy
from qra_converter.ocr.ports import OcrResponse, OcrTextBlock
from qra_converter.parsing.contracts import BoundingBox

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MVP_SOURCES = PROJECT_ROOT / "tests" / "fixtures" / "converter_mvp"


class _FieldProvider:
    provider_id = "field-fixture"
    model_version = "field-fixture-v1"
    deployment_scope = "LOCAL"
    max_retries = 0
    max_concurrency = 1

    def __init__(self, entity_key: str) -> None:
        self.calls: list[ExtractionRequest] = []
        self.entity_key = entity_key

    def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        self.calls.append(request)
        entity = next(
            (
                item
                for item in request.entity_context
                if str(item.get("business_key")) == self.entity_key
            ),
            None,
        )
        if entity is None:
            raise RuntimeError(
                self.entity_key
                + " != "
                + repr([str(item.get("business_key")) for item in request.entity_context])
            )
        block = next(
            item for item in request.document_blocks if str(item.get("text")).strip() == "12"
        )
        evidence_id = str(block["evidence_id"])
        return ExtractionResponse(
            provider_id=self.provider_id,
            model_id="fixture",
            model_version=self.model_version,
            structured_output={
                "items": [
                    {
                        "candidate_id": None,
                        "field_id": request.field_subset[0],
                        "entity_id": entity["entity_id"],
                        "raw_value": "12",
                        "source_unit": "mm",
                        "normalized_value": None,
                        "confidence": 0.99,
                        "evidence_ids": [evidence_id],
                        "not_found": False,
                        "effective_from": None,
                        "effective_to": None,
                    }
                ]
            },
            provider_request_id="fixture-reextract-1",
        )


class _BudgetedOcrProvider:
    provider_id = "budgeted-ocr-fixture"
    model_version = "budgeted-v1"

    def __init__(self) -> None:
        self.payload_policy = OcrPayloadPolicy(
            max_data_uri_bytes=700_000,
            max_http_body_bytes=750_000,
            model_max_pixels=4_000_000,
            maximum_tiles_per_page=64,
        )
        self.request_byte_counts: list[int] = []

    def build_request_bytes(self, request) -> bytes:
        return json.dumps(
            {
                "model": self.model_version,
                "image": (
                    f"data:{request.image_content_type};base64,"
                    + base64.b64encode(request.image_bytes).decode("ascii")
                ),
                "task": request.task_type,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def recognize(self, request) -> OcrResponse:
        request_bytes = self.build_request_bytes(request)
        if len(request_bytes) > self.payload_policy.max_http_body_bytes:
            raise AssertionError("planner emitted an oversized outbound request")
        self.request_byte_counts.append(len(request_bytes))
        return OcrResponse(
            self.provider_id,
            self.model_version,
            text_blocks=(
                OcrTextBlock(
                    "合成预算证据",
                    BoundingBox(0, 0, float(request.width), float(request.height)),
                    0.98,
                ),
            ),
            raw_response_sha256="8" * 64,
        )
class RoadmapStage3ReextractionTests(unittest.TestCase):
    def test_admin_api_accepts_about_six_mib_image_and_all_ocr_requests_fit_budget(self) -> None:
        image = Image.effect_noise((2800, 2800), 80).convert("L")
        encoded = io.BytesIO()
        image.save(encoded, format="JPEG", quality=90)
        content = encoded.getvalue()
        self.assertGreater(len(content), 5_500_000)
        self.assertLess(len(content), 7_000_000)
        provider = _BudgetedOcrProvider()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = QraDatabase(root / "qra.sqlite3")
            handler = type(
                "RoadmapStage3Handler", (QraRequestHandler,), {"database": database}
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with (
                patch(
                    "db_qra.conversion_adapter.configured_ocr_provider",
                    return_value=provider,
                ),
                patch("db_qra.server.RUNTIME_ROOT", root / "runtime"),
            ):
                thread.start()
                try:
                    host, port = server.server_address
                    body = json.dumps(
                        {
                            "profile": "generic.structured-mvp.v1",
                            "project_name": "合成大图预算回归",
                            "files": [
                                {
                                    "file_name": "synthetic-large.jpg",
                                    "content_base64": base64.b64encode(content).decode("ascii"),
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ).encode("utf-8")
                    request = Request(
                        f"http://{host}:{port}/admin/api/conversions",
                        data=body,
                        method="POST",
                        headers={
                            "Content-Type": "application/json",
                            "X-QRA-Actor": "stage3-test",
                        },
                    )
                    with urlopen(request, timeout=30) as response:
                        submitted = json.loads(response.read().decode("utf-8"))
                    job_id = str(submitted["job"]["id"])
                    final = submitted["job"]
                    for _ in range(300):
                        with urlopen(
                            f"http://{host}:{port}/admin/api/conversions/{job_id}",
                            timeout=10,
                        ) as response:
                            final = json.loads(response.read().decode("utf-8"))
                        if final["status"] not in {"QUEUED", "RUNNING"}:
                            break
                        time.sleep(0.05)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)
            source = database.list_conversion_sources(job_id)[0]
            audits = database.list_conversion_model_calls(job_id)
        self.assertNotIn(final["status"], {"FAILED", "QUEUED", "RUNNING"})
        self.assertEqual(source["security_status"], "PARSED")
        self.assertGreater(len(provider.request_byte_counts), 1)
        self.assertTrue(
            all(
                count <= provider.payload_policy.max_http_body_bytes
                for count in provider.request_byte_counts
            )
        )
        self.assertGreaterEqual(audits["summary"]["ocr_record_count"], 1)
        self.assertTrue(
            any(item["status"] == "COMPLETED" for item in audits["items"])
        )

    def test_file_scope_runs_from_protected_source_and_versions_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = QraDatabase(root / "qra.sqlite3")
            job_id, _ = submit_conversion(
                database,
                profile="generic.structured-mvp.v1",
                files=[
                    {"file_name": path.name, "content": path.read_bytes()}
                    for path in sorted(MVP_SOURCES.glob("*.csv"))
                ],
            )
            run_conversion_job(database, job_id, runtime_root=root / "runtime")
            service = ReviewService(database)
            session, _ = service.create_or_resume_session(
                job_id, actor="reviewer", target_node_ids=["segment_geometry"]
            )
            source = database.list_conversion_sources(job_id)[0]
            request, created = service.enqueue_reextraction(
                str(session["id"]),
                scope="FILE",
                source_id=str(source["id"]),
                page_number=None,
                field_id=None,
                entity_id=None,
                evidence_id=None,
                requested_parameters={},
                reason="按新OCR与提取预算重新运行文件",
                actor="reviewer",
            )
            self.assertTrue(created)
            with database.transaction() as connection:
                connection.execute(
                    """
                    UPDATE reextraction_request
                    SET status = 'RUNNING', started_at = 'interrupted'
                    WHERE id = ?
                    """,
                    (request["id"],),
                )
            self.assertIn(
                str(request["id"]), database.requeue_interrupted_reextractions()
            )
            result = ReextractionWorker(
                database, runtime_root=root / "reextraction"
            ).run(str(request["id"]))
            self.assertIn(result["status"], {"COMPLETED", "PARTIAL"}, result)
            self.assertTrue(result["result_parse_sha256"])
            with database.session() as connection:
                artifact_versions = int(
                    connection.execute(
                        """
                        SELECT count(*)
                        FROM conversion_parse_artifact_version AS artifact
                        JOIN conversion_parse_version AS version
                          ON version.id = artifact.version_id
                        WHERE version.job_id = ? AND version.source_id = ?
                        """,
                        (job_id, source["id"]),
                    ).fetchone()[0]
                )
            self.assertGreaterEqual(artifact_versions, 6)

    def test_page_scope_replaces_only_selected_pdf_page_and_preserves_other_candidates(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scan_path = root / "scan.pdf"
            scan = Image.new("RGB", (800, 1000), "white")
            ImageDraw.Draw(scan).text((80, 100), "SYNTHETIC PAGE EVIDENCE", fill="black")
            scan.save(scan_path, "PDF", resolution=150)
            database = QraDatabase(root / "qra.sqlite3")
            files = [
                {"file_name": path.name, "content": path.read_bytes()}
                for path in sorted(MVP_SOURCES.glob("*.csv"))
            ]
            files.append({"file_name": "scan.pdf", "content": scan_path.read_bytes()})
            job_id, _ = submit_conversion(
                database,
                profile="generic.structured-mvp.v1",
                files=files,
                failure_policy="QUARANTINE_AND_CONTINUE",
            )
            run_conversion_job(database, job_id, runtime_root=root / "runtime")
            service = ReviewService(database)
            session, _ = service.create_or_resume_session(
                job_id, actor="reviewer", target_node_ids=["segment_geometry"]
            )
            pdf_source = next(
                row
                for row in database.list_conversion_sources(job_id)
                if row["media_type"] == "application/pdf"
            )
            with database.session() as connection:
                non_pdf_before = [
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT payload_json FROM candidate_field
                        WHERE job_id = ? ORDER BY candidate_id
                        """,
                        (job_id,),
                    ).fetchall()
                ]
            request, _ = service.enqueue_reextraction(
                str(session["id"]),
                scope="PAGE",
                source_id=str(pdf_source["id"]),
                page_number=1,
                field_id=None,
                entity_id=None,
                evidence_id=None,
                requested_parameters={},
                reason="仅重新识别PDF第一页",
                actor="reviewer",
            )
            response = OcrResponse(
                "fixture",
                "fixture-v1",
                text_blocks=(
                    OcrTextBlock("合成页证据", BoundingBox(80, 100, 200, 40), 0.98),
                ),
                raw_response_sha256="9" * 64,
            )
            result = ReextractionWorker(
                database,
                runtime_root=root / "reextraction",
                ocr_provider=FixtureOcrProvider(
                    {f"{pdf_source['id']}:page-1:image-1": response}
                ),
            ).run(str(request["id"]))
            self.assertIn(result["status"], {"COMPLETED", "PARTIAL"}, result)
            self.assertTrue(result["result_parse_sha256"])
            with database.session() as connection:
                non_pdf_after = [
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT payload_json FROM candidate_field
                        WHERE job_id = ? ORDER BY candidate_id
                        """,
                        (job_id,),
                    ).fetchall()
                ]
            self.assertEqual(json_sha256(non_pdf_before), json_sha256(non_pdf_after))

    def test_field_worker_is_scoped_versioned_audited_and_stales_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = QraDatabase(root / "qra.sqlite3")
            job_id, _ = submit_conversion(
                database,
                profile="generic.structured-mvp.v1",
                files=[
                    {"file_name": path.name, "content": path.read_bytes()}
                    for path in sorted(MVP_SOURCES.glob("*.csv"))
                ],
                project_name="字段重提取测试",
            )
            run_conversion_job(database, job_id, runtime_root=root / "runtime")
            service = ReviewService(database)
            session, _ = service.create_or_resume_session(
                job_id, actor="reviewer", target_node_ids=["segment_geometry"]
            )
            items = service.list_items(str(session["id"]), limit=500)["items"]
            item = next(row for row in items if row["field_id"] == "segment.wall_thickness_mm")
            evidence = item["candidates"][0]["evidence"][0]
            with database.session() as connection:
                non_target_before = [
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT payload_json FROM candidate_field
                        WHERE job_id = ? AND field_id <> ? ORDER BY candidate_id
                        """,
                        (job_id, item["field_id"]),
                    ).fetchall()
                ]
            request, created = service.enqueue_reextraction(
                str(session["id"]),
                scope="FIELD",
                source_id=str(evidence["location"]["file_id"]),
                page_number=None,
                field_id=str(item["field_id"]),
                entity_id=str(item["entity_id"]),
                evidence_id=str(evidence["evidence_id"]),
                requested_parameters={},
                reason="使用新请求预算策略重新提取该字段",
                actor="reviewer",
            )
            self.assertTrue(created)
            provider = _FieldProvider(str(item["entity_id"]))
            result = ReextractionWorker(
                database,
                runtime_root=root / "reextraction",
                extraction_provider=provider,
            ).run(str(request["id"]))
            self.assertEqual(result["status"], "COMPLETED", result)
            self.assertEqual({call.task_type for call in provider.calls}, {"EXTRACT_FIELDS"})
            self.assertEqual(service.get_session(str(session["id"]))["status"], "STALE")
            with database.session() as connection:
                non_target_after = [
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT payload_json FROM candidate_field
                        WHERE job_id = ? AND field_id <> ? ORDER BY candidate_id
                        """,
                        (job_id, item["field_id"]),
                    ).fetchall()
                ]
                versions = int(
                    connection.execute(
                        "SELECT count(*) FROM candidate_set_version WHERE job_id = ?",
                        (job_id,),
                    ).fetchone()[0]
                )
                audits = int(
                    connection.execute(
                        "SELECT count(*) FROM model_call_audit WHERE job_id = ?",
                        (job_id,),
                    ).fetchone()[0]
                )
            self.assertEqual(json_sha256(non_target_before), json_sha256(non_target_after))
            self.assertEqual(versions, 2)
            self.assertGreaterEqual(audits, 1)


if __name__ == "__main__":
    unittest.main()
