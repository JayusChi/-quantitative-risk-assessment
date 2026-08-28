from __future__ import annotations

import argparse
import copy
import csv
import json
import math
from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path
from typing import Any, Callable

from qra_engine.dynamic import DYNAMIC_SCHEMA_VERSION, run_dynamic_flow


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_CASE = PROJECT_ROOT / "tests" / "fixtures" / "qra_synthetic_case_v1.json"
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "workspace" / "outputs" / "QRA全合成测试版_v1"
)


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    scenario_id: str
    name_zh: str
    description: str
    expected_relation: str
    mutate: Callable[[dict[str, Any]], list[dict[str, Any]]]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _multiply_factors(
    case: dict[str, Any],
    segment_ids: set[str],
    mechanism_multipliers: dict[str, float],
) -> list[dict[str, Any]]:
    changes = []
    for segment_id in sorted(segment_ids):
        factors = case["segment_correction_factor"][segment_id]
        for mechanism, multiplier in mechanism_multipliers.items():
            before = float(factors[mechanism])
            after = before * float(multiplier)
            factors[mechanism] = after
            changes.append(
                {
                    "path": f"segment_correction_factor.{segment_id}.{mechanism}",
                    "before": before,
                    "after": after,
                    "reason": "synthetic scenario perturbation",
                }
            )
    return changes


def _scale_explicit_observations(
    case: dict[str, Any],
    indicator_id: str,
    multiplier: float,
    *,
    maximum: float | None = None,
) -> list[dict[str, Any]]:
    changes = []
    indicators = case.get("engineering_indicators", {})
    observation_groups = [
        indicators.get("observations_global", {}),
        *indicators.get("observations_by_segment", {}).values(),
        *indicators.get("observations_by_archetype", {}).values(),
    ]
    for index, observations in enumerate(observation_groups, start=1):
        observation = observations.get(indicator_id)
        if not isinstance(observation, dict):
            continue
        value = observation.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        before = float(value)
        after = before * multiplier
        if maximum is not None:
            after = min(after, maximum)
        observation["value"] = after
        observation["quality"] = "D"
        observation["source_ref"] = "synthetic://scenario-perturbation"
        changes.append(
            {
                "path": f"engineering_indicators.observation_group_{index}.{indicator_id}",
                "before": before,
                "after": after,
                "reason": "synthetic field observation perturbation",
            }
        )
    return changes


def _baseline(_: dict[str, Any]) -> list[dict[str, Any]]:
    return []


def _corrosion_degradation(case: dict[str, Any]) -> list[dict[str, Any]]:
    target_segments = {
        *(f"SEG-{index:03d}" for index in range(5, 9)),
        *(f"SEG-{index:03d}" for index in range(17, 21)),
    }
    changes = _multiply_factors(
        case,
        target_segments,
        {
            "external_corrosion": 2.50,
            "internal_corrosion": 1.80,
            "stress_corrosion_cracking": 1.40,
        },
    )
    changes.extend(
        _scale_explicit_observations(
            case,
            "external_corrosion.coating_defect_density_per_km",
            2.0,
        )
    )
    changes.extend(
        _scale_explicit_observations(
            case,
            "inspection_integrity.maximum_corrosion_depth_ratio",
            1.8,
            maximum=0.85,
        )
    )
    return changes


def _third_party_surge(case: dict[str, Any]) -> list[dict[str, Any]]:
    target_segments = {f"SEG-{index:03d}" for index in range(7, 17)}
    changes = _multiply_factors(
        case,
        target_segments,
        {"third_party_damage": 2.80},
    )
    changes.extend(
        _scale_explicit_observations(
            case,
            "third_party.excavation_events_per_km_year",
            2.5,
        )
    )
    changes.extend(
        _scale_explicit_observations(
            case,
            "third_party.patrol_interval_days",
            1.5,
        )
    )
    return changes


def _high_pressure_population_peak(case: dict[str, Any]) -> list[dict[str, Any]]:
    changes = []
    before_pressure = float(case["pipeline"]["operating_pressure_mpa"])
    after_pressure = 9.5
    case["pipeline"]["operating_pressure_mpa"] = after_pressure
    changes.append(
        {
            "path": "pipeline.operating_pressure_mpa",
            "before": before_pressure,
            "after": after_pressure,
            "reason": "synthetic high operating-pressure scenario",
        }
    )
    for cell in case["population_cells"]:
        for field in ("population_day", "population_night"):
            before = int(cell[field])
            after = int(math.ceil(before * 1.50))
            cell[field] = after
            changes.append(
                {
                    "path": f"population_cells.{cell['cell_id']}.{field}",
                    "before": before,
                    "after": after,
                    "reason": "synthetic peak-occupancy scenario",
                }
            )
    return changes


def _mitigation_package(case: dict[str, Any]) -> list[dict[str, Any]]:
    all_segments = {str(row["segment_id"]) for row in case["segments"]}
    changes = _multiply_factors(
        case,
        all_segments,
        {
            "external_corrosion": 0.72,
            "third_party_damage": 0.50,
            "natural_geohazard": 0.80,
            "misoperation": 0.75,
        },
    )
    for segment in case["segments"]:
        for field, multiplier in (
            ("leak_detection_time_s", 0.50),
            ("valve_closure_time_s", 0.60),
        ):
            before = float(segment[field])
            after = max(30.0, before * multiplier)
            segment[field] = after
            changes.append(
                {
                    "path": f"segments.{segment['segment_id']}.{field}",
                    "before": before,
                    "after": after,
                    "reason": "synthetic mitigation package",
                }
            )
    return changes


SCENARIOS = (
    ScenarioSpec(
        "S00_BASELINE",
        "基准场景",
        "20段虚拟埋地天然气管道，包含农村、村庄、学校、工业和地灾区。",
        "reference",
        _baseline,
    ),
    ScenarioSpec(
        "S10_CORROSION_DEGRADATION",
        "腐蚀恶化",
        "提高指定管段外腐蚀、内腐蚀和应力腐蚀修正因子，并同步恶化测试观测。",
        "failure_frequency_and_pll_above_baseline",
        _corrosion_degradation,
    ),
    ScenarioSpec(
        "S20_THIRD_PARTY_SURGE",
        "第三方活动激增",
        "提高村庄、学校和工业段的第三方损伤修正因子及开挖活动测试值。",
        "failure_frequency_and_pll_above_baseline",
        _third_party_surge,
    ),
    ScenarioSpec(
        "S30_HIGH_PRESSURE_POPULATION_PEAK",
        "高压力与人口峰值",
        "把运行压力提高至设计压力以内的9.5 MPa，并把全部昼夜人口提高50%。",
        "pll_and_ir_above_baseline",
        _high_pressure_population_peak,
    ),
    ScenarioSpec(
        "S40_MITIGATION_PACKAGE",
        "综合治理措施",
        "降低腐蚀、第三方、地灾和误操作频率因子，并缩短检测与关阀时间。",
        "failure_frequency_and_pll_below_baseline",
        _mitigation_package,
    ),
)


def build_scenario_case(template: dict[str, Any], spec: ScenarioSpec) -> dict[str, Any]:
    case = copy.deepcopy(template)
    metadata = case["metadata"]
    metadata["case_id"] = f"SYN-TEST-EDITION-V1-{spec.scenario_id}"
    metadata["name"] = f"全合成QRA测试版：{spec.name_zh}"
    metadata["version"] = "1.0.0"
    metadata["created_at"] = "2026-08-26"
    metadata["data_classification"] = "SYNTHETIC_TEST_ONLY"
    metadata["allowed_use"] = [
        "software_development",
        "algorithm_verification",
        "regression_test",
        "training_demo",
    ]
    metadata["prohibited_use"] = [
        "real_asset_assessment",
        "formal_qra_report",
        "regulatory_submission",
        "safety_decision",
        "parameter_calibration_claim",
    ]
    case["pipeline"]["pipeline_id"] = "PIPE-SYNTHETIC-TEST-EDITION-V1"
    case["assessment"]["assessment_id"] = f"RA-{spec.scenario_id}"
    case["assessment"]["as_of"] = "2026-08-26"
    case["assessment"]["failure_probability_horizon_years"] = 1.0
    case["engineering_indicators"]["data_classification"] = "SYNTHETIC_TEST_ONLY"
    case["frequency_library"]["data_classification"] = "SYNTHETIC_TEST_ONLY"
    case["frequency_correction_model"]["status"] = "SYNTHETIC_TEST_ONLY"

    case.pop("mock_adapter_output", None)
    case.pop("damage_model", None)
    case.pop("expected_aggregation", None)
    changes = spec.mutate(case)
    case["synthetic_test_edition"] = {
        "edition_id": "QRA-SYNTHETIC-TEST-EDITION-V1",
        "scenario_id": spec.scenario_id,
        "scenario_name_zh": spec.name_zh,
        "description": spec.description,
        "expected_relation": spec.expected_relation,
        "zero_real_project_data": True,
        "deterministic_generation": True,
        "random_seed": None,
        "template": "tests/fixtures/qra_synthetic_case_v1.json",
        "changes": changes,
        "formal_release_allowed": False,
        "warning": (
            "全部资产、现场、人口、气象、频率和物理参数均为人工合成；"
            "结果只验证软件链路和场景响应，不代表任何真实管道。"
        ),
    }
    return case


def _scenario_metrics(output_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    frequency = _read_json(output_dir / "nodes" / "failure_frequency.json")
    source_term = _read_json(output_dir / "nodes" / "aqt3046_source_term.json")
    jet_fire = _read_json(output_dir / "nodes" / "jet_fire_thresholds.json")
    human = _read_json(output_dir / "nodes" / "human_qra.json")
    risk = human["human_risk"]
    ranking = risk["segment_risk"]["ranking"]
    rupture_flows = [
        float(row["mass_flow_rate_kg_s"])
        for row in source_term["rows"]
        if row["loc_id"] == "rupture"
    ]
    threshold_distances = [
        float(row["threshold_distance_m"])
        for row in jet_fire["rows"]
        if float(row["threshold_heat_flux_kw_m2"]) == 37.5
    ]
    completed = [row for row in manifest["nodes"] if row["status"] == "COMPLETED"]
    skipped = [row for row in manifest["nodes"] if row["status"].startswith("SKIPPED")]
    failed = [row for row in manifest["nodes"] if row["status"] == "FAILED_ISOLATED"]
    return {
        "dynamic_status": manifest["status"],
        "completed_node_count": len(completed),
        "skipped_node_count": len(skipped),
        "failed_node_count": len(failed),
        "numerical_result_sha256": manifest["numerical_result_sha256"],
        "annual_failure_frequency": frequency["total_initiating_frequency_per_year"],
        "one_year_at_least_one_failure_probability": frequency[
            "at_least_one_failure_probability_over_horizon"
        ],
        "pipeline_pll_per_year": risk["societal_risk"]["pipeline_pll_per_year"],
        "maximum_individual_risk_per_year": risk["individual_risk"]["maximum"][
            "value_per_year"
        ],
        "top_segment_id": ranking[0]["segment_id"],
        "top_segment_pll_per_year": ranking[0]["risk_value_fatalities_per_year"],
        "maximum_initial_rupture_flow_kg_s": max(rupture_flows),
        "maximum_jet_fire_37_5_kw_m2_distance_m": max(threshold_distances),
        "expanded_scenario_count": human["calculation_diagnostics"][
            "expanded_scenario_count"
        ],
        "formal_report_allowed": human["run"]["formal_report_allowed"],
        "formal_report_blockers": human["run"]["formal_report_blockers"],
    }


def _ratio(value: float, baseline: float) -> float:
    return value / baseline if baseline else 0.0


def _verify_relations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {row["scenario_id"]: row for row in rows}
    baseline = by_id["S00_BASELINE"]
    checks = []

    def add(check_id: str, condition: bool, actual: Any, expected: str) -> None:
        checks.append(
            {
                "check_id": check_id,
                "passed": bool(condition),
                "actual": actual,
                "expected": expected,
            }
        )
        if not condition:
            raise AssertionError(f"场景响应检查失败：{check_id}，实际{actual}，预期{expected}")

    for scenario_id in (
        "S10_CORROSION_DEGRADATION",
        "S20_THIRD_PARTY_SURGE",
    ):
        row = by_id[scenario_id]
        add(
            f"{scenario_id}.frequency_above_baseline",
            row["annual_failure_frequency"] > baseline["annual_failure_frequency"],
            row["annual_failure_frequency"],
            f"> {baseline['annual_failure_frequency']}",
        )
        add(
            f"{scenario_id}.pll_above_baseline",
            row["pipeline_pll_per_year"] > baseline["pipeline_pll_per_year"],
            row["pipeline_pll_per_year"],
            f"> {baseline['pipeline_pll_per_year']}",
        )

    high = by_id["S30_HIGH_PRESSURE_POPULATION_PEAK"]
    add(
        "S30_HIGH_PRESSURE_POPULATION_PEAK.pll_above_baseline",
        high["pipeline_pll_per_year"] > baseline["pipeline_pll_per_year"],
        high["pipeline_pll_per_year"],
        f"> {baseline['pipeline_pll_per_year']}",
    )
    add(
        "S30_HIGH_PRESSURE_POPULATION_PEAK.ir_above_baseline",
        high["maximum_individual_risk_per_year"]
        > baseline["maximum_individual_risk_per_year"],
        high["maximum_individual_risk_per_year"],
        f"> {baseline['maximum_individual_risk_per_year']}",
    )

    mitigated = by_id["S40_MITIGATION_PACKAGE"]
    add(
        "S40_MITIGATION_PACKAGE.frequency_below_baseline",
        mitigated["annual_failure_frequency"] < baseline["annual_failure_frequency"],
        mitigated["annual_failure_frequency"],
        f"< {baseline['annual_failure_frequency']}",
    )
    add(
        "S40_MITIGATION_PACKAGE.pll_below_baseline",
        mitigated["pipeline_pll_per_year"] < baseline["pipeline_pll_per_year"],
        mitigated["pipeline_pll_per_year"],
        f"< {baseline['pipeline_pll_per_year']}",
    )

    for row in rows:
        add(
            f"{row['scenario_id']}.all_nodes_completed",
            row["dynamic_status"] == "PASS"
            and row["completed_node_count"] == 11
            and row["skipped_node_count"] == 0
            and row["failed_node_count"] == 0,
            {
                "status": row["dynamic_status"],
                "completed": row["completed_node_count"],
                "skipped": row["skipped_node_count"],
                "failed": row["failed_node_count"],
            },
            "PASS, completed=11, skipped=0, failed=0",
        )
        add(
            f"{row['scenario_id']}.formal_report_blocked",
            not row["formal_report_allowed"],
            row["formal_report_allowed"],
            "False",
        )
    return checks


def _write_comparison_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "scenario_id",
        "scenario_name_zh",
        "annual_failure_frequency",
        "one_year_at_least_one_failure_probability",
        "pipeline_pll_per_year",
        "maximum_individual_risk_per_year",
        "top_segment_id",
        "top_segment_pll_per_year",
        "maximum_initial_rupture_flow_kg_s",
        "maximum_jet_fire_37_5_kw_m2_distance_m",
        "failure_frequency_ratio_to_baseline",
        "pll_ratio_to_baseline",
        "ir_ratio_to_baseline",
        "dynamic_status",
        "completed_node_count",
        "formal_report_allowed",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _render_index(output_root: Path, rows: list[dict[str, Any]]) -> None:
    cards = []
    table_rows = []
    for row in rows:
        scenario_id = escape(row["scenario_id"])
        name = escape(row["scenario_name_zh"])
        report_path = f"scenarios/{scenario_id}/report_dashboard.html"
        cards.append(
            f"""
            <article class="card">
              <div class="tag">{scenario_id}</div>
              <h2>{name}</h2>
              <p>{escape(row['description'])}</p>
              <dl>
                <div><dt>年失效频率</dt><dd>{row['annual_failure_frequency']:.4e}</dd></div>
                <div><dt>一年失效概率</dt><dd>{row['one_year_at_least_one_failure_probability']:.4%}</dd></div>
                <div><dt>PLL</dt><dd>{row['pipeline_pll_per_year']:.4e}</dd></div>
                <div><dt>最大IR</dt><dd>{row['maximum_individual_risk_per_year']:.4e}</dd></div>
              </dl>
              <a href="{report_path}">打开场景报告</a>
            </article>
            """
        )
        table_rows.append(
            "<tr>"
            f"<td>{scenario_id}</td>"
            f"<td>{name}</td>"
            f"<td>{row['failure_frequency_ratio_to_baseline']:.3f}</td>"
            f"<td>{row['pll_ratio_to_baseline']:.3f}</td>"
            f"<td>{row['ir_ratio_to_baseline']:.3f}</td>"
            f"<td>{escape(str(row['top_segment_id']))}</td>"
            f"<td>{escape(str(row['dynamic_status']))}</td>"
            "</tr>"
        )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>QRA全合成测试版 v1</title>
  <style>
    :root {{ --ink:#10233f; --blue:#1769aa; --pale:#edf5fb; --line:#cbdbea; --warn:#8a4b00; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:"Microsoft YaHei",Arial,sans-serif; color:var(--ink); background:#f5f8fb; }}
    header {{ padding:48px max(24px,6vw); color:white; background:linear-gradient(125deg,#0b2748,#1769aa); }}
    header h1 {{ margin:0 0 10px; font-size:34px; }}
    header p {{ max-width:920px; margin:0; line-height:1.8; }}
    main {{ max-width:1320px; margin:auto; padding:30px 24px 56px; }}
    .notice {{ padding:18px 20px; border-left:5px solid #e38b19; background:#fff4df; color:var(--warn); border-radius:8px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:18px; margin:24px 0 36px; }}
    .card {{ background:white; border:1px solid var(--line); border-radius:14px; padding:20px; box-shadow:0 8px 24px rgba(30,60,90,.07); }}
    .tag {{ color:var(--blue); font-size:12px; font-weight:700; letter-spacing:.05em; }}
    .card h2 {{ margin:8px 0; font-size:21px; }}
    .card p {{ color:#4b6078; min-height:66px; line-height:1.65; }}
    dl {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
    dl div {{ padding:10px; background:var(--pale); border-radius:8px; }}
    dt {{ color:#597089; font-size:12px; }} dd {{ margin:4px 0 0; font-weight:700; }}
    a {{ display:inline-block; margin-top:14px; padding:10px 14px; color:white; background:var(--blue); border-radius:8px; text-decoration:none; }}
    .table-wrap {{ overflow:auto; background:white; border-radius:12px; border:1px solid var(--line); }}
    table {{ width:100%; border-collapse:collapse; }} th,td {{ padding:12px 14px; border-bottom:1px solid var(--line); text-align:left; white-space:nowrap; }}
    th {{ background:#eaf3fb; }}
    footer {{ margin-top:24px; color:#65788c; font-size:13px; }}
  </style>
</head>
<body>
  <header>
    <h1>QRA全合成测试版 v1</h1>
    <p>零真实项目数据的端到端测试包：虚拟管段、现场指标、频率、气象、人口、点火和物性，经11个动态节点生成频率、失效概率、源项、后果、IR、F-N、PLL、矩阵和报告。</p>
  </header>
  <main>
    <div class="notice"><strong>测试用途：</strong>所有数据和结果均为人工合成，不得用于真实资产评价、监管报送或安全决策。</div>
    <section class="grid">{''.join(cards)}</section>
    <h2>相对基准场景</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>场景ID</th><th>场景</th><th>频率倍数</th><th>PLL倍数</th><th>IR倍数</th><th>最高风险管段</th><th>状态</th></tr></thead>
      <tbody>{''.join(table_rows)}</tbody>
    </table></div>
    <footer>生成器：tools/run_synthetic_test_edition.py · 动态输出契约 {escape(DYNAMIC_SCHEMA_VERSION)}</footer>
  </main>
</body>
</html>"""
    (output_root / "index.html").write_text(html, encoding="utf-8")


def _write_readme(output_root: Path) -> None:
    text = """# QRA全合成测试版 v1

本目录完全使用人工合成数据，不包含任何真实项目事实。

## 使用方法

1. 打开 `index.html` 查看五个场景和对比结果。
2. `inputs/` 保存可直接导入平台的场景JSON。
3. `scenarios/<场景ID>/report_dashboard.html` 是每个场景的完整动态报告。
4. `scenario-comparison.csv` 和 `scenario-comparison.json` 保存场景指标及相对基准倍数。
5. `scenario-response-checks.json` 保存单调性、节点完成度和正式签发阻断检查。

## 一键重建

```powershell
.\\.venv\\Scripts\\python.exe .\\tools\\run_synthetic_test_edition.py
```

## 边界

测试版用于软件开发、算法验证、回归和培训。失效频率库、K_j、人口、气象、点火、物性及后果参数均未基于真实资产校准；物理模型仍受报告列出的正式发布门禁约束。
"""
    (output_root / "README.md").write_text(text, encoding="utf-8")


def run(output_root: Path, *, generate_charts: bool = True) -> dict[str, Any]:
    if not TEMPLATE_CASE.is_file():
        raise FileNotFoundError(f"合成模板不存在：{TEMPLATE_CASE}")
    output_root.mkdir(parents=True, exist_ok=True)
    input_root = output_root / "inputs"
    scenario_root = output_root / "scenarios"
    template = _read_json(TEMPLATE_CASE)
    rows = []

    for spec in SCENARIOS:
        case = build_scenario_case(template, spec)
        input_path = input_root / f"{spec.scenario_id}.json"
        _write_json(input_path, case)
        scenario_output = scenario_root / spec.scenario_id
        manifest = run_dynamic_flow(
            case,
            scenario_output,
            generate_charts=generate_charts,
            job_id=f"SYNTHETIC-TEST-EDITION-V1-{spec.scenario_id}",
        )
        metrics = _scenario_metrics(scenario_output, manifest)
        rows.append(
            {
                "scenario_id": spec.scenario_id,
                "scenario_name_zh": spec.name_zh,
                "description": spec.description,
                "expected_relation": spec.expected_relation,
                "input_file": str(input_path),
                "output_directory": str(scenario_output),
                **metrics,
            }
        )

    baseline = rows[0]
    for row in rows:
        row["failure_frequency_ratio_to_baseline"] = _ratio(
            float(row["annual_failure_frequency"]),
            float(baseline["annual_failure_frequency"]),
        )
        row["pll_ratio_to_baseline"] = _ratio(
            float(row["pipeline_pll_per_year"]),
            float(baseline["pipeline_pll_per_year"]),
        )
        row["ir_ratio_to_baseline"] = _ratio(
            float(row["maximum_individual_risk_per_year"]),
            float(baseline["maximum_individual_risk_per_year"]),
        )

    checks = _verify_relations(rows)
    _write_json(output_root / "scenario-comparison.json", rows)
    _write_json(output_root / "scenario-response-checks.json", checks)
    _write_comparison_csv(output_root / "scenario-comparison.csv", rows)
    _render_index(output_root, rows)
    _write_readme(output_root)
    summary = {
        "schema_version": "1.0.0",
        "edition_id": "QRA-SYNTHETIC-TEST-EDITION-V1",
        "generated_on": date.today().isoformat(),
        "status": "PASSED_ALL_SYNTHETIC_SCENARIOS",
        "zero_real_project_data": True,
        "scenario_count": len(rows),
        "all_scenarios_passed": all(row["dynamic_status"] == "PASS" for row in rows),
        "all_response_checks_passed": all(row["passed"] for row in checks),
        "formal_report_allowed": False,
        "index": str(output_root / "index.html"),
        "comparison": str(output_root / "scenario-comparison.json"),
        "scenario_numerical_hashes": {
            row["scenario_id"]: row["numerical_result_sha256"] for row in rows
        },
    }
    _write_json(output_root / "edition-summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="生成并运行零真实数据的QRA全合成测试版"
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--no-charts", action="store_true")
    args = parser.parse_args()
    summary = run(
        args.output_root.resolve(),
        generate_charts=not args.no_charts,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
