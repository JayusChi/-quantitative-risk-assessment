"""Generate a fresh synthetic QRA batch and a controlled report for review."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from db_qra.controlled_reporting import (  # noqa: E402
    ControlledReportService,
    build_controlled_report_zip,
)
from db_qra.database import QraDatabase, bytes_sha256, json_sha256  # noqa: E402
from db_qra.engine_adapter import execute_run  # noqa: E402

BASE_ENVELOPE = (
    PROJECT_ROOT
    / "resources"
    / "synthetic"
    / "full-chain-v1"
    / "stage2"
    / "generated"
    / "S00_BASELINE_D00_CLEAN"
    / "golden"
    / "expected-snapshot.json"
)
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "output" / "pdf" / "leader-review-b01-20260902"
BATCH_ID = "SYN-LEADER-20260902-B01"
PROJECT_NAME = "全合成QRA测试：综合压力新模拟批次B01"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def _record_change(
    changes: list[dict[str, Any]],
    *,
    path: str,
    before: Any,
    after: Any,
    reason: str,
) -> None:
    changes.append(
        {
            "path": path,
            "before": before,
            "after": after,
            "reason": reason,
        }
    )


def build_new_batch() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    envelope = _read_json(BASE_ENVELOPE)
    case = copy.deepcopy(envelope["qra_input"])
    changes: list[dict[str, Any]] = []

    metadata = case["metadata"]
    metadata["case_id"] = BATCH_ID
    metadata["name"] = PROJECT_NAME
    metadata["project_name"] = PROJECT_NAME
    metadata["created_at"] = "2026-09-02"
    metadata["converter_note"] = (
        "基于全合成基准数据生成综合压力批次；未使用真实项目数据，未调用外部模型。"
    )

    pipeline = case["pipeline"]
    before_pressure = float(pipeline["operating_pressure_mpa"])
    after_pressure = 8.9
    pipeline["operating_pressure_mpa"] = after_pressure
    _record_change(
        changes,
        path="pipeline.operating_pressure_mpa",
        before=before_pressure,
        after=after_pressure,
        reason="新模拟批次的运行压力扰动，保持低于10.0 MPa设计压力",
    )

    factors = case["segment_correction_factor"]
    for index in range(5, 9):
        segment_id = f"SEG-{index:03d}"
        for mechanism, multiplier in (
            ("external_corrosion", 1.65),
            ("stress_corrosion_cracking", 1.25),
        ):
            before = float(factors[segment_id][mechanism])
            after = round(before * multiplier, 12)
            factors[segment_id][mechanism] = after
            _record_change(
                changes,
                path=f"segment_correction_factor.{segment_id}.{mechanism}",
                before=before,
                after=after,
                reason="模拟局部腐蚀与应力腐蚀环境加重",
            )

    for index in range(11, 16):
        segment_id = f"SEG-{index:03d}"
        mechanism = "third_party_damage"
        before = float(factors[segment_id][mechanism])
        after = round(before * 1.80, 12)
        factors[segment_id][mechanism] = after
        _record_change(
            changes,
            path=f"segment_correction_factor.{segment_id}.{mechanism}",
            before=before,
            after=after,
            reason="模拟施工季第三方活动增加",
        )

    for cell in case["population_cells"]:
        x_coordinate = float(cell["xy_m"][0])
        if not 3000.0 <= x_coordinate <= 5500.0:
            continue
        for key in ("population_day", "population_night"):
            before = int(cell[key])
            after = int(round(before * 1.30))
            cell[key] = after
            _record_change(
                changes,
                path=f"population_cells.{cell['cell_id']}.{key}",
                before=before,
                after=after,
                reason="模拟沿线阶段性人口暴露增加30%",
            )

    edition = case["synthetic_test_edition"]
    edition.update(
        {
            "scenario_id": "S50_COMBINED_STRESS_B01",
            "scenario_name_zh": "综合压力新模拟批次B01",
            "description": (
                "在全合成基准上组合运行压力、局部腐蚀、第三方活动和人口暴露扰动。"
            ),
            "expected_relation": "stress_test_above_baseline",
            "changes": changes,
            "deterministic_generation": True,
            "random_seed": None,
            "formal_release_allowed": False,
            "zero_real_project_data": True,
        }
    )
    return case, changes


def generate(output_root: Path) -> dict[str, Any]:
    output_root = output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"输出目录已存在且非空：{output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    case, changes = build_new_batch()
    input_path = output_root / "新模拟批次B01_输入数据.json"
    _write_json(input_path, case)

    database_path = output_root / "新模拟批次B01.sqlite3"
    database = QraDatabase(database_path)
    database.initialize()
    project = database.create_project(
        name=PROJECT_NAME,
        case_id=BATCH_ID,
        data_classification="SYNTHETIC_TEST_ONLY",
        is_demo=False,
        actor="codex-local-run",
    )
    snapshot_id, _ = database.import_case(
        case,
        name=f"{PROJECT_NAME} · 已确认模拟输入",
        source_path=str(input_path),
    )
    project_id = str(project["id"])
    database.attach_project_snapshot(project_id, snapshot_id)
    snapshot = database.snapshot_metadata(snapshot_id)
    run_id = database.create_run(
        snapshot_id,
        str(snapshot["payload_sha256"]),
        generate_charts=True,
    )
    database.attach_project_run(project_id, run_id)
    execute_run(
        database,
        run_id,
        snapshot_id,
        generate_charts=True,
        runtime_root=output_root / "runtime",
    )

    service = ControlledReportService(database)
    build = service.generate(project_id, actor="codex-local-run")
    integrity = service.verify_integrity(str(build.report["id"]))
    if integrity["status"] != "PASS":
        raise RuntimeError("新报告完整性校验未通过")

    pdf_path = output_root / "新模拟批次B01_QRA受控测试报告.pdf"
    html_path = output_root / "新模拟批次B01_QRA受控测试报告.html"
    docx_path = output_root / "新模拟批次B01_QRA受控测试报告.docx"
    zip_path = output_root / "新模拟批次B01_完整交付包.zip"
    pdf_path.write_bytes(build.pdf)
    html_path.write_bytes(build.html)
    docx_path.write_bytes(build.docx)
    zip_path.write_bytes(build_controlled_report_zip(database, str(build.report["id"])))
    _write_json(output_root / "报告上下文.json", build.context)
    _write_json(output_root / "报告校验结果.json", build.validation)

    run = database.get_run(run_id)
    nodes = database.list_nodes(run_id)
    completed_nodes = sum(row["status"] == "COMPLETED" for row in nodes)
    summary = {
        "batch_id": BATCH_ID,
        "project_name": PROJECT_NAME,
        "data_classification": "SYNTHETIC_TEST_ONLY",
        "change_count": len(changes),
        "input_sha256": json_sha256(case),
        "snapshot_id": snapshot_id,
        "snapshot_sha256": snapshot["payload_sha256"],
        "run_id": run_id,
        "run_status": run["status"],
        "completed_node_count": completed_nodes,
        "total_node_count": len(nodes),
        "result_sha256": run["result_sha256"],
        "report_id": build.report["id"],
        "report_status": build.report["status"],
        "report_validation": build.validation["status"],
        "report_integrity": integrity["status"],
        "formal_report_allowed": False,
        "deliverables": {
            "pdf": {
                "path": str(pdf_path),
                "sha256": bytes_sha256(build.pdf),
                "byte_count": len(build.pdf),
            },
            "html": str(html_path),
            "docx": str(docx_path),
            "zip": str(zip_path),
            "input": str(input_path),
        },
        "changes": changes,
    }
    _write_json(output_root / "新模拟批次B01_运行摘要.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    summary = generate(args.output_root)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
