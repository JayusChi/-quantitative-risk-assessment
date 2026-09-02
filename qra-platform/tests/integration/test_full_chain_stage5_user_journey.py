from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from db_qra.database import QraDatabase
from db_qra.engine_adapter import execute_run
from db_qra.project_service import ProjectService
from db_qra.project_ui import project_workspace_html
from db_qra.review_ui import review_workbench_html
from db_qra.server import QraRequestHandler


class Stage5UserJourneyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = QraDatabase(self.root / "stage5.sqlite3")
        self.database.initialize()
        self.service = ProjectService(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_project_lifecycle_and_complete_demo_are_aggregated(self) -> None:
        ordinary = self.service.create(name="普通项目", case_id="S5-001")
        self.assertEqual(ordinary["status"], "NEEDS_UPLOAD")
        self.assertEqual(ordinary["next_action"]["id"], "UPLOAD_FILES")
        self.assertEqual(len(ordinary["journey_steps"]), 6)
        self.database.archive_project(str(ordinary["id"]), archived=True)
        self.assertTrue(self.database.get_project(str(ordinary["id"]))["archived"])
        self.database.archive_project(str(ordinary["id"]), archived=False)

        loaded = self.service.create_demo(actor="test-user")
        self.assertTrue(loaded["created"])
        demo = loaded["project"]
        self.assertTrue(demo["is_demo"])
        self.assertEqual(len(demo["sources"]), 10)
        self.assertEqual(len(demo["calculation_versions"]["parameter_pack_ids"]), 6)
        execute_run(
            self.database,
            str(loaded["run_id"]),
            str(demo["advanced_audit"]["snapshot_id"]),
            generate_charts=False,
            runtime_root=self.root / "runtime",
        )
        demo = self.service.get(str(demo["id"]))
        self.assertEqual(demo["status"], "REPORT_READY")
        self.assertEqual(demo["calculation_progress"]["completed"], 11)
        self.assertEqual(len(demo["nodes"]), 11)
        self.assertEqual(demo["next_action"]["id"], "OPEN_REPORT")
        self.assertTrue(demo["report_center"]["draft"])
        self.assertEqual(demo["report_center"]["completeness"], "PASS")
        self.assertEqual(demo["report_center"]["numerical_consistency"], "PASS")
        self.assertEqual(demo["report_center"]["citations"], "BOUND")
        self.assertFalse(
            demo["calculation"]["summary"]["formal_acceptance_judgement_allowed"]
        )
        duplicate = self.service.create_demo(actor="test-user")
        self.assertFalse(duplicate["created"])
        self.assertEqual(duplicate["project"]["id"], demo["id"])

    def test_failed_calculation_has_safe_retry_without_losing_snapshot(self) -> None:
        loaded = self.service.create_demo()
        project = loaded["project"]
        snapshot_id = str(project["advanced_audit"]["snapshot_id"])
        run_id = str(loaded["run_id"])
        self.database.fail_run(run_id, "controlled failure")
        failed = self.service.get(str(project["id"]))
        self.assertEqual(failed["status"], "CALCULATION_FAILED")
        self.assertEqual(failed["next_action"]["id"], "RETRY_CALCULATION")
        self.assertEqual(failed["advanced_audit"]["snapshot_id"], snapshot_id)

    def test_ordinary_and_review_pages_expose_complete_guided_ui(self) -> None:
        page = project_workspace_html().decode("utf-8")
        for label in ("上传资料", "自动整理", "数据复核", "确认数据", "风险计算", "报告中心"):
            self.assertIn(label, page)
        self.assertIn('type="file"', page)
        self.assertNotIn("上传JSON数据", page)
        self.assertNotIn("数据库视图", page)
        self.assertIn("高级审计与技术追溯", page)
        self.assertIn("@media(max-width:900px)", page)
        self.assertIn("@media(max-width:560px)", page)
        self.assertIn("focus-visible", page)
        self.assertIn('role="alert"', page)

        review_page = review_workbench_html("CONV-TEST").decode("utf-8")
        self.assertIn("criticalityFilter", review_page)
        self.assertIn("计算节点筛选", review_page)
        self.assertIn("数据层：项目事实", review_page)
        self.assertIn("参数包由系统按版本受控绑定", review_page)
        self.assertIn("projectBack", review_page)

    def test_http_root_projects_and_project_api_work_without_cli(self) -> None:
        handler = type("Stage5Handler", (QraRequestHandler,), {"database": self.database})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            base = f"http://{host}:{port}"
            with urlopen(base + "/", timeout=20) as response:
                self.assertEqual(response.geturl(), base + "/projects/")
                page = response.read().decode("utf-8")
            self.assertIn("QRA 项目工作台", page)

            body = json.dumps(
                {"name": "HTTP普通项目", "case_id": "S5-HTTP", "actor": "test-user"}
            ).encode("utf-8")
            request = Request(
                base + "/admin/api/projects",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=20) as response:
                self.assertEqual(response.status, 201)
                project = json.loads(response.read().decode("utf-8"))
            self.assertEqual(project["status"], "NEEDS_UPLOAD")

            with urlopen(
                base + f"/projects/{project['id']}/", timeout=20
            ) as response:
                detail_page = response.read().decode("utf-8")
            self.assertIn(str(project["id"]), detail_page)
            with urlopen(base + "/admin/api/projects", timeout=20) as response:
                projects = json.loads(response.read().decode("utf-8"))
            self.assertEqual([row["id"] for row in projects], [project["id"]])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
