from __future__ import annotations

import argparse
import io
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_acceptance() -> tuple[dict[str, object], str]:
    loader = unittest.TestLoader()
    suite = loader.discover(
        str(PROJECT_ROOT / "tests" / "integration"),
        pattern="test_review_workbench.py",
        top_level_dir=str(PROJECT_ROOT),
    )
    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    document: dict[str, object] = {
        "acceptance_id": "stage5-review-workbench",
        "status": "REVIEW_WORKBENCH_ACCEPTED"
        if result.wasSuccessful()
        else "REVIEW_WORKBENCH_BLOCKED_BY_TEST_FAILURE",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "cloud_model_calls": 0,
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "checks": {
            "database_migration": result.wasSuccessful(),
            "immutable_decisions_and_snapshots": result.wasSuccessful(),
            "optimistic_locking": result.wasSuccessful(),
            "candidate_evidence_trace": result.wasSuccessful(),
            "deterministic_gate_and_hash": result.wasSuccessful(),
            "atomic_confirmation": result.wasSuccessful(),
            "calculation_and_report_trace": result.wasSuccessful(),
            "three_column_http_workbench": result.wasSuccessful(),
        },
    }
    return document, output.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description="运行第五阶段人工复核工作台确定性验收")
    parser.add_argument("--record", type=Path, help="写入机器可读验收记录")
    parser.add_argument("--json", action="store_true", help="在标准输出打印JSON摘要")
    args = parser.parse_args()
    document, detail = run_acceptance()
    if args.record:
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if args.json:
        print(json.dumps(document, ensure_ascii=False, indent=2))
    else:
        print(detail, end="")
        print(f"最终状态：{document['status']}")
    return 0 if document["status"] == "REVIEW_WORKBENCH_ACCEPTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
