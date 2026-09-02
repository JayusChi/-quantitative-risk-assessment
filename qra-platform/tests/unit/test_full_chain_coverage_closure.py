from __future__ import annotations

import importlib.util
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "tools" / "reconcile_full_chain_coverage.py"
ROOT = PROJECT_ROOT / "resources" / "synthetic" / "full-chain-v1"


def _load_builder():
    spec = importlib.util.spec_from_file_location("coverage_closure", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_coverage_closure_is_current_complete_and_honest() -> None:
    builder = _load_builder()
    actual = json.loads((ROOT / "coverage-gap-closure.json").read_text("utf-8"))
    assert actual == builder.build_closure()
    assert actual["original_gap_field_count"] == 218
    assert actual["ledger_disposition_count"] == 218
    assert actual["contract_field_count"] == 361
    assert actual["claims"]["all_original_gaps_have_current_disposition"] is True
    assert actual["claims"]["all_361_contract_fields_are_evidence_extracted"] is False
    assert actual["claims"]["all_361_contract_fields_have_verified_implementation"] is True
    assert actual["claims"]["project_fact_evidence_or_structural_assembly_complete"] is True
    assert all(row["ledger_status"] == "CLOSED_WITH_DISPOSITION" for row in actual["fields"])
    assert actual["status"] == "IMPLEMENTATION_COMPLETE"
    assert actual["implementation_counts"] == {"IMPLEMENTED": 218}


def test_stage1_register_points_to_the_closure_record() -> None:
    register = json.loads((ROOT / "coverage-gap-register.json").read_text("utf-8"))
    assert register["status"] == "SUPERSEDED_BY_COVERAGE_CLOSURE"
    assert register["closure_record"] == "coverage-gap-closure.json"
