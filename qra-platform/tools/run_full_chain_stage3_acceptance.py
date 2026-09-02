"""Execute and record the complete synthetic stage-3 acceptance gate."""

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

from db_qra.stage3_adapter import persist_confirmed_snapshot  # noqa: E402
from qra_converter.synthetic_stage3 import (  # noqa: E402
    GATE_NAME,
    SyntheticStage3Workflow,
    verify_snapshot_immutability,
    write_json,
)

DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "workspace" / "outputs" / "m1-5-stage3-raw-to-snapshot-20260901"
)
DEFAULT_RECORD = (
    PROJECT_ROOT / "resources" / "synthetic" / "full-chain-v1" / "stage3" / "stage3-acceptance.json"
)


def _check(checks: list[dict[str, Any]], check_id: str, passed: bool, detail: Any) -> None:
    checks.append(
        {
            "check_id": check_id,
            "status": "PASS" if passed else "FAIL",
            "detail": detail,
        }
    )


def run_acceptance(output_root: Path, record_path: Path) -> dict[str, Any]:
    workflow = SyntheticStage3Workflow(
        PROJECT_ROOT,
        snapshot_persister=persist_confirmed_snapshot,
    )
    database_path = output_root / "stage3-snapshots.sqlite3"
    d00 = workflow.run(
        condition_id="D00_CLEAN",
        confirm=True,
        output_dir=output_root / "D00_CLEAN",
        database_path=database_path,
    )
    d00_replay = workflow.run(condition_id="D00_CLEAN")
    d10 = workflow.run(condition_id="D10_CONFLICT", output_dir=output_root / "D10_CONFLICT")
    d10_resolved = workflow.run(
        condition_id="D10_CONFLICT",
        decisions={"pipeline.operating_pressure_mpa": "OVERLAY"},
        output_dir=output_root / "D10_CONFLICT_RESOLVED",
    )
    d20 = workflow.run(condition_id="D20_MISSING", output_dir=output_root / "D20_MISSING")
    d30 = workflow.run(
        condition_id="D30_LOW_QUALITY_SCAN",
        output_dir=output_root / "D30_LOW_QUALITY_SCAN",
    )
    d40 = workflow.run(
        condition_id="D40_OVERSIZED_IMAGE",
        output_dir=output_root / "D40_OVERSIZED_IMAGE",
    )
    d50 = workflow.run(
        condition_id="D50_PROMPT_INJECTION",
        output_dir=output_root / "D50_PROMPT_INJECTION",
    )
    online = workflow.run(
        condition_id="D00_CLEAN",
        provider_mode="online",
        output_dir=output_root / "ONLINE_DEMO_NO_CREDENTIALS",
    )

    checks: list[dict[str, Any]] = []
    coverage = d00["coverage_report"]
    _check(checks, "D00_GATE", d00["gate"]["status"] == "PASS", d00["gate"])
    _check(
        checks,
        "D00_CRITICAL_PRECISION_100",
        coverage["critical_precision"] == 1.0,
        coverage["critical_precision"],
    )
    _check(checks, "D00_RECALL_100", coverage["recall"] == 1.0, coverage["recall"])
    _check(
        checks,
        "D00_EVIDENCE_BINDING_100",
        coverage["evidence_binding_rate"] == 1.0,
        coverage["evidence_binding_rate"],
    )
    _check(
        checks,
        "D00_NO_EVIDENCE_CANDIDATES_ZERO",
        coverage["candidate_without_evidence_count"] == 0,
        coverage["candidate_without_evidence_count"],
    )
    _check(
        checks,
        "D00_BLANK_TO_ZERO_ZERO",
        coverage["blank_to_zero_count"] == 0,
        coverage["blank_to_zero_count"],
    )
    _check(
        checks,
        "D00_UNDECLARED_DEFAULT_ZERO",
        coverage["undeclared_default_count"] == 0,
        coverage["undeclared_default_count"],
    )
    _check(
        checks,
        "D00_PROMPT_INJECTION_CHANGE_ZERO",
        coverage["prompt_injection_change_count"] == 0,
        coverage["prompt_injection_change_count"],
    )
    _check(checks, "D00_SNAPSHOT_EXACT", d00["golden_diff"]["equal"], d00["golden_diff"])
    _check(
        checks,
        "DETERMINISTIC_REPLAY_HASH",
        d00_replay["gate"]["business_replay_hash"] == d00["gate"]["business_replay_hash"],
        d00_replay["gate"]["business_replay_hash"],
    )
    persistence = d00["snapshot_persistence"]
    immutable = verify_snapshot_immutability(database_path, persistence["database_snapshot_id"])
    _check(checks, "IMMUTABLE_SNAPSHOT", immutable, persistence)
    _check(
        checks,
        "D10_CONFLICT_BLOCKS",
        d10["gate"]["status"] == "BLOCKED" and d10["snapshot"] is None,
        d10["gate"],
    )
    _check(
        checks,
        "D10_DECISION_CONTINUES",
        d10_resolved["gate"]["status"] == "PASS"
        and d10_resolved["review_workbench"]["decision_set_sha256"]
        != d10["review_workbench"]["decision_set_sha256"],
        d10_resolved["gate"],
    )
    _check(
        checks,
        "D20_MISSING_BLOCKS_WITHOUT_ZERO",
        d20["gate"]["status"] == "BLOCKED"
        and d20["coverage_report"]["blank_to_zero_count"] == 0
        and "human_qra" in d20["capability"]["blocked_node_ids"],
        d20["capability"],
    )
    _check(
        checks,
        "D30_LOW_CONFIDENCE_REVIEW",
        d30["gate"]["status"] == "BLOCKED"
        and len(d30["parsed_artifacts"]["pdf_preprocessing"]) == 42,
        {"review_count": d30["gate"]["unresolved_review_count"]},
    )
    _check(
        checks,
        "D40_IMAGE_TILING_COORDINATES",
        d40["gate"]["status"] == "PASS" and len(d40["parsed_artifacts"]["image_tiling"]) == 8,
        {"tile_count": len(d40["parsed_artifacts"]["image_tiling"])},
    )
    _check(
        checks,
        "D50_PROMPT_INJECTION_ISOLATED",
        d50["gate"]["status"] == "PASS"
        and bool(d50["security_audit"]["detected_evidence_ids"])
        and d50["coverage_report"]["prompt_injection_change_count"] == 0,
        d50["security_audit"],
    )
    _check(
        checks,
        "ONLINE_FAILURE_PRESERVES_HUMAN_ROUTE",
        online["gate"]["status"] == "BLOCKED"
        and online["online_demo"]["parsed_artifacts_preserved"]
        and online["online_demo"]["human_review_route_available"],
        online["online_demo"],
    )
    passed = all(item["status"] == "PASS" for item in checks)
    record = {
        "schema_version": "1.0.0",
        "stage": 3,
        "gate": GATE_NAME,
        "status": "PASS" if passed else "FAIL",
        "checked_at": "2026-09-02T00:00:00+08:00",
        "check_count": len(checks),
        "passed_count": sum(item["status"] == "PASS" for item in checks),
        "checks": checks,
        "deterministic_metrics": coverage,
        "expected_snapshot_business_sha256": d00["snapshot"]["business_content_sha256"],
        "qra_input_sha256": d00["snapshot"]["qra_input_sha256"],
        "output_root": str(output_root),
        "artifacts": {
            "snapshot": "D00_CLEAN/snapshot.json",
            "golden_diff": "D00_CLEAN/golden-snapshot-diff.json",
            "coverage": "D00_CLEAN/conversion-coverage-report.json",
            "provenance": "D00_CLEAN/snapshot-provenance.json",
            "database": "stage3-snapshots.sqlite3",
        },
    }
    write_json(output_root / "stage3-acceptance.json", record)
    write_json(record_path, record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--record", type=Path, default=DEFAULT_RECORD)
    args = parser.parse_args()
    record = run_acceptance(args.output_root.resolve(), args.record.resolve())
    print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
