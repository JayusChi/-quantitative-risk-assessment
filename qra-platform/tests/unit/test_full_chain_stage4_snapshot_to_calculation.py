from __future__ import annotations

import json
from pathlib import Path

from db_qra.synthetic_stage4 import (
    EXPECTED_NODE_COUNT,
    GATE_NAME,
    build_conservation_report,
)
from qra_engine.dynamic import plan_dynamic_flow

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_ROOT = PROJECT_ROOT / "resources" / "synthetic" / "full-chain-v1"
STAGE4_ROOT = SYNTHETIC_ROOT / "stage4"
D00_ROOT = SYNTHETIC_ROOT / "stage2" / "generated" / "S00_BASELINE_D00_CLEAN"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def acceptance_check(check_id: str) -> dict:
    record = read_json(STAGE4_ROOT / "stage4-acceptance.json")
    return next(row for row in record["checks"] if row["check_id"] == check_id)


def expected_case() -> dict:
    return read_json(D00_ROOT / "golden" / "expected-snapshot.json")["qra_input"]


def expected_nodes() -> dict[str, dict]:
    root = D00_ROOT / "golden" / "expected-results" / "nodes"
    return {path.stem: read_json(path) for path in sorted(root.glob("*.json"))}


def test_stage4_gate_record_passes_all_checks() -> None:
    record = read_json(STAGE4_ROOT / "stage4-acceptance.json")
    assert record["gate"] == GATE_NAME
    assert record["status"] == "PASS"
    assert record["check_count"] == 18
    assert record["passed_count"] == 18
    assert all(row["status"] == "PASS" for row in record["checks"])
    assert record["completed_node_count"] == EXPECTED_NODE_COUNT
    assert record["failed_node_count"] == 0
    assert record["skipped_node_count"] == 0
    assert record["formal_report_allowed"] is False


def test_confirmed_snapshot_has_11_of_11_runnable_capability() -> None:
    plan = plan_dynamic_flow(expected_case())
    assert len(plan["runnable_node_ids"]) == EXPECTED_NODE_COUNT
    assert plan["skipped_node_ids"] == []
    assert [row["status"] for row in plan["plan"]] == ["RUNNABLE"] * EXPECTED_NODE_COUNT


def test_job_binds_six_parameter_packs_and_run_assumption() -> None:
    packs = acceptance_check("SYSTEM_PARAMETER_PACKS_BOUND")
    assumption = acceptance_check("RUN_ASSUMPTION_BOUND")
    assert packs["status"] == "PASS"
    assert len(packs["detail"]) == 6
    assert all(row["verified"] for row in packs["detail"])
    assert assumption["detail"]["binding"] == "run-assumption:S00_BASELINE-v1"


def test_source_chain_direct_json_and_golden_are_numerically_equal() -> None:
    paths = acceptance_check("RAW_SOURCE_CHAIN_EQUALS_DIRECT_JSON")
    golden = acceptance_check("ALL_NODES_EQUAL_GOLDEN")
    replay = acceptance_check("DETERMINISTIC_NUMERICAL_HASH")
    assert paths["status"] == "PASS"
    assert paths["detail"]["node_count"] == EXPECTED_NODE_COUNT
    assert paths["detail"]["mismatch_count"] == 0
    assert golden["detail"] == {"mismatch_count": 0, "status": "PASS"}
    assert replay["detail"]["source_chain_numerical_result_sha256"] == replay["detail"][
        "direct_json_rerun_numerical_result_sha256"
    ]
    assert replay["detail"]["direct_json_rerun_numerical_result_sha256"] == (
        "2d351acfc98cb73df38e4221e8baf427edd5fdd4b6788992d8c5480246d25286"
    )


def test_s00_metrics_and_conservation_equal_baseline() -> None:
    consistency = acceptance_check("S00_CONSISTENCY_METRICS_EQUAL_BASELINE")
    assert consistency["status"] == "PASS"
    actual = consistency["detail"]["actual"]
    assert actual["segment_count"] == 20
    assert actual["top_risk_segment_id"] == "SEG-012"
    assert actual["maximum_individual_risk_receptor_id"] == "POP-002"
    assert len(actual["fn_curve"]) == 6

    report = build_conservation_report(expected_case(), expected_nodes())
    assert report["status"] == "PASS"
    assert max(abs(value) for value in report["errors"].values()) <= report["tolerance"]
    assert report["fn_curve_cumulative_frequency_non_increasing"] is True


def test_every_node_has_reverse_provenance() -> None:
    check = acceptance_check("ALL_NODES_REVERSE_TRACEABLE")
    assert check["status"] == "PASS"
    assert check["detail"]["node_count"] == EXPECTED_NODE_COUNT


def test_d20_skips_blocked_node_without_coercing_missing_data_to_zero() -> None:
    blocked = acceptance_check("D20_BLOCKED_NODES_NOT_COMPLETED")["detail"]
    missing = acceptance_check("D20_MISSING_NOT_ZERO_WITH_FILL_LIST")["detail"]
    assert blocked["status"] == "PASS"
    assert "human_qra" in blocked["blocked_node_ids"]
    assert blocked["blocked_nodes_not_completed"] is True
    assert blocked["persisted_as_snapshot"] is False
    assert blocked["formal_report_allowed"] is False
    assert missing == [
        {
            "label_zh": missing[0]["label_zh"],
            "path": "population_cells",
            "state": "MISSING_NOT_ZERO",
            "value": None,
        }
    ]


def test_formal_report_gate_stays_closed() -> None:
    check = acceptance_check("FORMAL_REPORT_GATE_CLOSED")
    assert check["status"] == "PASS"
    assert check["detail"] == {"D20": False, "database": False, "summary": False}
