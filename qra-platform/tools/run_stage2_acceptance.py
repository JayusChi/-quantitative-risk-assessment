from __future__ import annotations

import csv
import hashlib
import json
import shutil
import threading
import time
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]

from db_qra.database import QraDatabase, json_sha256
from db_qra.server import QraRequestHandler
import db_qra.server as server_module
from qra_engine import ENGINE_VERSION
from qra_engine.dynamic import DYNAMIC_SCHEMA_VERSION


EXPECTED_COMPLETED_NODES = {
    "data_inventory",
    "indicator_coverage",
    "segment_geometry",
    "adaptive_evidence_qra",
    "risk_matrix",
}
EXPECTED_SKIPPED_NODES = {
    "failure_frequency",
    "leak_point_discretization",
    "aqt3046_source_term",
    "jet_fire_thresholds",
    "gbt34346_annex_c",
    "human_qra",
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def _request(
    base: str,
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> tuple[int, str, bytes]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = Request(
        base + path,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-QRA-Actor": "stage2-internal-validator",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()
    except HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read()


def _request_json(
    base: str,
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    status, _, content = _request(base, path, method=method, body=body)
    return status, json.loads(content.decode("utf-8")) if content else None


def _submit_and_wait(base: str, snapshot_id: str) -> dict[str, Any]:
    status, submitted = _request_json(
        base,
        "/admin/api/runs",
        method="POST",
        body={"snapshot_id": snapshot_id, "generate_charts": True},
    )
    if status != 202:
        raise AssertionError(f"计算任务提交失败：HTTP {status} {submitted}")
    run_id = str(submitted["run"]["id"])
    for _ in range(600):
        status, details = _request_json(base, f"/admin/api/runs/{run_id}")
        if status != 200 or not isinstance(details, dict):
            raise AssertionError(f"计算任务查询失败：HTTP {status} {details}")
        run_status = details["run"]["status"]
        if run_status == "COMPLETED":
            return details
        if run_status == "FAILED":
            raise AssertionError(f"计算任务失败：{details['run'].get('error_message')}")
        time.sleep(0.05)
    raise TimeoutError(f"计算任务未在期限内完成：{run_id}")


def _extract_artifacts(database: QraDatabase, run_id: str, output_dir: Path) -> None:
    for record in database.list_artifacts(run_id):
        relative = Path(str(record["path"]))
        stored = database.get_artifact(run_id, relative.as_posix())
        if stored is None:
            raise AssertionError(f"数据库登记的结果资源不存在：{relative.as_posix()}")
        target = output_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(stored[1])


def _supplement_priority_list() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "ordering_basis": "先解除人员风险解释阻断，再解除频率/源项节点阻断，最后进入模型与准则审批。",
        "items": [
            {
                "rank": 1,
                "item_id": "GAP-M2-001",
                "gap_type": "DATA_MISSING",
                "priority": "P0",
                "data": "受体坐标、昼夜人口、室内外比例及人口调查基准日",
                "impact": "IR与项目F-N不可计算；当前PLL只能使用模型人口密度先验作筛查。",
                "blocks": ["human_qra", "IR", "project_FN", "formal_PLL_interpretation"],
                "required_evidence": "权威人口/高后果目标台账或现场调查，含坐标、时段、室内外比例、来源和日期。",
            },
            {
                "rank": 2,
                "item_id": "GAP-M2-002",
                "gap_type": "DATA_MISSING",
                "priority": "P0",
                "data": "权威内部桩号、GIS中心线及阀门里程",
                "impact": "当前只有1个全线管段，无法比较真实高风险区段；泄漏点离散和隔离边界不可运行。",
                "blocks": ["meaningful_segment_ranking", "leak_point_discretization"],
                "required_evidence": "测绘/GIS导出、桩号表和上下游阀门位置，并与JJ001至JJ041G边界核对。",
            },
            {
                "rank": 3,
                "item_id": "GAP-M2-003",
                "gap_type": "PARAMETER_NOT_APPROVED",
                "priority": "P0",
                "data": "适用失效频率库、分机理孔径比例及项目K_j",
                "impact": "分机理失效频率和完整人员QRA不可运行；当前频率仅为未校准筛查先验。",
                "blocks": ["failure_frequency", "human_qra", "formal_frequency_interpretation"],
                "required_evidence": "版本化参数库、适用性说明、校准/引用依据和模型负责人批准。",
            },
            {
                "rank": 4,
                "item_id": "GAP-M2-004",
                "gap_type": "DATA_MISSING_AND_PARAMETER_NOT_APPROVED",
                "priority": "P0",
                "data": "批准的运行压力取值/分布、运行温度、逐段壁厚",
                "impact": "AQ/T 3046源项和GB/T 34346附录C节点不可运行；筛查使用4.0 MPa、288.15 K、10 mm模型默认值。",
                "blocks": ["aqt3046_source_term", "gbt34346_annex_c", "human_qra"],
                "required_evidence": "SCADA或运行统计、设计/竣工/测厚资料及压力范围3.0–3.8 MPa的模型取值决定。",
            },
            {
                "rank": 5,
                "item_id": "GAP-M2-005",
                "gap_type": "PARAMETER_NOT_APPROVED",
                "priority": "P0",
                "data": "气体组分/物性、环境绝压、泄放系数、粗糙度、燃烧热和辐射分数",
                "impact": "独立源项和喷射火节点不可运行；筛查后果依赖模型默认物性。",
                "blocks": ["aqt3046_source_term", "jet_fire_thresholds", "human_qra"],
                "required_evidence": "气质报告、项目环境条件和经批准的版本化后果参数库。",
            },
            {
                "rank": 6,
                "item_id": "GAP-M2-006",
                "gap_type": "DATA_MISSING_AND_PARAMETER_NOT_APPROVED",
                "priority": "P0",
                "data": "气象联合概率、点火模型、点火源与拥塞/约束资料",
                "impact": "完整人员QRA事件树、扩散、IR和项目F-N不可运行。",
                "blocks": ["human_qra"],
                "required_evidence": "代表性气象统计、点火源调查、点火概率批准及拥塞/爆炸条件审查。",
            },
            {
                "rank": 7,
                "item_id": "GAP-M2-007",
                "gap_type": "MODEL_NOT_VALIDATED_AND_GOVERNANCE_DEFERRED",
                "priority": "P1",
                "data": "证据更新模型校准、物理模型外部验证、项目准则和业务现场复核",
                "impact": "即使补齐数据也不能直接签发正式报告或作风险接受性判断。",
                "blocks": ["formal_acceptance", "M2_business_acceptance", "G3", "G4"],
                "required_evidence": "独立基准、收敛性/敏感性、批准准则、实名复核人与书面业务解释。",
            },
        ],
    }


def run(stage1_root: Path, output_root: Path) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"阶段2输出目录已存在，拒绝混入旧证据：{output_root}")
    output_root.mkdir(parents=True)

    stage1_summary_path = stage1_root / "acceptance-summary.json"
    stage1_database_path = stage1_root / "stage1-web.sqlite3"
    stage1_case_path = stage1_root / "golden-case.candidate.json"
    for required in (stage1_summary_path, stage1_database_path, stage1_case_path):
        if not required.is_file():
            raise FileNotFoundError(f"阶段1证据缺失：{required}")

    stage1_summary = _read_json(stage1_summary_path)
    if stage1_summary.get("technical_status") != "PASSED_INTERNAL_FUNCTION_VALIDATION":
        raise AssertionError("阶段1内部功能验证状态不允许进入阶段2试算")
    snapshot_id = str(stage1_summary["web"]["snapshot_id"])
    expected_input_hash = str(stage1_summary["cli"]["case_sha256"])
    case = _read_json(stage1_case_path)
    if json_sha256(case) != expected_input_hash:
        raise AssertionError("阶段1黄金候选与验收哈希不一致")

    stage2_database_path = output_root / "stage2-web.sqlite3"
    shutil.copy2(stage1_database_path, stage2_database_path)
    database = QraDatabase(stage2_database_path)
    snapshot = database.snapshot_document(snapshot_id)
    if snapshot.get("payload_sha256") != expected_input_hash:
        raise AssertionError("阶段1不可变快照哈希与黄金候选不一致")

    handler = type("Stage2AcceptanceHandler", (QraRequestHandler,), {"database": database})
    server_module.RUNTIME_ROOT = output_root / "api-runtime"
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        base = f"http://{host}:{port}"
        first = _submit_and_wait(base, snapshot_id)
        second = _submit_and_wait(base, snapshot_id)
        first_run = first["run"]
        second_run = second["run"]
        if first_run["result_sha256"] != second_run["result_sha256"]:
            raise AssertionError("相同快照和模型版本的两次计算数值哈希不一致")
        if first_run["summary"]["dynamic_status"] != "PARTIAL":
            raise AssertionError("真实试算未按预期形成PARTIAL筛查结果")

        first_nodes = {row["node_id"]: row for row in first["nodes"]}
        completed_nodes = {
            node_id for node_id, row in first_nodes.items() if row["status"] == "COMPLETED"
        }
        skipped_nodes = {
            node_id
            for node_id, row in first_nodes.items()
            if row["status"].startswith("SKIPPED")
        }
        failed_nodes = {
            node_id
            for node_id, row in first_nodes.items()
            if row["status"] == "FAILED_ISOLATED"
        }
        if completed_nodes != EXPECTED_COMPLETED_NODES:
            raise AssertionError(f"实际完成节点不符合预期：{sorted(completed_nodes)}")
        if skipped_nodes != EXPECTED_SKIPPED_NODES:
            raise AssertionError(f"实际跳过节点不符合预期：{sorted(skipped_nodes)}")
        if failed_nodes:
            raise AssertionError(f"真实试算存在隔离失败节点：{sorted(failed_nodes)}")

        adaptive = database.get_result_document(first_run["id"], "adaptive_evidence_qra")
        matrix = database.get_result_document(first_run["id"], "risk_matrix")
        ranking = adaptive["human_risk"]["segment_risk"]["ranking"]
        if len(ranking) != 1 or ranking[0]["segment_id"] != "GDBZYQ-JJ-1":
            raise AssertionError("阶段2试算未保持阶段1单段边界")
        individual = adaptive["human_risk"]["individual_risk"]
        societal = adaptive["human_risk"]["societal_risk"]
        if individual.get("available") or individual["maximum"]["value_per_year"] is not None:
            raise AssertionError("缺少空间受体时不得输出数值IR或把缺失写成0")
        if societal.get("fn_curve_available") or societal.get("fn_curve"):
            raise AssertionError("缺少人口分布时不得输出项目F-N曲线")
        if float(societal["pipeline_pll_per_year"]) <= 0.0:
            raise AssertionError("自适应筛查节点未形成PLL筛查估计")
        if adaptive.get("formal_report_allowed"):
            raise AssertionError("真实筛查结果错误放行了正式报告")

        human_missing = {
            item["path"] for item in first_nodes["human_qra"]["missing_inputs"]
        }
        required_human_gaps = {
            "assessment",
            "frequency_library",
            "weather_joint_probability",
            "population_cells",
            "ignition_model",
        }
        if not required_human_gaps.issubset(human_missing):
            raise AssertionError("完整QRA节点未展开自身主要缺失数据段")

        dashboard_status, _, dashboard = _request(
            base, f"/runs/{quote(str(first_run['id']), safe='')}/"
        )
        if dashboard_status != 200 or "不可算（缺空间受体）" not in dashboard.decode(
            "utf-8"
        ):
            raise AssertionError("HTML报告未明确显示IR不可计算")
        export_status, content_type, export = _request(
            base, f"/admin/api/runs/{quote(str(first_run['id']), safe='')}/export"
        )
        if export_status != 200 or "application/zip" not in content_type:
            raise AssertionError("阶段2结果ZIP导出失败")
        export_path = output_root / "stage2-run-a-export.zip"
        export_path.write_bytes(export)

        _extract_artifacts(database, str(first_run["id"]), output_root / "run-a-artifacts")
        _extract_artifacts(database, str(second_run["id"]), output_root / "run-b-artifacts")

        segment_rows = database.get_segment_results(str(first_run["id"]))
        segment_csv_path = output_root / "segment-risk-table.csv"
        with segment_csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(segment_rows[0]))
            writer.writeheader()
            writer.writerows(segment_rows)

        supplement = _supplement_priority_list()
        _write_json(output_root / "supplement-priority-list.json", supplement)
        difference_register = {
            "schema_version": "1.0.0",
            "four_layer_diagnosis": {
                "data": "权威内部分段、坐标、人口、温度、壁厚、阀门和气象等缺失；不能解释为真实低风险。",
                "mapping": "未发现新增映射缺陷；3.0–3.8 MPa范围被忠实保留，未擅自折算为单值。",
                "parameters": "失效频率、K_j、物性、点火和准则未批准；筛查模型默认值只用于内部排序能力验证。",
                "formula": "本次0个节点运行失败；现有公式链仍处于工程验证前状态，不能据此声明模型已验证。",
            },
            "software_defects": [
                {
                    "defect_id": "DEF-M2-001",
                    "severity": "P1",
                    "status": "CLOSED_WITH_REGRESSION",
                    "symptom": "无空间人口时最大IR显示0，可能把缺失误读为低风险。",
                    "rectification": "IR改为null和NOT_CALCULATED_MISSING_SPATIAL_RECEPTORS；HTML显示不可算；不生成IR/F-N相关图表。",
                    "regression": "tests.integration.test_stage2_jiujiang_trial",
                },
                {
                    "defect_id": "DEF-M2-002",
                    "severity": "P1",
                    "status": "CLOSED_WITH_REGRESSION",
                    "symptom": "完整QRA因依赖节点阻断时自身missing_inputs为空，遗漏气象、人口和点火缺口。",
                    "rectification": "规划阶段即使存在阻断依赖也执行安全预检，完整展开复合节点数据合同缺口。",
                    "regression": "tests.integration.test_stage2_jiujiang_trial",
                },
            ],
            "open_software_defects": [],
        }
        _write_json(output_root / "execution-difference-register.json", difference_register)

        top = ranking[0]
        summary = {
            "schema_version": "1.0.0",
            "executed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "technical_status": "PASSED_INTERNAL_STAGE2_FUNCTION_VALIDATION",
            "g2_status": "PASSED_SCREENING_EXECUTION",
            "m2_status": "HOLD_FOR_DATA_COMPLETION_AND_BUSINESS_REVIEW",
            "scope": "九江支线阶段1不可变快照的当前能力受控试算、差异整改和重复性验证；不构成完整QRA或正式风险结论。",
            "software": {
                "platform_package_version": "0.9.1",
                "engine_version": ENGINE_VERSION,
                "dynamic_schema_version": DYNAMIC_SCHEMA_VERSION,
            },
            "input": {
                "snapshot_id": snapshot_id,
                "input_sha256": expected_input_hash,
                "stage1_database_sha256": _sha256_file(stage1_database_path),
                "stage2_database_sha256_after_runs": _sha256_file(stage2_database_path),
                "segment_count": len(case.get("segments", [])),
                "population_cell_count": len(case.get("population_cells", [])),
                "g1_status": stage1_summary.get("g1_status"),
            },
            "repeatability": {
                "run_a_id": first_run["id"],
                "run_b_id": second_run["id"],
                "run_a_status": first_run["status"],
                "run_b_status": second_run["status"],
                "dynamic_status": first_run["summary"]["dynamic_status"],
                "numerical_result_sha256": first_run["result_sha256"],
                "repeated_result_sha256": second_run["result_sha256"],
                "result_hash_equal": True,
            },
            "node_execution": {
                "completed": sorted(completed_nodes),
                "skipped": sorted(skipped_nodes),
                "failed": sorted(failed_nodes),
            },
            "risk_result": {
                "result_tier": adaptive["result_tier"],
                "population_source": adaptive["population_source"],
                "pipeline_pll_screening_per_year": societal["pipeline_pll_per_year"],
                "segment_pll_screening_per_year": top["risk_value_fatalities_per_year"],
                "pll_per_km_screening": top["risk_density_fatalities_per_km_year"],
                "screening_lower_bound": top["risk_value_lower_screening_bound"],
                "screening_upper_bound": top["risk_value_upper_screening_bound"],
                "dominant_scenario": top["dominant_risk_scenario"]["scenario_id"],
                "maximum_conditional_consequence": top[
                    "maximum_conditional_consequence"
                ]["expected_fatalities"],
                "risk_matrix_display_band": matrix["segments"][0]["display_risk_band"],
                "individual_risk_available": False,
                "fn_curve_available": False,
                "formal_acceptance_judgement_allowed": False,
            },
            "business_interpretation": {
                "ranking_interpretability": "NOT_COMPARABLE_SINGLE_WHOLE_LINE_SEGMENT",
                "site_consistency_review": "PENDING_BUSINESS_PERSONNEL_AND_SPATIAL_DATA",
                "true_low_risk_conclusion_allowed": False,
                "note": "唯一管段排名仅表示全线单段占比100%；主导场景和PLL受模型先验控制，不能解释为现场真实高风险位置或真实低风险。",
            },
            "rectification": {
                "closed_p0_p1_software_defect_count": 2,
                "open_p0_p1_software_defect_count": 0,
                "register": "execution-difference-register.json",
                "regression_test": "tests/integration/test_stage2_jiujiang_trial.py",
            },
            "deliverables": {
                "artifacts": "run-a-artifacts/",
                "repeat_artifacts": "run-b-artifacts/",
                "segment_risk_table": segment_csv_path.name,
                "supplement_priority_list": "supplement-priority-list.json",
                "difference_register": "execution-difference-register.json",
                "export_zip": export_path.name,
                "export_zip_sha256": _sha256_file(export_path),
            },
            "completion_checks": {
                "stable_repeat_run": True,
                "same_input_model_numerical_hash": True,
                "all_skips_have_reason": True,
                "partial_result_tier_explicit": True,
                "missing_ir_not_zero": True,
                "business_site_interpretation_complete": False,
                "complete_qra": False,
            },
        }
        _write_json(output_root / "stage2-acceptance-summary.json", summary)
        return summary
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="执行九江支线阶段2真实数据试算与差异整改验收")
    parser.add_argument(
        "--stage1-root",
        type=Path,
        default=PROJECT_ROOT / "workspace" / "runtime" / "stage1-real-data-acceptance-final",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "workspace" / "runtime" / "stage2-real-data-trial-final",
    )
    args = parser.parse_args()
    summary = run(args.stage1_root.resolve(), args.output_root.resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
