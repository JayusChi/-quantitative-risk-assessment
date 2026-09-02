"""Execute and record the stage-5 ordinary-user journey acceptance gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from db_qra.database import QraDatabase  # noqa: E402
from db_qra.engine_adapter import execute_run  # noqa: E402
from db_qra.project_service import ProjectService  # noqa: E402
from db_qra.project_ui import project_workspace_html  # noqa: E402
from db_qra.review_ui import review_workbench_html  # noqa: E402

GATE_NAME = "S5_USER_JOURNEY_ACCEPTED"
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "workspace" / "outputs" / "m1-5-stage5-user-journey-20260901"
)
DEFAULT_RECORD = (
    PROJECT_ROOT
    / "resources"
    / "synthetic"
    / "full-chain-v1"
    / "stage5"
    / "stage5-acceptance.json"
)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def add_check(
    checks: list[dict[str, Any]], check_id: str, passed: bool, detail: Any
) -> None:
    checks.append(
        {"check_id": check_id, "status": "PASS" if passed else "FAIL", "detail": detail}
    )


def run_acceptance(*, output_root: Path, record_path: Path) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    database_path = output_root / "stage5-user-journey.sqlite3"
    if database_path.exists():
        database_path.unlink()
    database = QraDatabase(database_path)
    database.initialize()
    service = ProjectService(database)

    ordinary = service.create(name="阶段5普通用户项目", case_id="S5-ORDINARY")
    demo_result = service.create_demo(actor="stage5-acceptance")
    demo = demo_result["project"]
    execute_run(
        database,
        str(demo_result["run_id"]),
        str(demo["advanced_audit"]["snapshot_id"]),
        generate_charts=False,
        runtime_root=output_root / "runtime",
    )
    demo = service.get(str(demo["id"]))

    retry_run_id = database.create_run(
        str(demo["advanced_audit"]["snapshot_id"]),
        str(demo["advanced_audit"]["snapshot_sha256"]),
        generate_charts=False,
    )
    database.attach_project_run(str(demo["id"]), retry_run_id)
    database.fail_run(retry_run_id, "stage5 controlled retry check")
    retry_state = service.get(str(demo["id"]))
    database.attach_project_run(str(demo["id"]), str(demo["calculation"]["id"]))
    demo = service.get(str(demo["id"]))

    page = project_workspace_html().decode("utf-8")
    review_page = review_workbench_html("STAGE5-CHECK").decode("utf-8")
    checks: list[dict[str, Any]] = []
    add_check(
        checks,
        "S5-01_PROJECT_AGGREGATION",
        all(
            key in demo
            for key in (
                "status",
                "data_completeness_percent",
                "pending_issue_count",
                "calculation_progress",
                "latest_report",
            )
        ),
        {
            "project_count": len(service.list()),
            "ordinary_status": ordinary["status"],
            "demo_status": demo["status"],
        },
    )
    add_check(
        checks,
        "S5-02_FULL_SYNTHETIC_DEMO_LOADER",
        demo["is_demo"]
        and len(demo["sources"]) == 10
        and len(demo["calculation_versions"]["parameter_pack_ids"]) == 6,
        {
            "source_count": len(demo["sources"]),
            "parameter_pack_count": len(
                demo["calculation_versions"]["parameter_pack_ids"]
            ),
        },
    )
    add_check(
        checks,
        "S5-03_SIX_STEP_GUIDED_JOURNEY",
        [row["id"] for row in demo["journey_steps"]]
        == ["UPLOAD", "CONVERT", "REVIEW", "CONFIRM", "CALCULATE", "REPORT"],
        demo["journey_steps"],
    )
    add_check(
        checks,
        "S5-04_STATUS_AND_11_NODE_PROGRESS",
        demo["status"] == "REPORT_READY"
        and demo["calculation_progress"]["completed"] == 11
        and len(demo["nodes"]) == 11,
        {
            "status": demo["status"],
            "progress": demo["calculation_progress"],
        },
    )
    add_check(
        checks,
        "S5-05_ORDINARY_UI_HIDES_JSON_AND_DATABASE",
        'type="file"' in page
        and "上传JSON数据" not in page
        and "数据库视图" not in page
        and "无需准备结构化代码" in page,
        {
            "file_upload": True,
            "visible_json_upload": False,
            "visible_database_operations": False,
        },
    )
    add_check(
        checks,
        "S5-06_ADVANCED_AUDIT_DISCLOSURE",
        "高级审计与技术追溯" in page
        and all(
            demo["advanced_audit"].get(key)
            for key in (
                "project_id",
                "snapshot_id",
                "snapshot_sha256",
                "run_id",
                "input_sha256",
                "result_sha256",
                "engine_version",
            )
        ),
        demo["advanced_audit"],
    )
    add_check(
        checks,
        "S5-07_SYNTHETIC_MARKER_AND_FORMAL_GATE",
        bool(demo["synthetic_warning"])
        and demo["report_center"]["draft"]
        and not demo["calculation"]["summary"][
            "formal_acceptance_judgement_allowed"
        ],
        {
            "warning": demo["synthetic_warning"],
            "manual_status": demo["report_center"]["manual_status"],
        },
    )
    add_check(
        checks,
        "S5-08_RETRY_AND_CONTINUE_STATE",
        retry_state["status"] == "CALCULATION_FAILED"
        and retry_state["next_action"]["id"] == "RETRY_CALCULATION"
        and demo["next_action"]["id"] == "OPEN_REPORT",
        {
            "failed_action": retry_state["next_action"],
            "completed_action": demo["next_action"],
        },
    )
    add_check(
        checks,
        "S5-09_DESKTOP_AND_NARROW_RESPONSIVE",
        "@media(max-width:900px)" in page and "@media(max-width:560px)" in page,
        {"desktop": True, "narrow_breakpoints": [900, 560]},
    )
    add_check(
        checks,
        "S5-10_KEYBOARD_ERRORS_AND_PROJECT_BACKLINK",
        "focus-visible" in page
        and 'class="skip"' in page
        and 'role="alert"' in page
        and "projectBack" in review_page
        and ".top .actions #projectBack" in review_page
        and "criticalityFilter" in review_page,
        {
            "focus_indicator": True,
            "skip_link": True,
            "error_live_region": True,
            "review_project_backlink": True,
        },
    )
    add_check(
        checks,
        "END_TO_END_REPORT_RETURN_NAVIGATION",
        demo["report_center"]["completeness"] == "PASS"
        and demo["report_center"]["numerical_consistency"] == "PASS"
        and demo["report_center"]["citations"] == "BOUND"
        and demo["latest_report"]["html_url"].startswith("/runs/"),
        {
            "report_center": demo["report_center"],
            "report": demo["latest_report"],
        },
    )
    passed = all(row["status"] == "PASS" for row in checks)
    record = {
        "schema_version": "1.0.0",
        "stage": 5,
        "gate": GATE_NAME,
        "status": "PASS" if passed else "FAIL",
        "checked_at": "2026-09-01T00:00:00+08:00",
        "check_count": len(checks),
        "passed_count": sum(row["status"] == "PASS" for row in checks),
        "checks": checks,
        "project_id": demo["id"],
        "snapshot_id": demo["advanced_audit"]["snapshot_id"],
        "calculation_run_id": demo["advanced_audit"]["run_id"],
        "completed_node_count": demo["calculation_progress"]["completed"],
        "formal_report_allowed": False,
        "database": str(database_path),
        "output_root": str(output_root),
    }
    write_json(output_root / "stage5-acceptance.json", record)
    write_json(record_path, record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--record", type=Path, default=DEFAULT_RECORD)
    args = parser.parse_args()
    record = run_acceptance(
        output_root=args.output_root.resolve(), record_path=args.record.resolve()
    )
    print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
