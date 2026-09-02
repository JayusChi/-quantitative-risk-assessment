"""Run the M1.5 stage-6 controlled-report acceptance gate."""

from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from db_qra.controlled_reporting import (  # noqa: E402
    SYNTHETIC_WATERMARK,
    ControlledReportService,
    build_controlled_report_zip,
    build_deterministic_draft,
    validate_controlled_report,
)
from db_qra.database import QraDatabase, bytes_sha256, json_sha256  # noqa: E402
from db_qra.engine_adapter import execute_run  # noqa: E402
from db_qra.project_service import ProjectService  # noqa: E402

GATE_NAME = "S6_CONTROLLED_REPORT_AGENT_ACCEPTED"
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "workspace" / "outputs" / "m1-5-stage6-controlled-report-20260901"
)
DEFAULT_RECORD = (
    PROJECT_ROOT
    / "resources"
    / "synthetic"
    / "full-chain-v1"
    / "stage6"
    / "stage6-acceptance.json"
)


class UnavailableProvider:
    provider_id = "stage6-unavailable-provider"

    def generate(self, *, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("synthetic provider unavailable")


class StructuredFixtureProvider:
    provider_id = "stage6-structured-fixture-provider"

    def generate(self, *, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        draft = build_deterministic_draft(context)
        draft["generation_mode"] = "MODEL_STRUCTURED"
        draft["provider_id"] = self.provider_id
        draft["sections"][0]["paragraphs"][0]["text_template"] += (
            " 文字表达可以变化，受控引用目标保持不变。"
        )
        return draft


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def add_check(
    checks: list[dict[str, Any]],
    check_id: str,
    passed: bool,
    detail: Any,
) -> None:
    checks.append(
        {
            "check_id": check_id,
            "status": "PASS" if passed else "FAIL",
            "detail": detail,
        }
    )


def validation_status(validation: dict[str, Any], check_id: str) -> str | None:
    row = next(
        (item for item in validation.get("checks", []) if item.get("check_id") == check_id),
        None,
    )
    return None if row is None else str(row.get("status"))


def run_acceptance(*, output_root: Path, record_path: Path) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    database_path = output_root / "stage6-controlled-report.sqlite3"
    if database_path.exists():
        database_path.unlink()
    database = QraDatabase(database_path)
    database.initialize()
    project_result = ProjectService(database).create_demo(actor="stage6-acceptance")
    project = project_result["project"]
    execute_run(
        database,
        str(project_result["run_id"]),
        str(project["advanced_audit"]["snapshot_id"]),
        generate_charts=True,
        runtime_root=output_root / "runtime",
    )
    project = ProjectService(database).get(str(project["id"]))
    service = ControlledReportService(database)

    fallback = service.generate(
        str(project["id"]),
        provider=UnavailableProvider(),
        actor="stage6-acceptance",
    )
    confirmed = service.confirm(
        str(fallback.report["id"]),
        reviewer="stage6-reviewer",
        reason="已核对数字、引用、水印和合成数据使用边界",
    )
    model = service.generate(
        str(project["id"]),
        provider=StructuredFixtureProvider(),
        actor="stage6-acceptance",
    )

    prohibited_draft = deepcopy(model.draft)
    prohibited_draft["sections"][1]["paragraphs"][0]["text_template"] += " 风险可接受。"
    prohibited_validation = validate_controlled_report(model.context, prohibited_draft)

    node_context = deepcopy(model.context)
    for row in node_context["nodes"]:
        if row["node_id"] == "human_qra":
            row["status"] = "SKIPPED_MISSING_INPUT"
    for row in node_context["result_index"]:
        if row["result_ref"] == "RESULT.human_qra":
            row["status"] = "SKIPPED_MISSING_INPUT"
    node_draft = deepcopy(model.draft)
    node_draft["sections"][6]["paragraphs"][0]["text_template"] += " 该结果节点已完成。"
    node_validation = validate_controlled_report(node_context, node_draft)

    unbound_draft = deepcopy(model.draft)
    unbound_paragraph = unbound_draft["sections"][12]["paragraphs"][0]
    unbound_paragraph["text_template"] = "新增项目事实没有受控来源。"
    unbound_paragraph["metric_refs"] = []
    unbound_paragraph["evidence_refs"] = []
    unbound_paragraph["result_refs"] = []
    unbound_paragraph["uncertainty_refs"] = []
    unbound_validation = validate_controlled_report(model.context, unbound_draft)

    immutable = False
    try:
        with database.transaction() as connection:
            connection.execute(
                "UPDATE controlled_report SET context_json = '{}' WHERE id = ?",
                (fallback.report["id"],),
            )
    except Exception:
        immutable = True

    bundle = build_controlled_report_zip(database, str(fallback.report["id"]))
    with zipfile.ZipFile(io.BytesIO(bundle), "r") as archive:
        bundle_names = set(archive.namelist())
        bundle_manifest = json.loads(archive.read("bundle-manifest.json"))
    with zipfile.ZipFile(io.BytesIO(fallback.docx), "r") as archive:
        docx_xml = b"".join(
            archive.read(name)
            for name in archive.namelist()
            if name.startswith("word/") and name.endswith(".xml")
        ).decode("utf-8", errors="ignore")

    output_files = {
        "html": output_root / "QRA全合成受控测试报告_v1.html",
        "pdf": output_root / "QRA全合成受控测试报告_v1.pdf",
        "docx": output_root / "QRA全合成受控测试报告_v1.docx",
        "zip": output_root / "QRA全合成受控测试报告_v1.zip",
        "context": output_root / "report-context-v1.json",
        "draft": output_root / "report-draft-v1.json",
        "validation": output_root / "report-validation-v1.json",
    }
    output_files["html"].write_bytes(fallback.html)
    output_files["pdf"].write_bytes(fallback.pdf)
    output_files["docx"].write_bytes(fallback.docx)
    output_files["zip"].write_bytes(bundle)
    write_json(output_files["context"], fallback.context)
    write_json(output_files["draft"], fallback.draft)
    write_json(output_files["validation"], fallback.validation)
    chart_root = output_root / "charts"
    chart_root.mkdir(parents=True, exist_ok=True)
    for chart_id, content in fallback.charts.items():
        (chart_root / f"{chart_id}.png").write_bytes(content)

    checks: list[dict[str, Any]] = []
    add_check(
        checks,
        "S6-01_REPORT_CONTEXT_V1",
        fallback.context["schema_version"] == "report-context-v1"
        and fallback.validation["status"] == "PASS"
        and len(fallback.context["nodes"]) == 11
        and len(fallback.context["metrics"]) >= 10,
        {
            "context_id": fallback.context["context_id"],
            "metric_count": len(fallback.context["metrics"]),
            "node_count": len(fallback.context["nodes"]),
        },
    )
    add_check(
        checks,
        "S6-02_REPORT_DRAFT_V1",
        fallback.draft["schema_version"] == "report-draft-v1"
        and len(fallback.draft["sections"]) == 15
        and validation_status(fallback.validation, "SECTION_TOPOLOGY") == "PASS",
        {
            "section_count": len(fallback.draft["sections"]),
            "prompt_version": fallback.draft["prompt_version"],
        },
    )
    add_check(
        checks,
        "S6-03_DETERMINISTIC_CONTEXT_TRANSFORM",
        fallback.report["context_sha256"] == model.report["context_sha256"]
        and fallback.context["metrics"] == model.context["metrics"]
        and fallback.context["chart_resources"] == model.context["chart_resources"],
        {
            "context_sha256": fallback.report["context_sha256"],
            "metric_count": len(fallback.context["metrics"]),
        },
    )
    add_check(
        checks,
        "S6-04_STRUCTURED_MODEL_OUTPUT",
        model.draft["generation_mode"] == "MODEL_STRUCTURED"
        and model.draft["provider_id"] == StructuredFixtureProvider.provider_id
        and model.validation["status"] == "PASS"
        and model.report["draft_sha256"] != fallback.report["draft_sha256"],
        {
            "provider_id": model.draft["provider_id"],
            "draft_sha256": model.report["draft_sha256"],
        },
    )
    add_check(
        checks,
        "S6-05_NUMERIC_REFERENCE_CHECK",
        validation_status(fallback.validation, "NUMERIC_REFERENCES") == "PASS"
        and validation_status(fallback.validation, "RENDERED_NUMERIC_AND_WATERMARK")
        == "PASS"
        and b"{{metric:" not in fallback.html,
        {
            "metric_reference_count": len(
                fallback.validation["reference_targets"]["metrics"]
            ),
            "render_check": validation_status(
                fallback.validation, "RENDERED_NUMERIC_AND_WATERMARK"
            ),
        },
    )
    add_check(
        checks,
        "S6-06_EVIDENCE_REFERENCE_CHECK",
        validation_status(fallback.validation, "EVIDENCE_REFERENCES") == "PASS"
        and validation_status(fallback.validation, "RESULT_REFERENCES") == "PASS"
        and len(fallback.context["evidence_index"]) >= 1
        and len(fallback.context["result_index"]) == 11,
        {
            "evidence_count": len(fallback.context["evidence_index"]),
            "result_count": len(fallback.context["result_index"]),
        },
    )
    add_check(
        checks,
        "S6-07_NODE_STATUS_CONSISTENCY",
        validation_status(fallback.validation, "NODE_STATUS_CONSISTENCY") == "PASS"
        and validation_status(node_validation, "NODE_STATUS_CONSISTENCY") == "FAIL",
        {
            "positive": validation_status(
                fallback.validation, "NODE_STATUS_CONSISTENCY"
            ),
            "negative": validation_status(node_validation, "NODE_STATUS_CONSISTENCY"),
        },
    )
    add_check(
        checks,
        "S6-08_PROHIBITED_AND_UNSOURCED_CLAIM_CHECK",
        validation_status(fallback.validation, "PROHIBITED_CLAIMS") == "PASS"
        and validation_status(prohibited_validation, "PROHIBITED_CLAIMS") == "FAIL"
        and validation_status(unbound_validation, "KEY_CONCLUSION_REFERENCES") == "FAIL",
        {
            "prohibited_negative": validation_status(
                prohibited_validation, "PROHIBITED_CLAIMS"
            ),
            "unbound_negative": validation_status(
                unbound_validation, "KEY_CONCLUSION_REFERENCES"
            ),
        },
    )
    add_check(
        checks,
        "S6-09_SYNTHETIC_WATERMARK",
        SYNTHETIC_WATERMARK.encode("utf-8") in fallback.html
        and SYNTHETIC_WATERMARK in docx_xml
        and len(fallback.pdf) > 1000,
        {
            "html": True,
            "docx_ooxml": SYNTHETIC_WATERMARK in docx_xml,
            "pdf_byte_count": len(fallback.pdf),
        },
    )
    add_check(
        checks,
        "S6-10_MODEL_UNAVAILABLE_FALLBACK",
        fallback.draft["generation_mode"] == "DETERMINISTIC_TEMPLATE_FALLBACK"
        and fallback.validation.get("generation_fallback", {}).get(
            "requested_provider_id"
        )
        == UnavailableProvider.provider_id,
        fallback.validation.get("generation_fallback"),
    )
    add_check(
        checks,
        "S6-11_HUMAN_CONFIRMATION",
        confirmed["status"] == "CONFIRMED_TEST_ONLY"
        and confirmed["confirmed_by"] == "stage6-reviewer"
        and fallback.context["formal_report_allowed"] is False,
        {
            "status": confirmed["status"],
            "confirmed_by": confirmed["confirmed_by"],
            "formal_report_allowed": fallback.context["formal_report_allowed"],
        },
    )
    reports = database.list_project_reports(str(project["id"]))
    add_check(
        checks,
        "S6-12_VERSION_HASH_AND_IMMUTABILITY",
        len(reports) == 2
        and {int(row["version_no"]) for row in reports} == {1, 2}
        and immutable
        and all(row["context_sha256"] for row in reports)
        and all(row["draft_sha256"] for row in reports),
        {
            "version_count": len(reports),
            "versions": [row["version_no"] for row in reports],
            "payload_immutable": immutable,
        },
    )
    add_check(
        checks,
        "S6-13_HTML_AND_BUNDLE_OUTPUT",
        fallback.html.startswith(b"<!doctype html>")
        and bundle.startswith(b"PK")
        and {
            "report.html",
            "report-context-v1.json",
            "report-draft-v1.json",
            "report-validation-v1.json",
            "bundle-manifest.json",
        }.issubset(bundle_names)
        and bundle_manifest["formal_report_allowed"] is False,
        {
            "html_sha256": bytes_sha256(fallback.html),
            "bundle_sha256": bytes_sha256(bundle),
            "bundle_file_count": len(bundle_names),
        },
    )
    add_check(
        checks,
        "S6-14_PDF_AND_DOCX_OUTPUT",
        fallback.pdf.startswith(b"%PDF")
        and fallback.docx.startswith(b"PK")
        and len(fallback.pdf) > 1000
        and len(fallback.docx) > 1000,
        {
            "pdf_sha256": bytes_sha256(fallback.pdf),
            "pdf_byte_count": len(fallback.pdf),
            "docx_sha256": bytes_sha256(fallback.docx),
            "docx_byte_count": len(fallback.docx),
        },
    )

    passed_count = sum(row["status"] == "PASS" for row in checks)
    record = {
        "schema_version": "1.0.0",
        "stage": 6,
        "gate": GATE_NAME,
        "status": "PASS" if passed_count == len(checks) else "FAIL",
        "checked_at": "2026-09-01T00:00:00+08:00",
        "check_count": len(checks),
        "passed_count": passed_count,
        "checks": checks,
        "project_id": project["id"],
        "snapshot_id": project["advanced_audit"]["snapshot_id"],
        "calculation_run_id": project["calculation"]["id"],
        "controlled_report_id": fallback.report["id"],
        "controlled_report_status": confirmed["status"],
        "context_sha256": fallback.report["context_sha256"],
        "draft_sha256": fallback.report["draft_sha256"],
        "reference_targets_sha256": fallback.validation["reference_targets_sha256"],
        "completed_node_count": project["calculation_progress"]["completed"],
        "formal_report_allowed": False,
        "database": str(database_path),
        "output_root": str(output_root),
        "deliverables": {
            name: {
                "path": str(path),
                "sha256": bytes_sha256(path.read_bytes()),
                "byte_count": path.stat().st_size,
            }
            for name, path in output_files.items()
        },
        "implementation_hash": json_sha256(
            {
                "context": fallback.context,
                "draft": fallback.draft,
                "validation": fallback.validation,
            }
        ),
    }
    write_json(output_root / "stage6-acceptance.json", record)
    write_json(record_path, record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--record", type=Path, default=DEFAULT_RECORD)
    args = parser.parse_args()
    record = run_acceptance(output_root=args.output_root, record_path=args.record)
    print(
        json.dumps(
            {
                "gate": record["gate"],
                "status": record["status"],
                "passed": f"{record['passed_count']}/{record['check_count']}",
                "output_root": record["output_root"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
