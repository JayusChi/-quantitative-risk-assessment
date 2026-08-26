from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[2]

from db_qra.admin_ui import admin_html
from db_qra.database import QraDatabase
from db_qra.engine_adapter import calculate_snapshot
from db_qra.server import QraRequestHandler
import db_qra.server as server_module


class DatabaseQraEndToEndTest(unittest.TestCase):
    def test_import_calculate_persist_and_serve(self) -> None:
        input_path = (
            PROJECT_ROOT
            / "workspace"
            / "inputs"
            / "虚拟输入_6类最小实用数据_10管段.json"
        )
        case = json.loads(input_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database = QraDatabase(root / "test.sqlite3")
            snapshot_id, created = database.import_case(
                case, name="端到端测试", source_path=str(input_path)
            )
            self.assertTrue(created)
            duplicate_id, duplicate_created = database.import_case(
                case, name="重复导入", source_path=str(input_path)
            )
            self.assertEqual(snapshot_id, duplicate_id)
            self.assertFalse(duplicate_created)
            self.assertEqual(
                database.snapshot_metadata(snapshot_id)["counts"]["segments"], 10
            )

            run = calculate_snapshot(
                database, snapshot_id, runtime_root=root / "runtime"
            )
            self.assertEqual(run["status"], "COMPLETED")
            self.assertTrue(run["summary"]["risk_result_available"])
            self.assertEqual(len(database.get_segment_results(run["id"])), 10)

            artifacts = database.list_artifacts(run["id"])
            artifact_paths = {row["path"] for row in artifacts}
            self.assertIn("report_dashboard.html", artifact_paths)
            self.assertIn("nodes/risk_matrix.json", artifact_paths)
            self.assertTrue(any(path.endswith(".svg") for path in artifact_paths))
            self.assertIsNotNone(
                database.get_artifact(run["id"], "report_dashboard.html")
            )

            handler = type(
                "TestHandler",
                (QraRequestHandler,),
                {"database": database},
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            server_module.RUNTIME_ROOT = root / "runtime"
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
                ) -> object:
                    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
                    request = Request(
                        base + path,
                        data=data,
                        method=method,
                        headers={"Content-Type": "application/json"},
                    )
                    with urlopen(request, timeout=10) as response:
                        return json.loads(response.read().decode("utf-8"))

                with urlopen(f"http://{host}:{port}/", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    admin = response.read().decode("utf-8")
                    self.assertIn("SIGA 风险智控", admin)
                    self.assertIn("上传JSON数据", admin)
                    self.assertIn("数据库只读视图", admin)
                overview = request_json("/admin/api/overview")
                self.assertEqual(overview["snapshot_count"], 1)
                self.assertEqual(overview["completed_run_count"], 1)
                preview = request_json(
                    "/admin/api/snapshots/preview",
                    method="POST",
                    body={"filename": "test.json", "payload": case},
                )
                self.assertEqual(preview["segment_count"], 10)
                self.assertEqual(preview["data_category_count"], 6)
                self.assertGreaterEqual(preview["runnable_node_count"], 1)

                invalid_payloads = [
                    {},
                    {"metadata": {"case_id": "EMPTY"}, "pipeline": {}, "segments": []},
                    {"unrelated": "value"},
                ]
                reversed_chainage = json.loads(json.dumps(case, ensure_ascii=False))
                reversed_chainage["segments"][0]["start_km"] = 2.0
                invalid_payloads.append(reversed_chainage)
                duplicate_segment = json.loads(json.dumps(case, ensure_ascii=False))
                duplicate_segment["segments"][1]["segment_id"] = duplicate_segment["segments"][0]["segment_id"]
                invalid_payloads.append(duplicate_segment)
                wrong_unit = json.loads(json.dumps(case, ensure_ascii=False))
                wrong_unit["frequency_library"] = {"unit": "per_mile_year"}
                invalid_payloads.append(wrong_unit)
                for invalid in invalid_payloads:
                    request = Request(
                        base + "/admin/api/snapshots/preview",
                        data=json.dumps(
                            {"filename": "invalid.json", "payload": invalid},
                            ensure_ascii=False,
                        ).encode("utf-8"),
                        method="POST",
                        headers={"Content-Type": "application/json"},
                    )
                    with self.assertRaises(HTTPError) as captured:
                        urlopen(request, timeout=10)
                    self.assertEqual(captured.exception.code, 400)
                    error = json.loads(captured.exception.read().decode("utf-8"))
                    self.assertEqual(error["error"], "INPUT_VALIDATION_FAILED")
                    self.assertTrue(error["issues"])
                invalid_import_request = Request(
                    base + "/admin/api/snapshots/import",
                    data=json.dumps(
                        {
                            "name": "不得写入",
                            "source_filename": "invalid.json",
                            "payload": {},
                        },
                        ensure_ascii=False,
                    ).encode("utf-8"),
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with self.assertRaises(HTTPError) as captured:
                    urlopen(invalid_import_request, timeout=10)
                self.assertEqual(captured.exception.code, 400)
                self.assertEqual(
                    request_json("/admin/api/overview")["snapshot_count"], 1
                )
                with urlopen(
                    f"http://{host}:{port}/runs/{run['id']}/", timeout=5
                ) as response:
                    dashboard = response.read().decode("utf-8")
                    self.assertEqual(response.status, 200)
                    self.assertIn("动态QRA计算结果", dashboard)
                with urlopen(
                    f"http://{host}:{port}/api/runs/{run['id']}/segments", timeout=5
                ) as response:
                    segments = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(len(segments), 10)
                    self.assertEqual(segments[0]["risk_rank"], 1)
                chart_path = next(
                    path for path in artifact_paths if path.endswith(".svg")
                )
                chart_url = quote(chart_path, safe="/")
                with urlopen(
                    f"http://{host}:{port}/runs/{run['id']}/{chart_url}", timeout=5
                ) as response:
                    self.assertEqual(response.status, 200)
                    self.assertTrue(
                        response.headers["Content-Type"].startswith("image/svg+xml")
                    )
                table = request_json("/admin/api/database/calculation_segment_result")
                self.assertEqual(len(table["rows"]), 10)
                self.assertNotIn("payload_json", table["columns"])
                with urlopen(
                    f"{base}/admin/api/runs/{run['id']}/export", timeout=10
                ) as response:
                    self.assertTrue(response.read(4).startswith(b"PK"))

                queued = request_json(
                    "/admin/api/runs",
                    method="POST",
                    body={"snapshot_id": snapshot_id, "generate_charts": True},
                )["run"]
                async_run_id = queued["id"]
                final_status = queued["status"]
                for _ in range(50):
                    details = request_json(f"/admin/api/runs/{async_run_id}")
                    final_status = details["run"]["status"]
                    if final_status in {"COMPLETED", "FAILED"}:
                        break
                    time.sleep(0.1)
                self.assertEqual(final_status, "COMPLETED")
                self.assertEqual(
                    len(request_json(f"/admin/api/runs/{async_run_id}/segments")),
                    10,
                )
                second_run = request_json(f"/admin/api/runs/{async_run_id}")["run"]
                self.assertNotEqual(run["id"], second_run["id"])
                self.assertEqual(run["result_sha256"], second_run["result_sha256"])
                stored_manifest = json.loads(
                    database.get_artifact(run["id"], "dynamic_manifest.json")[1].decode(
                        "utf-8"
                    )
                )
                self.assertEqual(
                    run["result_sha256"], stored_manifest["numerical_result_sha256"]
                )
                self.assertNotEqual(
                    stored_manifest["numerical_result_sha256"],
                    stored_manifest["audit_manifest_sha256"],
                )

                unused_case = json.loads(json.dumps(case, ensure_ascii=False))
                unused_case.setdefault("metadata", {})["case_id"] = "UNUSED-SNAPSHOT"
                imported = request_json(
                    "/admin/api/snapshots/import",
                    method="POST",
                    body={
                        "name": "未使用快照",
                        "source_filename": "unused.json",
                        "payload": unused_case,
                    },
                )
                deleted = request_json(
                    f"/admin/api/snapshots/{imported['snapshot_id']}",
                    method="DELETE",
                )
                self.assertEqual(deleted["status"], "DELETED")
                audits = request_json("/admin/api/audit?limit=100")
                event_types = {row["event_type"] for row in audits}
                self.assertIn("RUN_COMPLETED", event_types)
                self.assertIn("RESULT_EXPORTED", event_types)
                self.assertIn("SNAPSHOT_DELETED", event_types)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_upload_failure_path_clears_stale_state(self) -> None:
        html = admin_html().decode("utf-8")
        self.assertIn("function clearUploadPayload()", html)
        self.assertIn(
            "catch(e){clearUploadPayload();$('#fileStatus').textContent='预检失败'",
            html,
        )
        self.assertIn("$('#previewHash').value=''", html)


if __name__ == "__main__":
    unittest.main()
