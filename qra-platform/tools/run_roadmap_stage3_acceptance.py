"""Run deterministic Roadmap Stage 3 P0 gates and optional authorized live OCR."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "roadmap_stage3_golden"
DEFAULT_OCR_SETTINGS = PROJECT_ROOT / "workspace" / "state" / "ocr-settings.json"

TEST_MODULES = (
    "tests.unit.converter.parsing.test_aliyun_bailian",
    "tests.unit.converter.parsing.test_roadmap_stage3_ocr",
    "tests.unit.test_stage3_robustness_evaluator",
    "tests.integration.test_roadmap_stage3_reextraction",
)


def _suite() -> unittest.TestResult:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(loader.loadTestsFromName(name) for name in TEST_MODULES)
    return unittest.TextTestRunner(verbosity=2, stream=sys.stderr).run(suite)


def _write_record(path: Path, record: dict[str, Any]) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def _load_local_ocr_settings(settings_path: Path) -> dict[str, object]:
    """Load the current-user encrypted settings without returning secrets."""

    from db_qra.ocr_settings import (
        OcrSettingsStore,
        environment_ocr_configured,
        load_ocr_settings_into_process,
    )

    if environment_ocr_configured():
        return {
            "configured": True,
            "source": "process-environment",
            "protection": "PROCESS_ENVIRONMENT_ONLY",
        }
    try:
        settings = load_ocr_settings_into_process(OcrSettingsStore(settings_path))
    except (OSError, RuntimeError, ValueError):
        return {
            "configured": False,
            "source": "encrypted-store",
            "protection": "WINDOWS_DPAPI_CURRENT_USER",
            "reimport_required": True,
        }
    configured = settings is not None and environment_ocr_configured()
    return {
        "configured": configured,
        "source": "encrypted-store" if settings is not None else "none",
        "protection": "WINDOWS_DPAPI_CURRENT_USER" if settings is not None else None,
    }


def _load_csv_ocr_settings(config_path: Path) -> dict[str, object]:
    """Load an authorized provider export into this process without persisting it."""

    from db_qra.ocr_settings import apply_ocr_settings, parse_bailian_config_csv

    try:
        text_value = config_path.read_text(encoding="utf-8-sig")
        settings = parse_bailian_config_csv(text_value)
        apply_ocr_settings(settings, source="acceptance-csv")
    except (OSError, UnicodeError, ValueError):
        return {
            "configured": False,
            "source": "authorized-csv",
            "protection": "PROCESS_MEMORY_ONLY",
            "reimport_required": True,
        }
    return {
        "configured": True,
        "source": "authorized-csv",
        "protection": "PROCESS_MEMORY_ONLY",
    }


def _live_smoke(path: Path) -> dict[str, Any]:
    from qra_converter.parsing.pipeline import (
        ParsingPipeline,
        configured_ocr_provider,
        real_ocr_configured,
    )

    if not real_ocr_configured():
        return {
            "status": "NOT_RUN",
            "problem_code": "ACCEPTANCE.REAL_OCR_CONFIGURATION_MISSING",
        }
    if not path.is_file():
        return {
            "status": "NOT_RUN",
            "problem_code": "ACCEPTANCE.REAL_OCR_INPUT_MISSING",
        }
    audit: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as temporary:
        execution = ParsingPipeline(
            output_root=Path(temporary) / "output",
            cache_root=None,
            ocr_provider=configured_ocr_provider(),
            audit_callback=audit.append,
            job_id="ROADMAP-STAGE3-LIVE-SMOKE",
        ).parse_path(path)
    text_block_count = sum(len(page.text_blocks) for page in execution.document.pages)
    table_count = len(execution.document.tables)
    terminal_calls = [
        row
        for row in audit
        if row.get("status") in {"COMPLETED", "FAILED", "SKIPPED", "CACHED"}
    ]
    failed_calls = [
        row for row in terminal_calls if row.get("status") in {"FAILED", "SKIPPED"}
    ]
    completed_calls = [row for row in terminal_calls if row.get("status") == "COMPLETED"]
    passed = (
        execution.succeeded
        and text_block_count > 0
        and bool(completed_calls)
        and not failed_calls
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "provider_id": configured_ocr_provider().provider_id,
        "model_version": configured_ocr_provider().model_version,
        "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "page_count": execution.document.page_count,
        "text_block_count": text_block_count,
        "table_count": table_count,
        "model_call_count": len(terminal_calls),
        "completed_model_call_count": len(completed_calls),
        "failed_model_call_count": len(failed_calls),
        "issue_codes": sorted({issue.code for issue in execution.document.issues}),
        "parse_sha256": execution.document.parse_sha256,
        "secret_or_source_content_recorded": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="执行Roadmap Stage 3确定性P0验收")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--record", type=Path)
    parser.add_argument("--require-real-ocr", action="store_true")
    parser.add_argument("--real-input", type=Path)
    parser.add_argument(
        "--ocr-settings",
        type=Path,
        default=DEFAULT_OCR_SETTINGS,
        help="本机 Windows DPAPI 加密 OCR 设置文件",
    )
    parser.add_argument(
        "--config-csv",
        type=Path,
        help="已授权的百炼业务空间 CSV；仅加载到当前进程，不持久化",
    )
    arguments = parser.parse_args()
    for path in (SOURCE_ROOT, PROJECT_ROOT, PROJECT_ROOT / "tools"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))

    result = _suite()
    from evaluate_stage3_robustness import evaluate_files

    synthetic = evaluate_files(
        FIXTURES / "manifest.jsonl",
        FIXTURES / "annotations.jsonl",
        FIXTURES / "results.jsonl",
    )
    deterministic_passed = result.wasSuccessful() and bool(synthetic["passed"])
    configuration: dict[str, object] | None = None
    if arguments.require_real_ocr:
        configuration = (
            _load_csv_ocr_settings(arguments.config_csv)
            if arguments.config_csv is not None
            else _load_local_ocr_settings(arguments.ocr_settings)
        )
        real_ocr = (
            _live_smoke(arguments.real_input)
            if arguments.real_input is not None
            else {
                "status": "NOT_RUN",
                "problem_code": "ACCEPTANCE.REAL_OCR_INPUT_REQUIRED",
            }
        )
    else:
        real_ocr = {
            "status": "NOT_RUN",
            "problem_code": "ACCEPTANCE.REAL_OCR_NOT_REQUESTED",
        }
    real_passed = real_ocr.get("status") == "PASS"
    passed = deterministic_passed and (
        real_passed if arguments.require_real_ocr else True
    )
    record: dict[str, Any] = {
        "acceptance_id": "qra.roadmap-stage3-robustness/1.0.0",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "deterministic_suite": "PASS" if deterministic_passed else "FAIL",
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "p0_gates": {
            "final_serialized_request_budget": "PASS" if deterministic_passed else "FAIL",
            "large_image_adaptation": "PASS" if deterministic_passed else "FAIL",
            "long_image_tiling_and_coordinates": "PASS" if deterministic_passed else "FAIL",
            "overlap_merge": "PASS" if deterministic_passed else "FAIL",
            "oversize_error_classification": "PASS" if deterministic_passed else "FAIL",
            "table_task_cell_contract": "PASS" if deterministic_passed else "FAIL",
            "versioned_scoped_reextraction": "PASS" if deterministic_passed else "FAIL",
            "synthetic_golden_tool_contract": "PASS" if synthetic["passed"] else "FAIL",
            "real_bailian_smoke": real_ocr["status"],
        },
        "synthetic_golden": {
            "document_counts": synthetic["document_counts"],
            "metrics": synthetic["metrics"],
            "synthetic_only": True,
        },
        "real_ocr": real_ocr,
        "real_ocr_configuration": configuration,
        "passed_for_requested_mode": passed,
    }
    if arguments.record:
        _write_record(arguments.record, record)
    if arguments.json:
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            f"Roadmap Stage 3工程验收：{'PASS' if deterministic_passed else 'FAIL'}；"
            f"测试{result.testsRun}项；真实百炼{real_ocr['status']}"
        )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
