from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE6_ROOT = PROJECT_ROOT / "resources" / "synthetic" / "full-chain-v1" / "stage6"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_stage6_controlled_report_gate_record_passes() -> None:
    profile = read_json(STAGE6_ROOT / "acceptance-profile.json")
    record = read_json(STAGE6_ROOT / "stage6-acceptance.json")
    assert record["gate"] == profile["gate"]
    assert record["status"] == "PASS"
    assert record["check_count"] == profile["expected_check_count"]
    assert record["passed_count"] == profile["expected_check_count"]
    assert all(row["status"] == "PASS" for row in record["checks"])
    assert record["completed_node_count"] == profile["expected_node_count"]
    assert record["formal_report_allowed"] is False


def test_stage6_acceptance_covers_every_work_item() -> None:
    record = read_json(STAGE6_ROOT / "stage6-acceptance.json")
    assert {row["check_id"] for row in record["checks"]} == {
        "S6-01_REPORT_CONTEXT_V1",
        "S6-02_REPORT_DRAFT_V1",
        "S6-03_DETERMINISTIC_CONTEXT_TRANSFORM",
        "S6-04_STRUCTURED_MODEL_OUTPUT",
        "S6-05_NUMERIC_REFERENCE_CHECK",
        "S6-06_EVIDENCE_REFERENCE_CHECK",
        "S6-07_NODE_STATUS_CONSISTENCY",
        "S6-08_PROHIBITED_AND_UNSOURCED_CLAIM_CHECK",
        "S6-09_SYNTHETIC_WATERMARK",
        "S6-10_MODEL_UNAVAILABLE_FALLBACK",
        "S6-11_HUMAN_CONFIRMATION",
        "S6-12_VERSION_HASH_AND_IMMUTABILITY",
        "S6-13_HTML_AND_BUNDLE_OUTPUT",
        "S6-14_PDF_AND_DOCX_OUTPUT",
    }


def test_stage6_hashes_and_deliverables_are_recorded() -> None:
    record = read_json(STAGE6_ROOT / "stage6-acceptance.json")
    assert len(record["context_sha256"]) == 64
    assert len(record["draft_sha256"]) == 64
    assert len(record["reference_targets_sha256"]) == 64
    assert set(record["deliverables"]) == {
        "html",
        "pdf",
        "docx",
        "zip",
        "context",
        "draft",
        "validation",
    }
    assert all(len(row["sha256"]) == 64 for row in record["deliverables"].values())
