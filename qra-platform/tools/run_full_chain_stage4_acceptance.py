"""Execute and record the complete synthetic stage-4 acceptance gate."""

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

from db_qra.synthetic_stage4 import (  # noqa: E402
    GATE_NAME,
    SyntheticStage4Workflow,
    read_json,
    write_json,
)

DEFAULT_STAGE3_OUTPUT = (
    PROJECT_ROOT / "workspace" / "outputs" / "m1-5-stage3-raw-to-snapshot-20260901"
)
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "workspace" / "outputs" / "m1-5-stage4-full-calculation-20260901"
)
DEFAULT_RECORD = (
    PROJECT_ROOT
    / "resources"
    / "synthetic"
    / "full-chain-v1"
    / "stage4"
    / "stage4-acceptance.json"
)
DEFAULT_PROFILE = DEFAULT_RECORD.parent / "acceptance-profile.json"


def _check(checks: list[dict[str, Any]], check_id: str, passed: bool, detail: Any) -> None:
    checks.append(
        {"check_id": check_id, "status": "PASS" if passed else "FAIL", "detail": detail}
    )


def run_acceptance(
    *,
    stage3_snapshot_path: Path,
    database_path: Path,
    output_root: Path,
    record_path: Path,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    profile = read_json(DEFAULT_PROFILE)
    result = SyntheticStage4Workflow(PROJECT_ROOT).run(
        stage3_snapshot_path=stage3_snapshot_path,
        database_path=database_path,
        output_root=output_root,
        snapshot_id=snapshot_id,
    )
    summary = result["summary"]
    binding = result["job_binding"]
    plan = result["capability_plan"]
    diff = result["diff_report"]
    replay = result["replay_report"]
    conservation = result["conservation_report"]
    lineage = result["lineage_report"]
    d20 = result["d20_report"]
    database_run = result["database_run"]
    direct_manifest = result["direct_manifest"]

    checks: list[dict[str, Any]] = []
    _check(checks, "STAGE4_WORKFLOW_PASS", summary["status"] == "PASS", summary)
    _check(
        checks,
        "IMMUTABLE_SNAPSHOT_HASH_BOUND",
        summary["snapshot_sha256"] == profile["expected_qra_input_sha256"],
        {
            "snapshot_id": summary["snapshot_id"],
            "snapshot_sha256": summary["snapshot_sha256"],
        },
    )
    _check(
        checks,
        "SYSTEM_PARAMETER_PACKS_BOUND",
        len(binding["parameter_pack_bindings"]) == 6
        and binding["all_parameter_packs_verified"],
        binding["parameter_pack_bindings"],
    )
    _check(
        checks,
        "RUN_ASSUMPTION_BOUND",
        binding["run_assumption_verified"]
        and binding["run_assumption_binding"] == "run-assumption:S00_BASELINE-v1",
        {
            "binding": binding["run_assumption_binding"],
            "business_content_sha256": binding["run_assumption_business_sha256"],
        },
    )
    _check(
        checks,
        "CAPABILITY_11_RUNNABLE",
        len(plan["runnable_node_ids"]) == profile["expected_completed_node_count"]
        and not plan["skipped_node_ids"],
        {
            "runnable": plan["runnable_node_ids"],
            "skipped": plan["skipped_node_ids"],
        },
    )
    _check(
        checks,
        "CALCULATION_JOB_AUTO_CREATED",
        bool(summary["calculation_run_id"]) and database_run["status"] == "COMPLETED",
        {
            "run_id": summary["calculation_run_id"],
            "status": database_run["status"],
        },
    )
    _check(
        checks,
        "S00_11_OF_11_COMPLETED",
        summary["completed_node_count"] == profile["expected_completed_node_count"],
        summary["completed_node_count"],
    )
    _check(
        checks,
        "S00_FAILURES_ZERO_SKIPS_ZERO",
        summary["failed_node_count"] == profile["expected_failed_node_count"]
        and summary["skipped_node_count"] == profile["expected_skipped_node_count"],
        {
            "failed": summary["failed_node_count"],
            "skipped": summary["skipped_node_count"],
        },
    )
    _check(
        checks,
        "RAW_SOURCE_CHAIN_EQUALS_DIRECT_JSON",
        all(row["source_chain_equals_direct_json"] for row in diff["node_diffs"]),
        {"node_count": len(diff["node_diffs"]), "mismatch_count": diff["mismatch_count"]},
    )
    _check(
        checks,
        "ALL_NODES_EQUAL_GOLDEN",
        diff["status"] == "PASS" and diff["mismatch_count"] == 0,
        {"status": diff["status"], "mismatch_count": diff["mismatch_count"]},
    )
    _check(
        checks,
        "S00_CONSISTENCY_METRICS_EQUAL_BASELINE",
        diff["metrics_equal"],
        {"actual": diff["actual_metrics"], "expected": diff["expected_metrics"]},
    )
    _check(
        checks,
        "FREQUENCY_PROBABILITY_BRANCH_CONSERVATION",
        conservation["status"] == "PASS",
        conservation,
    )
    _check(
        checks,
        "DETERMINISTIC_NUMERICAL_HASH",
        replay["status"] == "PASS"
        and replay["expected_numerical_result_sha256"]
        == profile["expected_numerical_result_sha256"],
        replay,
    )
    _check(
        checks,
        "BASELINE_HASH_OR_VERSION_CHANGE_LOG",
        direct_manifest["numerical_result_sha256"]
        == profile["expected_numerical_result_sha256"],
        {
            "actual": direct_manifest["numerical_result_sha256"],
            "expected": profile["expected_numerical_result_sha256"],
            "version_change_log_required": True,
            "version_change_record": profile["baseline_change_record"],
        },
    )
    _check(
        checks,
        "ALL_NODES_REVERSE_TRACEABLE",
        lineage["status"] == "PASS"
        and len(lineage["node_lineage"]) == profile["expected_completed_node_count"],
        {"status": lineage["status"], "node_count": len(lineage["node_lineage"])},
    )
    _check(
        checks,
        "D20_BLOCKED_NODES_NOT_COMPLETED",
        d20["status"] == "PASS"
        and bool(d20["blocked_node_ids"])
        and d20["blocked_nodes_not_completed"],
        d20,
    )
    _check(
        checks,
        "D20_MISSING_NOT_ZERO_WITH_FILL_LIST",
        not d20["missing_values_coerced_to_zero"]
        and any(
            row["path"] == profile["missing_required_path"]
            and row["value"] is None
            and row["state"] == "MISSING_NOT_ZERO"
            for row in d20["fill_data_list"]
        ),
        d20["fill_data_list"],
    )
    _check(
        checks,
        "FORMAL_REPORT_GATE_CLOSED",
        not summary["formal_report_allowed"]
        and not binding["formal_report_allowed"]
        and not database_run["summary"]["formal_acceptance_judgement_allowed"]
        and not d20["formal_report_allowed"],
        {
            "summary": summary["formal_report_allowed"],
            "database": database_run["summary"]["formal_acceptance_judgement_allowed"],
            "D20": d20["formal_report_allowed"],
        },
    )

    passed = all(item["status"] == "PASS" for item in checks)
    record = {
        "schema_version": "1.0.0",
        "stage": 4,
        "gate": GATE_NAME,
        "status": "PASS" if passed else "FAIL",
        "checked_at": "2026-09-01T00:00:00+08:00",
        "check_count": len(checks),
        "passed_count": sum(item["status"] == "PASS" for item in checks),
        "checks": checks,
        "snapshot_id": summary["snapshot_id"],
        "calculation_run_id": summary["calculation_run_id"],
        "qra_input_sha256": summary["snapshot_sha256"],
        "numerical_result_sha256": summary["numerical_result_sha256"],
        "completed_node_count": summary["completed_node_count"],
        "failed_node_count": summary["failed_node_count"],
        "skipped_node_count": summary["skipped_node_count"],
        "formal_report_allowed": False,
        "output_root": str(output_root),
        "artifacts": {
            "job_binding": "calculation-job-binding.json",
            "result_diff": "result-diff-report.json",
            "conservation": "conservation-report.json",
            "deterministic_rerun": "deterministic-rerun-record.json",
            "reverse_provenance": "reverse-provenance-record.json",
            "standard_formula_path": "standard-formula-path.json",
            "D20_report": "D20-missing-data-report.json",
            "source_chain_results": "source-chain-db-run/",
            "direct_json_results": "direct-json-run/",
        },
    }
    write_json(output_root / "stage4-acceptance.json", record)
    write_json(record_path, record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage3-snapshot",
        type=Path,
        default=DEFAULT_STAGE3_OUTPUT / "D00_CLEAN" / "snapshot.json",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_STAGE3_OUTPUT / "stage3-snapshots.sqlite3",
    )
    parser.add_argument("--snapshot-id")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--record", type=Path, default=DEFAULT_RECORD)
    args = parser.parse_args()
    record = run_acceptance(
        stage3_snapshot_path=args.stage3_snapshot.resolve(),
        database_path=args.database.resolve(),
        output_root=args.output_root.resolve(),
        record_path=args.record.resolve(),
        snapshot_id=args.snapshot_id,
    )
    print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
