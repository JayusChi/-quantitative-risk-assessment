from __future__ import annotations

import base64
import io
import json
import sqlite3
import tempfile
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image

from db_qra.admin_ui import admin_html
from db_qra.conversion_adapter import run_conversion_job, submit_conversion
from db_qra.database import SCHEMA_VERSION, QraDatabase
from db_qra.server import QraRequestHandler

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MVP_SOURCES = PROJECT_ROOT / "tests" / "fixtures" / "converter_mvp"


def source_files() -> list[dict[str, object]]:
    return [
        {"file_name": path.name, "content": path.read_bytes()}
        for path in sorted(MVP_SOURCES.glob("*.csv"))
    ]


def archive_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return output.getvalue()


class FileIntakePhase2IntegrationTest(unittest.TestCase):
    def test_decodeable_jpeg_with_small_trailing_metadata_enters_parse_queue(self) -> None:
        image_output = io.BytesIO()
        Image.new("RGB", (800, 600), "white").save(image_output, format="JPEG")
        content = image_output.getvalue() + b"wechat-trailing-metadata"
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = QraDatabase(Path(temporary_directory) / "qra.sqlite3")
            job_id, created = submit_conversion(
                database,
                profile="generic.multisource-review.v2",
                files=[
                    {
                        "file_name": "scan.jpg",
                        "media_type": "image/jpeg",
                        "content": content,
                    }
                ],
                project_name="JPEG尾随数据兼容测试",
            )
            job = database.get_conversion_job(job_id)
        self.assertTrue(created)
        self.assertEqual(job["status"], "QUEUED")
        self.assertEqual(job["sources"][0]["security_status"], "READY_FOR_PARSE")
        self.assertNotIn(
            "INTAKE.JPEG_TRAILING_DATA",
            {issue["code"] for issue in job["intake_issues"]},
        )

    def test_legacy_conversion_source_schema_is_migrated_without_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "legacy.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE conversion_source (
                    job_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    byte_count INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    content BLOB NOT NULL,
                    PRIMARY KEY (job_id, file_name)
                );
                INSERT INTO conversion_source(
                    job_id, file_name, media_type, byte_count, sha256, content
                ) VALUES ('LEGACY-JOB', 'legacy.csv', 'text/csv', 4, 'legacy-hash', x'612C620A');
                """
            )
            connection.commit()
            connection.close()

            database = QraDatabase(path)
            database.initialize()
            with database.session() as migrated:
                columns = {
                    str(row["name"])
                    for row in migrated.execute(
                        'PRAGMA table_info("conversion_source")'
                    ).fetchall()
                }
                row = migrated.execute(
                    """
                    SELECT id, relative_path, declared_media_type,
                           detected_media_type, security_status, created_at
                    FROM conversion_source WHERE job_id = 'LEGACY-JOB'
                    """
                ).fetchone()
                schema_versions = {
                    str(item["version"])
                    for item in migrated.execute("SELECT version FROM db_schema").fetchall()
                }
            self.assertTrue(
                {
                    "id",
                    "relative_path",
                    "declared_media_type",
                    "detected_media_type",
                    "security_status",
                    "created_at",
                }.issubset(columns)
            )
            self.assertTrue(str(row["id"]).startswith("SOURCE-"))
            self.assertEqual(row["relative_path"], "legacy.csv")
            self.assertEqual(row["security_status"], "READY_FOR_PARSE")
            self.assertIn(SCHEMA_VERSION, schema_versions)

    def test_schema_sources_cross_task_duplicate_and_cancel_state_machine(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = QraDatabase(Path(temporary_directory) / "qra.sqlite3")
            first_id, _ = submit_conversion(
                database,
                profile="generic.structured-mvp.v1",
                files=source_files(),
                project_name="入口登记一",
            )
            first = database.get_conversion_job(first_id)
            self.assertEqual(SCHEMA_VERSION, "2.0.0")
            self.assertEqual(first["failure_policy"], "ALL_OR_NOTHING")
            self.assertEqual(first["contract_id"], "qra.part1-input")
            self.assertEqual(first["contract_version"], "1.0.0")
            self.assertEqual(len(first["file_manifest_sha256"]), 64)
            self.assertTrue(
                all(source["security_status"] == "READY_FOR_PARSE" for source in first["sources"])
            )

            second_id, _ = submit_conversion(
                database,
                profile="generic.multisource-review.v2",
                files=source_files(),
                project_name="入口登记二",
            )
            second = database.get_conversion_job(second_id)
            self.assertTrue(
                all(source["duplicate_of_source_id"] for source in second["sources"])
            )

            cancelled = database.request_conversion_cancel(first_id, actor="intake-reviewer")
            self.assertEqual(cancelled["status"], "CANCELLED")
            self.assertEqual(
                database.request_conversion_cancel(first_id, actor="intake-reviewer")["status"],
                "CANCELLED",
            )
            with self.assertRaisesRegex(ValueError, "只有阻断或失败"):
                database.retry_conversion_job(first_id)
            event_types = {
                event["event_type"] for event in database.list_conversion_events(first_id)
            }
            self.assertTrue(
                {
                    "SOURCE_RECEIVED",
                    "CONVERSION_QUEUED",
                    "CONVERSION_CANCEL_REQUESTED",
                    "CONVERSION_CANCELLED",
                }.issubset(event_types)
            )

            running_id, _ = submit_conversion(
                database,
                profile="generic.structured-mvp.v1",
                files=[
                    {
                        "file_name": "fresh.csv",
                        "content": b"segment_id,value\nSYN-NEW,3\n",
                    }
                ],
            )
            database.set_conversion_running(running_id)
            pending = database.request_conversion_cancel(running_id, actor="worker-reviewer")
            self.assertEqual(pending["status"], "RUNNING")
            self.assertIsNotNone(pending["cancel_requested_at"])
            database.finalize_conversion_cancel(running_id)
            self.assertEqual(database.get_conversion_job(running_id)["status"], "CANCELLED")

    def test_zip_members_are_persisted_and_only_ready_members_reach_converter(self) -> None:
        entries = [
            (f"资料/{path.name}", path.read_bytes())
            for path in sorted(MVP_SOURCES.glob("*.csv"))
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = QraDatabase(root / "qra.sqlite3")
            job_id, _ = submit_conversion(
                database,
                profile="generic.structured-mvp.v1",
                files=[{"file_name": "资料包.zip", "content": archive_bytes(entries)}],
                project_name="ZIP安全展开",
            )
            submitted = database.get_conversion_job(job_id)
            self.assertEqual(submitted["status"], "QUEUED")
            self.assertEqual(len(submitted["sources"]), len(entries) + 1)
            archive_source = next(
                source for source in submitted["sources"] if source["archive_name"] is None
            )
            self.assertEqual(archive_source["security_status"], "VALIDATED")
            members = [source for source in submitted["sources"] if source["archive_name"]]
            self.assertTrue(
                all(source["archive_member_path"].startswith("资料/") for source in members)
            )
            completed = run_conversion_job(database, job_id, runtime_root=root / "runtime")
            self.assertEqual(completed["status"], "READY_FOR_CONFIRMATION")
            self.assertTrue(
                all(
                    source["security_status"] == "PARSED"
                    for source in database.list_conversion_sources(job_id)
                    if source["archive_name"]
                )
            )

    def test_default_policy_blocks_unsafe_zip_without_starting_parser(self) -> None:
        unsafe = archive_bytes([("../escape.csv", b"id,value\nA,1\n")])
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = QraDatabase(Path(temporary_directory) / "qra.sqlite3")
            job_id, _ = submit_conversion(
                database,
                profile="generic.structured-mvp.v1",
                files=[{"file_name": "unsafe.zip", "content": unsafe}],
            )
            job = database.get_conversion_job(job_id)
            self.assertEqual(job["status"], "BLOCKED")
            self.assertEqual(job["sources"][0]["security_status"], "QUARANTINED")
            self.assertEqual(job["intake_issues"][0]["code"], "INTAKE.ZIP_PATH_TRAVERSAL")
            self.assertEqual(run_conversion_job(database, job_id)["status"], "BLOCKED")
            self.assertFalse(database.conversion_source_contents(job_id, ready_only=True))

    def test_http_sources_events_cancel_filters_and_page_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = QraDatabase(Path(temporary_directory) / "qra.sqlite3")
            queued_id, _ = submit_conversion(
                database,
                profile="generic.structured-mvp.v1",
                files=source_files(),
                project_name="等待API取消",
            )
            with self.assertRaisesRegex(ValueError, "请先取消"):
                database.delete_conversion_job(queued_id)
            handler = type("IntakeHandler", (QraRequestHandler,), {"database": database})
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address
                base = f"http://{host}:{port}"

                def request_json(
                    path: str,
                    *,
                    method: str = "GET",
                    body: dict[str, object] | None = None,
                ) -> tuple[int, object]:
                    data = (
                        json.dumps(body, ensure_ascii=False).encode("utf-8")
                        if body is not None
                        else None
                    )
                    request = Request(
                        base + path,
                        data=data,
                        method=method,
                        headers={"Content-Type": "application/json"},
                    )
                    with urlopen(request, timeout=10) as response:
                        return response.status, json.loads(response.read().decode("utf-8"))

                _, sources = request_json(f"/admin/api/conversions/{queued_id}/sources")
                self.assertEqual(len(sources), len(source_files()))
                self.assertTrue(all("content" not in source for source in sources))
                _, events = request_json(f"/admin/api/conversions/{queued_id}/events")
                self.assertGreaterEqual(len(events), len(source_files()) + 1)
                _, cancelled = request_json(
                    f"/admin/api/conversions/{queued_id}/cancel",
                    method="POST",
                    body={"actor": "api-reviewer"},
                )
                self.assertEqual(cancelled["job"]["status"], "CANCELLED")
                _, cancelled_again = request_json(
                    f"/admin/api/conversions/{queued_id}/cancel", method="POST"
                )
                self.assertEqual(cancelled_again["job"]["status"], "CANCELLED")
                _, filtered = request_json("/admin/api/conversions?status=CANCELLED&limit=1")
                self.assertEqual([row["id"] for row in filtered], [queued_id])
                delete_status, deleted = request_json(
                    f"/admin/api/conversions/{queued_id}", method="DELETE"
                )
                self.assertEqual(delete_status, 200)
                self.assertEqual(deleted["status"], "DELETED")
                self.assertEqual(deleted["conversion_id"], queued_id)
                _, filtered_after_delete = request_json(
                    "/admin/api/conversions?status=CANCELLED&limit=1"
                )
                self.assertEqual(filtered_after_delete, [])

                unsafe = archive_bytes([("../escape.csv", b"id,value\nA,1\n")])
                status, submitted = request_json(
                    "/admin/api/conversions",
                    method="POST",
                    body={
                        "profile": "generic.structured-mvp.v1",
                        "contract": "qra.part1-input/1.0.0",
                        "failure_policy": "ALL_OR_NOTHING",
                        "files": [
                            {
                                "file_name": "unsafe.zip",
                                "content_base64": base64.b64encode(unsafe).decode("ascii"),
                            }
                        ],
                    },
                )
                self.assertEqual(status, 202)
                self.assertEqual(submitted["job"]["status"], "BLOCKED")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        html = admin_html().decode("utf-8")
        self.assertIn("XMLHttpRequest", html)
        self.assertIn("xhr.upload.onprogress", html)
        self.assertIn("state.conversionXhr.abort()", html)
        self.assertIn("data-remove-conversion-file", html)
        self.assertIn("data-cancel-conversion", html)
        self.assertIn("textContent=file.name", html)
        self.assertIn(".png,.jpg,.jpeg", html)
        self.assertNotIn("本地发送 0%", html)
        self.assertIn("已选择，等待提交", html)
        self.assertIn('id="conversionProjectNameError"', html)
        self.assertIn('id="conversionFormError"', html)
        self.assertIn("showConversionFormError", html)
        self.assertIn("不代表图片 OCR 失败", html)
        self.assertIn("data-delete-conversion", html)
        self.assertIn("永久删除", html)


if __name__ == "__main__":
    unittest.main()
