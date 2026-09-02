from __future__ import annotations

from pathlib import Path

from tools.run_full_chain_stage6_acceptance import GATE_NAME, run_acceptance


def test_full_chain_stage6_builds_and_confirms_all_report_formats(tmp_path: Path) -> None:
    record = run_acceptance(
        output_root=tmp_path / "stage6-output",
        record_path=tmp_path / "stage6-acceptance.json",
    )
    assert record["gate"] == GATE_NAME
    assert record["status"] == "PASS"
    assert record["passed_count"] == record["check_count"] == 14
    assert record["controlled_report_status"] == "CONFIRMED_TEST_ONLY"
    assert record["formal_report_allowed"] is False
