"""Run deterministic stage-four golden and persistence/API acceptance."""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
GOLDEN_MANIFEST = PROJECT_ROOT / "tests" / "fixtures" / "extraction_stage4" / "golden_manifest.json"

TEST_MODULES = (
    "tests.unit.converter.test_aliyun_bailian_extraction",
    "tests.unit.converter.test_stage4_normalization",
    "tests.unit.converter.test_stage4_extraction",
    "tests.unit.converter.test_stage4_fusion",
    "tests.integration.test_stage4_pipeline",
    "tests.integration.test_stage4_labelled_golden",
)


def _suite() -> unittest.TestResult:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(loader.loadTestsFromName(name) for name in TEST_MODULES)
    return unittest.TextTestRunner(verbosity=2, stream=sys.stderr).run(suite)


def _manifest_summary() -> dict[str, object]:
    manifest = json.loads(GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    cases = list(manifest["cases"])
    layers = sorted({str(item["layer"]) for item in cases})
    return {
        "golden_set_id": manifest["golden_set_id"],
        "case_count": len(cases),
        "layers": layers,
        "case_ids": [str(item["id"]) for item in cases],
    }


def _write_record(path: Path, record: dict[str, object]) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def main() -> int:
    parser = argparse.ArgumentParser(description="执行第四阶段确定性黄金集与API验收")
    parser.add_argument("--record", type=Path, help="写入验收JSON")
    parser.add_argument("--json", action="store_true", help="输出机器可读摘要")
    parser.add_argument(
        "--real-model-record",
        type=Path,
        help="读取由test_bailian_extraction.py生成的真实模型冒烟记录",
    )
    parser.add_argument(
        "--require-real-model",
        action="store_true",
        help="要求真实千问冒烟和带标签黄金指标均通过，否则返回非零",
    )
    arguments = parser.parse_args()
    source_text = str(SOURCE_ROOT)
    if source_text not in sys.path:
        sys.path.insert(0, source_text)
    project_text = str(PROJECT_ROOT)
    if project_text not in sys.path:
        sys.path.insert(0, project_text)
    result = _suite()
    golden = _manifest_summary()
    from tests.integration.test_stage4_labelled_golden import run_labelled_golden

    labelled_metrics = run_labelled_golden()
    framework_passed = result.wasSuccessful() and int(golden["case_count"]) >= 20
    real_model: dict[str, object] | None = None
    if arguments.real_model_record:
        loaded = json.loads(arguments.real_model_record.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("真实模型冒烟记录必须是JSON对象")
        real_model = {
            key: loaded.get(key)
            for key in (
                "executed_at",
                "status",
                "provider_id",
                "model_version",
                "model_call_count",
                "failed_model_call_count",
                "model_candidate_count",
                "evidence_binding_rate",
                "result_sha256",
                "synthetic_input_only",
                "secret_or_endpoint_recorded",
            )
        }
    live_passed = bool(
        real_model
        and real_model.get("status") == "PASS"
        and real_model.get("secret_or_endpoint_recorded") is False
    )
    labelled_accuracy_measured = bool(
        float(labelled_metrics.get("precision") or 0) >= 0.95
        and float(labelled_metrics.get("recall") or 0) >= 0.90
        and float(labelled_metrics.get("evidence_binding_rate") or 0) == 1.0
    )
    formal_passed = framework_passed and live_passed and labelled_accuracy_measured
    record: dict[str, object] = {
        "stage": 4,
        "acceptance_id": "qra.stage4-extraction-fusion/1.0.0",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "deterministic_suite": "PASS" if result.wasSuccessful() else "FAIL",
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "golden": golden,
        "gates": {
            "candidate_contract_validation": "PASS" if framework_passed else "FAIL",
            "evidence_binding": "PASS" if framework_passed else "FAIL",
            "unit_normalization": "PASS" if framework_passed else "FAIL",
            "conflict_detection": "PASS" if framework_passed else "FAIL",
            "prompt_injection_detection": "PASS" if framework_passed else "FAIL",
            "read_only_review_apis": "PASS" if framework_passed else "FAIL",
            "real_qwen_provider_smoke": "PASS" if live_passed else "NOT_RUN",
            "labelled_precision_recall": (
                "PASS" if labelled_accuracy_measured else "FAIL"
            ),
        },
        "framework_acceptance": framework_passed,
        "formal_test_edition_acceptance": formal_passed,
        "live_model_required": True,
        "real_model": real_model,
        "labelled_golden_metrics": labelled_metrics,
        "remaining_gate": (
            None
            if formal_passed
            else "需要对带结构化标签的黄金资料逐项执行并计算精确率/召回率"
        ),
    }
    if arguments.record:
        _write_record(arguments.record, record)
    if arguments.json:
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            f"第四阶段框架验收：{'PASS' if framework_passed else 'FAIL'}；"
            f"测试{result.testsRun}项；黄金场景{golden['case_count']}项"
        )
    passed_for_mode = formal_passed if arguments.require_real_model else framework_passed
    return 0 if passed_for_mode else 1


if __name__ == "__main__":
    raise SystemExit(main())
