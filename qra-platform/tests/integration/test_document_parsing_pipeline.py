from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from PIL import Image, ImageDraw

from db_qra.conversion_adapter import run_conversion_job, submit_conversion
from db_qra.database import QraDatabase
from db_qra.server import QraRequestHandler
from qra_converter.parsing.pipeline import (
    ParsingPipeline,
    configured_ocr_provider,
    real_ocr_configured,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MVP_SOURCES = PROJECT_ROOT / "tests" / "fixtures" / "converter_mvp"


def mvp_files() -> list[dict[str, object]]:
    return [
        {"file_name": path.name, "content": path.read_bytes()}
        for path in sorted(MVP_SOURCES.glob("*.csv"))
    ]


class DocumentParsingPipelineIntegrationTest(unittest.TestCase):
    def test_ready_sources_persist_independent_parse_state_quality_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = QraDatabase(root / "qra.sqlite3")
            job_id, _ = submit_conversion(
                database,
                profile="generic.structured-mvp.v1",
                files=mvp_files(),
                project_name="第三阶段完整链路",
            )
            completed = run_conversion_job(database, job_id, runtime_root=root / "runtime")
            sources = database.list_conversion_sources(job_id)
            artifacts = database.list_conversion_parse_artifacts(job_id)
            handler = type(
                "Stage3ArtifactHandler",
                (QraRequestHandler,),
                {"database": database},
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address
                source_id = str(sources[0]["id"])
                base = (
                    f"http://{host}:{port}/admin/api/conversions/{job_id}/"
                    f"sources/{source_id}/artifacts"
                )
                with urlopen(base, timeout=5) as response:
                    api_artifacts = json.loads(response.read().decode("utf-8"))
                with urlopen(
                    f"{base}?{urlencode({'path': 'parsed_document.json'})}",
                    timeout=5,
                ) as response:
                    api_document = json.loads(response.read().decode("utf-8"))
                    api_etag = response.headers["ETag"]
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
        self.assertEqual(completed["status"], "READY_FOR_CONFIRMATION")
        self.assertTrue(all(source["security_status"] == "PARSED" for source in sources))
        self.assertTrue(all(source["parser_id"] == "csv/stdlib" for source in sources))
        self.assertTrue(all(len(source["parse_sha256"]) == 64 for source in sources))
        self.assertTrue(all(source["parse_quality"]["table_count"] == 1 for source in sources))
        self.assertEqual(len(artifacts), len(sources) * 3)
        self.assertEqual(
            {artifact["artifact_kind"] for artifact in artifacts},
            {"PARSED_DOCUMENT", "QUALITY_REPORT", "PREVIEW_MANIFEST"},
        )
        self.assertEqual(len(api_artifacts), 3)
        self.assertEqual(api_document["document_id"], source_id)
        self.assertEqual(len(api_etag), 64)

    def test_unconfigured_image_ocr_marks_only_that_source_failed_and_keeps_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image_path = root / "scan.png"
            Image.new("RGB", (800, 600), "white").save(image_path)
            original = image_path.read_bytes()
            database = QraDatabase(root / "qra.sqlite3")
            job_id, _ = submit_conversion(
                database,
                profile="generic.structured-mvp.v1",
                files=[{"file_name": "scan.png", "content": original}],
                project_name="未配置OCR降级",
            )
            completed = run_conversion_job(database, job_id, runtime_root=root / "runtime")
            source = database.list_conversion_sources(job_id)[0]
            stored = database.get_conversion_parse_artifact(
                job_id, source["id"], "parsed_document.json"
            )
            protected_source = database.conversion_source_contents(job_id)[0]
        self.assertEqual(completed["status"], "BLOCKED")
        self.assertEqual(source["security_status"], "PARSE_FAILED")
        self.assertEqual(source["security_issue_code"], "PARSE.OCR_REQUIRED")
        self.assertIsNotNone(stored)
        parsed = json.loads(stored[1].decode("utf-8"))
        self.assertIn(
            "PARSE.OCR_PROVIDER_NOT_CONFIGURED",
            {issue["code"] for issue in parsed["issues"]},
        )
        self.assertEqual(protected_source["content"], original)

    def test_terminal_conversion_delete_cascades_sources_and_parse_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = QraDatabase(root / "qra.sqlite3")
            job_id, _ = submit_conversion(
                database,
                profile="generic.structured-mvp.v1",
                files=mvp_files(),
                project_name="待删除OCR转换记录",
            )
            completed = run_conversion_job(database, job_id, runtime_root=root / "runtime")
            self.assertEqual(completed["status"], "READY_FOR_CONFIRMATION")
            artifact_count = len(database.list_conversion_parse_artifacts(job_id))
            self.assertGreater(artifact_count, 0)

            deleted = database.delete_conversion_job(job_id, actor="delete-tester")

            self.assertEqual(deleted["status"], "DELETED")
            self.assertEqual(deleted["deleted_artifact_count"], artifact_count)
            self.assertEqual(database.list_conversion_parse_artifacts(job_id), [])
            with self.assertRaises(KeyError):
                database.get_conversion_job(job_id)
            deletion_events = [
                event
                for event in database.list_audit_events(100)
                if event["entity_id"] == job_id
            ]
            self.assertEqual(
                [event["event_type"] for event in deletion_events],
                ["CONVERSION_DELETED"],
            )

    @unittest.skipUnless(
        real_ocr_configured(),
        "部署环境未配置真实OCR；正式扫描件验收必须显式配置后运行",
    )
    def test_real_ocr_provider_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "real-ocr-smoke.png"
            image = Image.new("RGB", (1200, 800), "white")
            ImageDraw.Draw(image).text((100, 100), "QRA REAL OCR SMOKE 001", fill="black")
            image.save(path)
            execution = ParsingPipeline(
                output_root=root / "out", ocr_provider=configured_ocr_provider()
            ).parse_path(path)
        self.assertTrue(execution.succeeded)
        self.assertTrue(execution.document.pages[0].text_blocks)
        self.assertNotEqual(execution.document.metadata["ocr"]["provider_id"], "fixture")


if __name__ == "__main__":
    unittest.main()
