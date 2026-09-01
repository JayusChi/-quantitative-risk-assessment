from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from db_qra.conversion_adapter import run_conversion_job, submit_conversion
from db_qra.database import QraDatabase
from db_qra.engine_adapter import execute_run
from db_qra.review_service import ReviewService
from db_qra.roadmap_stage4 import build_m1_summary
from tools.run_roadmap_stage4_acceptance import _redaction_guard

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "converter_jiujiang_stage1" / "脱敏高后果区.csv"
TARGET_NODES = [
    "data_inventory",
    "indicator_coverage",
    "segment_geometry",
    "adaptive_evidence_qra",
    "risk_matrix",
]


class RoadmapStage4E2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = QraDatabase(self.root / "qra.sqlite3")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _convert(self, suffix: str = "A") -> str:
        job_id, created = submit_conversion(
            self.database,
            profile="jiangxi-natural-gas.jiujiang.v1",
            files=[
                {
                    "file_name": f"{suffix}-{RAW_FIXTURE.name}",
                    "content": RAW_FIXTURE.read_bytes(),
                }
            ],
            case_id=f"ROADMAP-STAGE4-{suffix}",
            project_name="路线图第四阶段脱敏工程闭环",
            actor="stage4-engineering",
        )
        self.assertTrue(created)
        job = run_conversion_job(
            self.database,
            job_id,
            runtime_root=self.root / f"conversion-{suffix}",
        )
        self.assertIn(job["status"], {"BLOCKED", "READY_FOR_CONFIRMATION"})
        self.assertIsNone(job["review_decisions"])
        return job_id

    def _review_confirm_calculate(self, job_id: str) -> dict[str, object]:
        service = ReviewService(self.database)
        session, created = service.create_or_resume_session(
            job_id,
            actor="stage4-engineering-reviewer",
        )
        self.assertTrue(created)
        session_id = str(session["id"])
        self.assertEqual(service.get_session(session_id)["target_node_ids"], TARGET_NODES)
        items = service.list_items(session_id, limit=500)["items"]
        self.assertTrue(items)
        for item in items:
            detail = service.get_item(session_id, str(item["review_item_key"]))
            candidate = detail["candidates"][0]
            evidence = candidate["evidence"][0]
            service.save_decision(
                session_id,
                review_item_key=str(detail["review_item_key"]),
                action="ACCEPT_CANDIDATE",
                selected_candidate_id=str(candidate["candidate_id"]),
                override_value=None,
                override_unit=None,
                reason="内部工程回归逐项核对候选与原始表格证据",
                actor="stage4-engineering-reviewer",
                expected_revision=int(service.get_session(session_id)["revision"]),
                source_id=str(evidence["location"]["file_id"]),
                evidence_id=str(evidence["evidence_id"]),
            )
        gate = service.run_gate(session_id, actor="stage4-engineering-reviewer")
        self.assertEqual(gate["status"], "PASS")
        current = service.get_session(session_id)
        confirmed = service.confirm(
            session_id,
            snapshot_name="路线图第四阶段脱敏工程快照",
            reviewer="stage4-engineering-reviewer",
            reason="内部功能验收，非正式业务签批",
            expected_revision=int(current["revision"]),
            expected_candidate_set_hash=str(current["candidate_set_hash"]),
            expected_decision_set_hash=str(current["decision_set_hash"]),
            run_after_confirm=True,
            generate_charts=True,
        )
        self.assertIsNotNone(confirmed["run_id"])
        execute_run(
            self.database,
            str(confirmed["run_id"]),
            str(confirmed["snapshot_id"]),
            targets=confirmed["targets"],
            generate_charts=True,
            runtime_root=self.root / "calculation",
        )
        return {"session_id": session_id, **confirmed}

    def test_raw_file_review_snapshot_calculation_report_and_reverse_trace(self) -> None:
        job_id = self._convert()
        with self.database.session() as connection:
            counts = {
                table: int(
                    connection.execute(
                        f"SELECT count(*) FROM {table} WHERE job_id = ?", (job_id,)
                    ).fetchone()[0]
                )
                for table in (
                    "conversion_parse_artifact",
                    "extracted_entity",
                    "candidate_field",
                    "candidate_evidence_link",
                )
            }
        self.assertTrue(all(value > 0 for value in counts.values()), counts)

        result = self._review_confirm_calculate(job_id)
        summary = build_m1_summary(
            self.database,
            job_id,
            review_session_id=str(result["session_id"]),
            calculation_run_id=str(result["run_id"]),
        )
        self.assertEqual(summary["status"], "PASS", summary["error_codes"])
        self.assertEqual(summary["conversion_status"], "CONFIRMED")
        self.assertEqual(summary["completed_target_nodes"], TARGET_NODES)
        self.assertGreater(summary["segment_result_count"], 0)
        self.assertGreater(summary["report_artifact_count"], 0)
        self.assertEqual(summary["report_entry_path"], "report_dashboard.html")
        self.assertEqual(summary["reverse_trace_status"], "PASS")
        stored = self.database.get_artifact(str(result["run_id"]), "report_dashboard.html")
        self.assertIsNotNone(stored)
        report_html = stored[1].decode("utf-8")
        self.assertIn("现有证据定量筛查", report_html)
        self.assertIn("未记录 / 未记录", report_html)
        self.assertNotIn("继续升级完整QRA所需数据 <span>0 项", report_html)
        self.assertIn("尚不能直接作为接受性结论", report_html)

        repeated = ReviewService(self.database).confirm(
            str(result["session_id"]),
            snapshot_name="重复点击不应产生新事实",
            reviewer="stage4-engineering-reviewer",
            reason="验证确认接口幂等",
            expected_revision=999,
            expected_candidate_set_hash="ignored-after-confirmation",
            expected_decision_set_hash="ignored-after-confirmation",
            run_after_confirm=True,
            generate_charts=True,
        )
        self.assertEqual(repeated["snapshot_id"], result["snapshot_id"])
        self.assertEqual(repeated["run_id"], result["run_id"])

    def test_run_rejects_cross_snapshot_and_report_path_traversal(self) -> None:
        job_id = self._convert("PATH")
        result = self._review_confirm_calculate(job_id)
        with self.assertRaisesRegex(ValueError, "不匹配"):
            execute_run(
                self.database,
                str(result["run_id"]),
                "SNAPSHOT-FROM-OTHER-TASK",
                runtime_root=self.root / "cross-task",
            )
        for unsafe in ("../secret", "/absolute/report.html", "C:/secret.txt", "..\\secret"):
            with self.subTest(path=unsafe), self.assertRaises(ValueError):
                self.database.get_artifact(str(result["run_id"]), unsafe)

    def test_m1_summary_does_not_accept_unconfirmed_conversion(self) -> None:
        job_id = self._convert("UNCONFIRMED")
        summary = build_m1_summary(self.database, job_id)
        self.assertEqual(summary["status"], "FAILED")
        self.assertIn("M1.NO_REVIEW_SESSION", summary["error_codes"])
        self.assertIn("M1.SNAPSHOT_NOT_FROM_CONVERSION", summary["error_codes"])

    def test_summary_is_foreign_key_scoped_and_rejects_cross_chain_ids(self) -> None:
        first_job_id = self._convert("SCOPE-A")
        first = self._review_confirm_calculate(first_job_id)
        baseline = build_m1_summary(
            self.database,
            first_job_id,
            review_session_id=str(first["session_id"]),
            calculation_run_id=str(first["run_id"]),
        )

        second_job_id = self._convert("SCOPE-B")
        second_session, _ = ReviewService(self.database).create_or_resume_session(
            second_job_id,
            actor="stage4-scope-reviewer",
        )
        isolated = build_m1_summary(
            self.database,
            first_job_id,
            review_session_id=str(first["session_id"]),
            calculation_run_id=str(first["run_id"]),
        )
        for key in (
            "source_count",
            "parse_artifact_count",
            "extracted_entity_count",
            "candidate_field_count",
            "candidate_evidence_link_count",
        ):
            self.assertEqual(isolated[key], baseline[key], key)
        with self.assertRaisesRegex(ValueError, "不属于当前转换任务"):
            build_m1_summary(
                self.database,
                first_job_id,
                review_session_id=str(second_session["id"]),
                calculation_run_id=str(first["run_id"]),
            )

    def test_snapshot_reuse_keeps_current_review_provenance_and_run_hash(self) -> None:
        job_id = self._convert("REUSE")
        first = self._review_confirm_calculate(job_id)
        second = self._review_confirm_calculate(job_id)
        self.assertEqual(second["snapshot_id"], first["snapshot_id"])
        self.assertNotEqual(second["session_id"], first["session_id"])
        self.assertNotEqual(second["run_id"], first["run_id"])

        run = self.database.get_run(str(second["run_id"]))
        snapshot = self.database.snapshot_metadata(str(second["snapshot_id"]))
        self.assertEqual(run["snapshot_id"], second["snapshot_id"])
        self.assertEqual(run["input_sha256"], snapshot["payload_sha256"])
        summary = build_m1_summary(
            self.database,
            job_id,
            review_session_id=str(second["session_id"]),
            calculation_run_id=str(second["run_id"]),
        )
        self.assertEqual(summary["status"], "PASS", summary["error_codes"])
        self.assertEqual(summary["review_provenance_count"], 1)
        self.assertEqual(summary["reverse_trace_status"], "PASS")

    def test_versioned_targets_reject_unknown_and_scope_drift(self) -> None:
        job_id = self._convert("TARGETS")
        service = ReviewService(self.database)
        with self.assertRaisesRegex(ValueError, "未知"):
            service.create_or_resume_session(
                job_id,
                actor="stage4-target-reviewer",
                target_node_ids=["unknown_target_node"],
            )
        with self.assertRaisesRegex(ValueError, "版本化试点清单"):
            service.create_or_resume_session(
                job_id,
                actor="stage4-target-reviewer",
                target_node_ids=["segment_geometry"],
            )

    def test_review_provenance_target_status_and_report_integrity_are_required(self) -> None:
        job_id = self._convert("INTEGRITY")
        result = self._review_confirm_calculate(job_id)
        session_id = str(result["session_id"])
        run_id = str(result["run_id"])

        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE calculation_run SET review_provenance_id = NULL WHERE id = ?",
                (run_id,),
            )
            connection.execute(
                "DELETE FROM input_snapshot_review_provenance WHERE review_session_id = ?",
                (session_id,),
            )
        no_review_trace = build_m1_summary(
            self.database,
            job_id,
            review_session_id=session_id,
            calculation_run_id=run_id,
        )
        self.assertIn("M1.NO_REVIEW_PROVENANCE", no_review_trace["error_codes"])
        self.assertIn("M1.REVERSE_TRACE_FAILED", no_review_trace["error_codes"])

        second = self._review_confirm_calculate(job_id)
        second_session_id = str(second["session_id"])
        second_run_id = str(second["run_id"])
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO calculation_node(
                    run_id, node_id, sequence_no, label_zh, standard_ref, status,
                    missing_inputs_json, blocked_dependencies_json, result_path, error_message
                ) VALUES (?, 'locator_matrix', 999, '非目标节点', NULL, 'SKIPPED',
                          '[\"population\"]', '[]', NULL, NULL)
                """,
                (second_run_id,),
            )
        non_target_skip = build_m1_summary(
            self.database,
            job_id,
            review_session_id=second_session_id,
            calculation_run_id=second_run_id,
        )
        self.assertEqual(non_target_skip["status"], "PASS", non_target_skip["error_codes"])

        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE calculation_node SET status = 'SKIPPED', "
                "missing_inputs_json = '[\"required_input\"]' "
                "WHERE run_id = ? AND node_id = 'risk_matrix'",
                (second_run_id,),
            )
        target_skip = build_m1_summary(
            self.database,
            job_id,
            review_session_id=second_session_id,
            calculation_run_id=second_run_id,
        )
        self.assertIn("M1.TARGET_NODE_INCOMPLETE", target_skip["error_codes"])

        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE calculation_node SET status = 'COMPLETED', missing_inputs_json = '[]' "
                "WHERE run_id = ? AND node_id = 'risk_matrix'",
                (second_run_id,),
            )
            connection.execute(
                "UPDATE calculation_artifact SET sha256 = ? "
                "WHERE run_id = ? AND path = 'report_dashboard.html'",
                ("0" * 64, second_run_id),
            )
        bad_report = build_m1_summary(
            self.database,
            job_id,
            review_session_id=second_session_id,
            calculation_run_id=second_run_id,
        )
        self.assertIn("M1.REPORT_INTEGRITY", bad_report["error_codes"])

    def test_interrupted_calculation_is_requeued_after_restart(self) -> None:
        job_id = self._convert("RECOVERY")
        result = self._review_confirm_calculate(job_id)
        run = self.database.get_run(str(result["run_id"]))
        recovery_run_id = self.database.create_run(
            str(result["snapshot_id"]),
            str(run["input_sha256"]),
            targets=TARGET_NODES,
            generate_charts=True,
        )
        self.database.set_run_running(recovery_run_id, "interrupted-test-engine")
        recovered = self.database.requeue_interrupted_runs()
        self.assertIn(recovery_run_id, recovered)
        recovered_run = self.database.get_run(recovery_run_id)
        self.assertEqual(recovered_run["status"], "QUEUED")
        self.assertIsNone(recovered_run["engine_version"])
        self.assertIsNone(recovered_run["started_at"])

    def test_acceptance_record_redaction_guard_rejects_sensitive_values(self) -> None:
        _redaction_guard(
            {
                "source_manifest_sha256": "a" * 64,
                "authorization_scope": "APPROVED_INTERNAL_PILOT_NO_EXTERNAL_SHARING",
                "final_status": "ENGINEERING_PASS",
            }
        )
        unsafe_values = {
            "absolute_path": r"D:\controlled\source.pdf",
            "authorization": "Authorization: secret",
            "bearer": "Bearer abc.def.ghi",
            "secret": "api_key=secret-value",
            "large_base64": "A" * 600,
        }
        for label, unsafe in unsafe_values.items():
            with self.subTest(label=label), self.assertRaisesRegex(
                ValueError, "M1.ACCEPTANCE_RECORD_NOT_REDACTED"
            ):
                _redaction_guard({"value": unsafe})


if __name__ == "__main__":
    unittest.main()
