from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from qra_engine.adaptive_risk import calculate_adaptive_evidence_qra
from qra_engine.dynamic import (
    _write_dynamic_dashboard,
    plan_dynamic_flow,
    run_dynamic_flow,
)

from tests.unit.engine.helpers import load_case


PARTIAL_CASE = (
    Path(__file__).resolve().parents[3]
    / "workspace"
    / "inputs"
    / "部分输入_10类数据.json"
)
TEN_SEGMENT_CASE = (
    Path(__file__).resolve().parents[3]
    / "workspace"
    / "inputs"
    / "虚拟输入_11类数据_10管段.json"
)


def load_partial_case() -> dict:
    return json.loads(PARTIAL_CASE.read_text(encoding="utf-8"))


class DynamicPlanningTests(unittest.TestCase):
    def test_full_case_makes_every_registered_node_runnable(self) -> None:
        plan = plan_dynamic_flow(load_case())
        self.assertTrue(plan["plan"])
        self.assertTrue(all(row["status"] == "RUNNABLE" for row in plan["plan"]))
        self.assertIn("human_qra", plan["runnable_node_ids"])
        self.assertIn("risk_matrix", plan["runnable_node_ids"])

    def test_partial_case_keeps_independent_nodes_runnable(self) -> None:
        plan = plan_dynamic_flow(load_partial_case())
        status = {row["node_id"]: row for row in plan["plan"]}
        for node_id in (
            "data_inventory",
            "indicator_coverage",
            "segment_geometry",
            "failure_frequency",
            "leak_point_discretization",
            "aqt3046_source_term",
            "jet_fire_thresholds",
        ):
            self.assertEqual(status[node_id]["status"], "RUNNABLE")
        self.assertEqual(status["human_qra"]["status"], "SKIPPED")
        missing_paths = {row["path"] for row in status["human_qra"]["missing_inputs"]}
        self.assertTrue(
            {"weather_joint_probability", "population_cells", "ignition_model"}
            <= missing_paths
        )
        self.assertEqual(status["adaptive_evidence_qra"]["status"], "RUNNABLE")
        self.assertEqual(status["risk_matrix"]["status"], "RUNNABLE")

    def test_target_selection_includes_only_dependencies_and_inventory(self) -> None:
        plan = plan_dynamic_flow(load_partial_case(), targets=["failure_frequency"])
        self.assertEqual(
            [row["node_id"] for row in plan["plan"]],
            [
                "data_inventory",
                "indicator_coverage",
                "segment_geometry",
                "failure_frequency",
            ],
        )


class DynamicExecutionTests(unittest.TestCase):
    def test_numerical_hash_excludes_job_identity_and_time(self) -> None:
        case = load_partial_case()
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_manifest = run_dynamic_flow(
                case, Path(first), generate_charts=False, job_id="FILE-RUN-A"
            )
            second_manifest = run_dynamic_flow(
                case, Path(second), generate_charts=False, job_id="FILE-RUN-B"
            )
        self.assertEqual(
            first_manifest["numerical_result_sha256"],
            second_manifest["numerical_result_sha256"],
        )
        self.assertEqual(first_manifest["input_sha256"], second_manifest["input_sha256"])
        self.assertNotEqual(
            first_manifest["audit_manifest_sha256"],
            second_manifest["audit_manifest_sha256"],
        )

    def test_partial_case_completes_available_algorithms_without_global_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = run_dynamic_flow(
                load_partial_case(),
                Path(temporary),
                generate_charts=False,
                job_id="PARTIAL-TEST",
            )
            self.assertEqual(manifest["status"], "PARTIAL")
            records = {row["node_id"]: row for row in manifest["nodes"]}
            self.assertEqual(records["failure_frequency"]["status"], "COMPLETED")
            self.assertEqual(records["aqt3046_source_term"]["status"], "COMPLETED")
            self.assertEqual(records["human_qra"]["status"], "SKIPPED_MISSING_INPUT")
            self.assertEqual(records["adaptive_evidence_qra"]["status"], "COMPLETED")
            self.assertEqual(records["risk_matrix"]["status"], "COMPLETED")
            self.assertTrue((Path(temporary) / "nodes" / "failure_frequency.json").is_file())
            self.assertTrue((Path(temporary) / "report_dashboard.html").is_file())
            capability = json.loads(
                (Path(temporary) / "capability_report.json").read_text(encoding="utf-8")
            )
            self.assertIn("failure_frequency_json", capability["available_outputs"])
            self.assertIn("dynamic_dashboard_html", capability["available_outputs"])
            self.assertTrue(capability["missing_inputs"])
            usage = {
                row["data_group_id"]: row
                for row in capability["data_group_algorithm_usage"]
            }
            self.assertTrue(usage["external_corrosion"]["directly_consumed"])

    def test_eleven_categories_produce_ten_segment_quantitative_risk(self) -> None:
        case = json.loads(TEN_SEGMENT_CASE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            manifest = run_dynamic_flow(
                case,
                Path(temporary),
                generate_charts=False,
                job_id="TEN-SEGMENT-EVIDENCE-QRA",
            )
            records = {row["node_id"]: row for row in manifest["nodes"]}
            self.assertEqual(records["adaptive_evidence_qra"]["status"], "COMPLETED")
            self.assertEqual(records["risk_matrix"]["status"], "COMPLETED")
            result = json.loads(
                (Path(temporary) / "nodes" / "adaptive_evidence_qra.json").read_text(
                    encoding="utf-8"
                )
            )
            ranking = result["human_risk"]["segment_risk"]["ranking"]
            self.assertEqual(len(ranking), 10)
            self.assertGreater(
                result["human_risk"]["societal_risk"]["pipeline_pll_per_year"],
                0.0,
            )
            self.assertEqual(ranking[0]["segment_id"], "SEG-004")
            self.assertGreater(
                ranking[0]["risk_value_upper_screening_bound"],
                ranking[0]["risk_value_fatalities_per_year"],
            )
            matrix = json.loads(
                (Path(temporary) / "nodes" / "risk_matrix.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(matrix["source_node_id"], "adaptive_evidence_qra")
            self.assertEqual(len(matrix["segments"]), 10)
            dashboard = (Path(temporary) / "report_dashboard.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("本次评估结论", dashboard)
            self.assertIn("SEG-004 是当前最高风险管段", dashboard)
            self.assertIn("管段风险排序与驱动因素", dashboard)
            self.assertIn("计算与数据明细", dashboard)

    def test_partial_quantitative_dashboard_renders_full_risk_chart_set(self) -> None:
        case = json.loads(TEN_SEGMENT_CASE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dynamic_flow(case, root, generate_charts=True)
            chart_root = root / "charts" / "adaptive_evidence_qra"
            for filename in (
                "02_管段PLL排序.svg",
                "03_风险矩阵.svg",
                "04_沿线风险剖面.svg",
                "06_FN曲线.svg",
                "07_融合优先级.svg",
            ):
                self.assertTrue((chart_root / filename).is_file(), filename)
            dashboard = (root / "report_dashboard.html").read_text(encoding="utf-8")
            self.assertIn("核心图谱", dashboard)
            self.assertIn("风险诊断图", dashboard)
            self.assertIn("数据基础图", dashboard)

    def test_raw_eleven_categories_work_without_prebuilt_indicator_summary(self) -> None:
        case = json.loads(TEN_SEGMENT_CASE.read_text(encoding="utf-8"))
        case.pop("engineering_indicators")
        result = calculate_adaptive_evidence_qra(case)
        ranking = result["human_risk"]["segment_risk"]["ranking"]
        self.assertEqual(len(ranking), 10)
        self.assertEqual(ranking[0]["segment_id"], "SEG-004")
        terms = ranking[0]["evidence_diagnostics"]["terms"]
        observed = [row for row in terms if row["status"] == "OBSERVED"]
        self.assertEqual(len(observed), 8)
        self.assertTrue(
            all(row["value_source"] == "raw_data_categories" for row in observed)
        )

    def test_minimal_segment_uses_explicit_priors_instead_of_returning_no_risk(self) -> None:
        case = {
            "segments": [
                {
                    "segment_id": "MIN-001",
                    "start_km": 0.0,
                    "end_km": 1.0,
                    "length_km": 1.0,
                }
            ]
        }
        result = calculate_adaptive_evidence_qra(case)
        row = result["human_risk"]["segment_risk"]["ranking"][0]
        self.assertGreater(row["risk_value_fatalities_per_year"], 0.0)
        self.assertEqual(result["population_source"], "model_population_density_prior")
        self.assertGreater(row["uncertainty_factor"], 2.0)

    def test_sparse_input_still_returns_data_inventory_and_missing_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = run_dynamic_flow(
                {"metadata": {"case_id": "SPARSE"}},
                Path(temporary),
                generate_charts=False,
            )
            self.assertEqual(manifest["status"], "PARTIAL_DATA_ONLY")
            records = {row["node_id"]: row for row in manifest["nodes"]}
            self.assertEqual(records["data_inventory"]["status"], "COMPLETED")
            self.assertEqual(records["indicator_coverage"]["status"], "COMPLETED")
            self.assertEqual(records["segment_geometry"]["status"], "SKIPPED_MISSING_INPUT")
            dashboard = (Path(temporary) / "report_dashboard.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("动态QRA计算结果", dashboard)
            self.assertIn("PARTIAL_DATA_ONLY", dashboard)

    def test_runtime_failure_is_isolated_from_other_nodes(self) -> None:
        case = copy.deepcopy(load_partial_case())
        case["segments"][0]["wall_thickness_mm"] = 400.0
        with tempfile.TemporaryDirectory() as temporary:
            manifest = run_dynamic_flow(
                case,
                Path(temporary),
                generate_charts=False,
            )
            records = {row["node_id"]: row for row in manifest["nodes"]}
            self.assertEqual(records["failure_frequency"]["status"], "COMPLETED")
            self.assertEqual(records["aqt3046_source_term"]["status"], "FAILED_ISOLATED")
            self.assertEqual(
                records["jet_fire_thresholds"]["status"],
                "SKIPPED_DEPENDENCY_FAILED",
            )

    def test_full_qra_dashboard_uses_authoritative_scenario_and_null_semantics(self) -> None:
        case = {
            "metadata": {"case_id": "REPORT-FULL", "project_name": "完整报告测试"},
            "pipeline": {"pipeline_id": "P-1"},
            "segments": [
                {
                    "segment_id": "SEG-001",
                    "start_km": 0.0,
                    "end_km": 1.0,
                    "length_km": 1.0,
                }
            ],
            "population_cells": [],
        }
        top = {
            "segment_id": "SEG-001",
            "start_km": 0.0,
            "end_km": 1.0,
            "length_km": 1.0,
            "risk_value_rank": 1,
            "risk_value_fatalities_per_year": 1.2e-5,
            "fraction_of_pipeline_risk_value": 1.0,
            "dominant_risk_scenario": {
                "loc_id": "rupture",
                "branch_id": "jet_fire",
                "fatal_heat_flux_distance_m": 123.4,
            },
        }
        results = {
            "human_qra": {
                "run": {"formal_report_blockers": []},
                "human_risk": {
                    "segment_risk": {"ranking": [top]},
                    "societal_risk": {"pipeline_pll_per_year": 1.2e-5},
                    "individual_risk": {"maximum": {"value_per_year": 2.0e-6}},
                },
            },
            "risk_matrix": {
                "segments": [
                    {
                        "segment_id": "SEG-001",
                        "display_risk_band": "MEDIUM",
                        "display_risk_band_zh": "中",
                    }
                ]
            },
        }
        capability = {
            "status": "PASS",
            "completed_node_ids": ["human_qra", "risk_matrix"],
            "skipped_node_ids": [],
            "failed_node_ids": [],
            "missing_inputs": [],
            "data_group_algorithm_usage": [],
            "risk_result": {
                "available": True,
                "source_node_id": "human_qra",
                "result_tier": "FULL_SPATIAL_HUMAN_QRA",
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = _write_dynamic_dashboard(
                case,
                results,
                capability,
                [],
                Path(temporary) / "report.html",
                job_id="REPORT-TEST",
            )
            dashboard = path.read_text(encoding="utf-8")
        self.assertIn("完全破裂—喷射火", dashboard)
        self.assertIn("123.4 m", dashboard)
        self.assertIn("不适用/未计算", dashboard)
        self.assertNotIn("0.00e+00 – 0.00e+00", dashboard)
        self.assertNotIn("输入：0 类数据", dashboard)


if __name__ == "__main__":
    unittest.main()
