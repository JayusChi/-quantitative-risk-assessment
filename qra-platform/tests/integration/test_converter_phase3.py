from __future__ import annotations

import base64
import io
import json
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from db_qra.cli import DEFAULT_SERVER_PORT, build_parser
from db_qra.conversion_adapter import run_conversion_job, submit_conversion
from db_qra.database import QraDatabase
from db_qra.server import QraRequestHandler
from qra_converter.service import convert_sources
from qra_engine.dynamic import plan_dynamic_flow
from qra_engine.validation import validate_import_contract

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MVP_SOURCES = PROJECT_ROOT / "tests" / "fixtures" / "converter_mvp"
MVP_PROFILE = PROJECT_ROOT / "resources" / "mappings" / "generic" / "generic.structured-mvp.v1.json"
PHASE2_SOURCES = PROJECT_ROOT / "tests" / "fixtures" / "converter_phase2"


def source_files() -> list[dict[str, object]]:
    return [
        {"file_name": path.name, "content": path.read_bytes()}
        for path in sorted(MVP_SOURCES.glob("*.csv"))
    ]


class ConverterPhase3IntegrationTest(unittest.TestCase):
    def test_admin_server_uses_project_default_port(self) -> None:
        args = build_parser().parse_args(["serve"])
        self.assertEqual(DEFAULT_SERVER_PORT, 8766)
        self.assertEqual(args.port, DEFAULT_SERVER_PORT)

    def test_task_deduplication_confirmation_provenance_and_cli_parity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = QraDatabase(root / "qra.sqlite3")
            job_id, created = submit_conversion(
                database,
                profile="generic.structured-mvp.v1",
                files=source_files(),
                case_id="PHASE3-PARITY",
                project_name="第三阶段一致性测试",
                actor="phase3-tester",
            )
            self.assertTrue(created)
            duplicate_id, duplicate_created = submit_conversion(
                database,
                profile="generic.structured-mvp.v1",
                files=list(reversed(source_files())),
                case_id="IGNORED-BY-SOURCE-DEDUPE",
                project_name="重复提交",
                actor="phase3-tester",
            )
            self.assertEqual(duplicate_id, job_id)
            self.assertFalse(duplicate_created)

            job = run_conversion_job(database, job_id, runtime_root=root / "runtime")
            self.assertEqual(job["status"], "READY_FOR_CONFIRMATION")
            details = database.get_conversion_job(job_id)
            self.assertEqual(details["preview"]["status"], "READY_FOR_REVIEW")
            self.assertEqual(details["source_count"], 5)

            cli_output = root / "cli-output"
            cli_summary = convert_sources(
                source_dir=MVP_SOURCES,
                profile_path=MVP_PROFILE,
                output_dir=cli_output,
                case_id="PHASE3-PARITY",
                project_name="第三阶段一致性测试",
                contract_validator=validate_import_contract,
                capability_planner=lambda case: plan_dynamic_flow(case, None),
            )
            self.assertEqual(cli_summary["status"], "READY_FOR_REVIEW")
            cli_case = json.loads((cli_output / "case.json").read_text(encoding="utf-8"))
            self.assertEqual(details["payload"], cli_case)

            snapshot_id, snapshot_created = database.confirm_conversion(
                job_id,
                name="第三阶段转换快照",
                reviewer="reviewer-a",
                reason="已核对转换预览、来源和单位换算",
            )
            self.assertTrue(snapshot_created)
            self.assertEqual(database.load_snapshot(snapshot_id), cli_case)
            metadata = database.snapshot_metadata(snapshot_id)
            self.assertEqual(metadata["payload_sha256"], details["case_sha256"])
            self.assertEqual(
                metadata["conversion"]["mapping_profile_id"],
                "generic.structured-mvp.v1",
            )
            self.assertEqual(metadata["conversion"]["conversion_job_id"], job_id)
            self.assertEqual(metadata["conversion"]["confirmed_by"], "reviewer-a")
            with self.assertRaisesRegex(ValueError, "已确认转换"):
                database.delete_snapshot(snapshot_id)

            event_types = {row["event_type"] for row in database.list_audit_events(100)}
            self.assertTrue(
                {
                    "CONVERSION_QUEUED",
                    "CONVERSION_STARTED",
                    "CONVERSION_READY",
                    "CONVERSION_CONFIRMED",
                    "SNAPSHOT_IMPORTED",
                }.issubset(event_types)
            )

    def test_blocked_conversion_review_retry_and_restart_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = QraDatabase(root / "qra.sqlite3")
            files = [
                {"file_name": path.name, "content": path.read_bytes()}
                for path in sorted(PHASE2_SOURCES.glob("*.csv"))
            ]
            job_id, _ = submit_conversion(
                database,
                profile="generic.multisource-review.v2",
                files=files,
                project_name="第三阶段复核重试",
            )
            blocked = run_conversion_job(database, job_id, runtime_root=root / "runtime")
            self.assertEqual(blocked["status"], "BLOCKED")
            self.assertEqual(blocked["error"]["code"], "CONVERSION_BLOCKED")

            decisions = json.loads(
                (PHASE2_SOURCES / "review_decisions.json").read_text(encoding="utf-8")
            )
            retry_id = database.retry_conversion_job(
                job_id, review_decisions=decisions, actor="reviewer-b"
            )
            self.assertNotEqual(retry_id, job_id)
            retried = run_conversion_job(database, retry_id, runtime_root=root / "runtime")
            self.assertEqual(retried["status"], "READY_FOR_CONFIRMATION")
            retry_details = database.get_conversion_job(retry_id)
            self.assertEqual(retry_details["parent_job_id"], job_id)
            self.assertEqual(retry_details["retry_count"], 1)
            self.assertGreater(
                retry_details["conversion_report"]["summary"]["review_audit_count"], 0
            )

            queued_id, _ = submit_conversion(
                database,
                profile="generic.structured-mvp.v1",
                files=source_files(),
                project_name="等待服务恢复",
            )
            database.set_conversion_running(queued_id)
            recovered = database.requeue_interrupted_conversions()
            self.assertIn(queued_id, recovered)
            self.assertEqual(database.get_conversion_job(queued_id)["status"], "QUEUED")

    def test_zip_traversal_is_rejected_as_structured_failure(self) -> None:
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("../管段台账.csv", "管段编号,起点里程,终点里程\nS1,0,1\n")
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = QraDatabase(root / "qra.sqlite3")
            job_id, _ = submit_conversion(
                database,
                profile="generic.structured-mvp.v1",
                files=[{"file_name": "unsafe.zip", "content": archive_bytes.getvalue()}],
                project_name="恶意资料包测试",
            )
            failed = run_conversion_job(database, job_id, runtime_root=root / "runtime")
            self.assertEqual(failed["status"], "FAILED")
            self.assertEqual(failed["error"]["code"], "CONVERSION_EXECUTION_FAILED")
            self.assertIn("不安全路径", failed["error"]["message"])

    def test_http_upload_preview_confirm_and_existing_json_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = QraDatabase(Path(temporary_directory) / "qra.sqlite3")
            handler = type("Phase3Handler", (QraRequestHandler,), {"database": database})
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
                        headers={
                            "Content-Type": "application/json",
                            "X-QRA-Actor": "http-tester",
                        },
                    )
                    with urlopen(request, timeout=10) as response:
                        return response.status, json.loads(response.read().decode("utf-8"))

                _, profiles = request_json("/admin/api/conversion-profiles")
                self.assertTrue(
                    any(row["profile_id"] == "generic.structured-mvp.v1" for row in profiles)
                )
                upload = {
                    "profile": "generic.structured-mvp.v1",
                    "case_id": "PHASE3-HTTP",
                    "project_name": "网页转换测试",
                    "files": [
                        {
                            "file_name": source["file_name"],
                            "content_base64": base64.b64encode(source["content"]).decode("ascii"),
                        }
                        for source in source_files()
                    ],
                }
                status, submitted = request_json(
                    "/admin/api/conversions", method="POST", body=upload
                )
                self.assertEqual(status, 202)
                job_id = submitted["job"]["id"]
                final = submitted["job"]
                for _ in range(100):
                    _, final = request_json(f"/admin/api/conversions/{job_id}")
                    if final["status"] not in {"QUEUED", "RUNNING"}:
                        break
                    time.sleep(0.05)
                self.assertEqual(final["status"], "READY_FOR_CONFIRMATION")
                self.assertEqual(final["preview"]["status"], "READY_FOR_REVIEW")

                dedupe_status, dedupe = request_json(
                    "/admin/api/conversions", method="POST", body=upload
                )
                self.assertEqual(dedupe_status, 200)
                self.assertTrue(dedupe["deduplicated"])
                self.assertEqual(dedupe["job"]["id"], job_id)

                confirm_status, confirmed = request_json(
                    f"/admin/api/conversions/{job_id}/confirm",
                    method="POST",
                    body={
                        "name": "网页确认快照",
                        "reviewer": "http-reviewer",
                        "reason": "已完成网页预览核对",
                    },
                )
                self.assertEqual(confirm_status, 201)
                snapshot_id = confirmed["snapshot_id"]
                _, snapshot = request_json(f"/admin/api/snapshots/{snapshot_id}")
                self.assertEqual(snapshot["conversion"]["conversion_job_id"], job_id)

                # The legacy direct-JSON upload endpoint remains available.
                legacy_case = json.loads(json.dumps(final["payload"], ensure_ascii=False))
                legacy_case["metadata"]["case_id"] = "LEGACY-DIRECT-JSON"
                legacy_status, legacy = request_json(
                    "/admin/api/snapshots/import",
                    method="POST",
                    body={
                        "name": "原JSON上传兼容性",
                        "source_filename": "legacy.json",
                        "payload": legacy_case,
                    },
                )
                self.assertEqual(legacy_status, 201)
                self.assertTrue(legacy["created"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
