from __future__ import annotations

import json
from pathlib import Path

import pytest

from db_qra.stage3_adapter import persist_confirmed_snapshot
from qra_converter.extraction.ports import ExtractionResponse
from qra_converter.synthetic_stage3 import (
    GATE_NAME,
    SyntheticStage3Workflow,
    verify_snapshot_immutability,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE3_ROOT = PROJECT_ROOT / "resources" / "synthetic" / "full-chain-v1" / "stage3"


@pytest.fixture(scope="module")
def workflow() -> SyntheticStage3Workflow:
    return SyntheticStage3Workflow(
        PROJECT_ROOT,
        snapshot_persister=persist_confirmed_snapshot,
    )


@pytest.fixture(scope="module")
def d00(workflow: SyntheticStage3Workflow) -> dict:
    return workflow.run(condition_id="D00_CLEAN")


def test_stage3_contract_is_versioned_and_covers_every_evidence_field() -> None:
    mapping = json.loads((STAGE3_ROOT / "synthetic-mapping.v1.json").read_text("utf-8"))
    replay = json.loads((STAGE3_ROOT / "deterministic-replay.json").read_text("utf-8"))
    providers = json.loads((STAGE3_ROOT / "provider-configs.v1.json").read_text("utf-8"))

    assert mapping["contract_version"] == "1.0.0"
    assert mapping["field_count"] == 256
    assert len(mapping["fields"]) == 256
    assert all(field["aliases"] for field in mapping["fields"])
    assert all(
        field["normalization"]["blank_policy"] == "MISSING_NEVER_ZERO"
        for field in mapping["fields"]
    )
    assert len(replay["items"]) == 53
    assert replay["binding_policy"] == (
        "source-file-sha256+page-or-image+bbox+crop-pixel-sha256"
    )
    assert providers["deterministic"]["network_allowed"] is False
    assert providers["online_demo"]["tools_allowed"] == []


def test_d00_raw_sources_create_exact_golden_snapshot(d00: dict) -> None:
    coverage = d00["coverage_report"]
    assert d00["gate"] == {
        **d00["gate"],
        "name": GATE_NAME,
        "status": "PASS",
    }
    assert d00["golden_diff"] == {
        "equal": True,
        "difference_count": 0,
        "differences": [],
    }
    assert coverage["candidate_count"] == 256
    assert coverage["critical_precision"] == 1.0
    assert coverage["precision"] == 1.0
    assert coverage["recall"] == 1.0
    assert coverage["evidence_binding_rate"] == 1.0
    assert coverage["candidate_without_evidence_count"] == 0
    assert coverage["blank_to_zero_count"] == 0
    assert coverage["undeclared_default_count"] == 0
    assert coverage["source_format_counts"] == {
        "xlsx_cell": 116,
        "csv_cell": 47,
        "docx_table_cell": 40,
        "pdf_page_bbox": 42,
        "image_bbox": 11,
    }


def test_d00_has_entities_relationships_and_correct_assembly_boundaries(d00: dict) -> None:
    entity_types = {item["entity_type"] for item in d00["entities"]}
    assert {"PIPELINE", "SEGMENT", "POPULATION_CELL", "WEATHER_CASE"} <= entity_types
    assert (
        len([item for item in d00["relationships"] if item["relation_type"] == "BELONGS_TO"]) == 20
    )
    project_bindings = [
        item
        for item in d00["provenance"]["assembly_bindings"]
        if item["source_kind"] == "PROJECT_FACT"
    ]
    parameter_bindings = [
        item
        for item in d00["provenance"]["assembly_bindings"]
        if item["source_kind"] == "MODEL_PARAMETER"
    ]
    assert project_bindings
    assert all(item["evidence_ids"] for item in project_bindings)
    assert len(parameter_bindings) == 68
    assert all(item["evidence_ids"] == [] for item in parameter_bindings)
    assert all(item["parameter_pack_id"].startswith("synthetic-") for item in parameter_bindings)


def test_deterministic_replay_business_hash_is_stable(
    workflow: SyntheticStage3Workflow, d00: dict
) -> None:
    second = workflow.run(condition_id="D00_CLEAN")
    assert (
        second["snapshot"]["business_content_sha256"] == d00["snapshot"]["business_content_sha256"]
    )
    assert second["gate"]["business_replay_hash"] == d00["gate"]["business_replay_hash"]
    assert (
        second["review_workbench"]["decision_set_sha256"]
        == d00["review_workbench"]["decision_set_sha256"]
    )


def test_confirmation_is_the_only_snapshot_write_and_storage_is_immutable(
    workflow: SyntheticStage3Workflow, tmp_path: Path
) -> None:
    output = tmp_path / "confirmed"
    database = tmp_path / "stage3.sqlite3"
    preview = workflow.run(condition_id="D00_CLEAN", output_dir=tmp_path / "preview")
    assert preview["snapshot_persistence"] is None
    assert not database.exists()

    confirmed = workflow.run(
        condition_id="D00_CLEAN",
        confirm=True,
        output_dir=output,
        database_path=database,
    )
    persistence = confirmed["snapshot_persistence"]
    assert persistence["created"] is True
    assert persistence["payload_sha256"] == confirmed["snapshot"]["qra_input_sha256"]
    assert verify_snapshot_immutability(database, persistence["database_snapshot_id"])
    assert json.loads((output / "snapshot.json").read_text("utf-8")) == confirmed["snapshot"]
    provenance = json.loads((output / "snapshot-provenance.json").read_text("utf-8"))
    assert len(provenance["candidate_ids"]) == 256
    assert len(provenance["evidence_ids"]) == 256


def test_d10_conflict_blocks_until_human_decision_and_changes_decision_hash(
    workflow: SyntheticStage3Workflow,
) -> None:
    blocked = workflow.run(condition_id="D10_CONFLICT")
    assert blocked["gate"]["status"] == "BLOCKED"
    assert blocked["snapshot"] is None
    item = next(
        row for row in blocked["review_workbench"]["items"] if row["kind"] == "VALUE_CONFLICT"
    )
    assert item["requires_resolution"] is True
    assert item["target_path"] == "pipeline.operating_pressure_mpa"

    resolved = workflow.run(
        condition_id="D10_CONFLICT",
        decisions={"pipeline.operating_pressure_mpa": "OVERLAY"},
    )
    assert resolved["gate"]["status"] == "PASS"
    assert resolved["snapshot"]["qra_input"]["pipeline"]["operating_pressure_mpa"] == 8.35
    assert (
        resolved["review_workbench"]["decision_set_sha256"]
        != blocked["review_workbench"]["decision_set_sha256"]
    )
    audit = resolved["review_workbench"]["audit"][0]
    assert {"original_value", "new_value", "reason", "reviewer", "reviewed_at"} <= audit.keys()


def test_d20_missing_key_fields_are_not_zero_and_block_affected_nodes(
    workflow: SyntheticStage3Workflow,
) -> None:
    result = workflow.run(condition_id="D20_MISSING")
    assert result["gate"]["status"] == "BLOCKED"
    assert result["snapshot"] is None
    assert result["capability"]["incomplete"] is True
    assert "human_qra" in result["capability"]["blocked_node_ids"]
    missing = [
        item for item in result["review_workbench"]["items"] if item["kind"] == "MISSING_FIELD"
    ]
    assert missing
    assert all(item["missing_representation"] is None for item in missing)
    assert result["coverage_report"]["blank_to_zero_count"] == 0


def test_d30_low_quality_pdf_preserves_original_coordinates_and_routes_review(
    workflow: SyntheticStage3Workflow,
) -> None:
    result = workflow.run(condition_id="D30_LOW_QUALITY_SCAN")
    assert result["gate"]["status"] == "BLOCKED"
    low_confidence = [
        item for item in result["review_workbench"]["items"] if item["kind"] == "LOW_CONFIDENCE"
    ]
    assert len(low_confidence) == 42
    traces = result["parsed_artifacts"]["pdf_preprocessing"]
    assert traces
    assert all(item["original_bbox_normalized"] for item in traces)
    assert all(item["status"] == "LOW_CONFIDENCE_REVIEW_REQUIRED" for item in traces)


def test_d40_oversized_image_is_tiled_with_original_coordinate_mapping(
    workflow: SyntheticStage3Workflow,
) -> None:
    result = workflow.run(condition_id="D40_OVERSIZED_IMAGE")
    tiles = result["parsed_artifacts"]["image_tiling"]
    assert result["gate"]["status"] == "PASS"
    assert len(tiles) == 8
    assert tiles[-1]["original_bbox_pixels"][-1] == 12000
    assert all(tile["scaled_request_within_model_limit"] for tile in tiles)
    assert all(tile["reextract_scope"] == ["page", "field"] for tile in tiles)


def test_d50_prompt_injection_is_untrusted_content_only(
    workflow: SyntheticStage3Workflow,
) -> None:
    result = workflow.run(condition_id="D50_PROMPT_INJECTION")
    audit = result["security_audit"]
    assert result["gate"]["status"] == "PASS"
    assert audit["detected_evidence_ids"]
    assert audit["document_commands_trusted"] is False
    assert audit["workflow_changed"] is False
    assert audit["contract_changed"] is False
    assert audit["gate_changed"] is False
    assert audit["candidate_count_from_injection"] == 0
    assert result["coverage_report"]["prompt_injection_change_count"] == 0


def test_online_demo_failure_preserves_parsed_artifacts_and_human_route(
    workflow: SyntheticStage3Workflow, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "QRA_EXTRACTION_PROVIDER",
        "QRA_EXTRACTION_MODEL_VERSION",
        "QRA_ALIYUN_API_KEY",
        "QRA_ALIYUN_OPENAI_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    result = workflow.run(condition_id="D00_CLEAN", provider_mode="online")
    online = result["online_demo"]
    assert result["gate"]["status"] == "BLOCKED"
    assert result["snapshot"] is None
    assert online["status"] == "MODEL_UNAVAILABLE_PRESERVED_AND_ROUTED_TO_HUMAN"
    assert online["parsed_artifacts_preserved"] is True
    assert online["human_review_route_available"] is True
    assert online["external_call_made"] is False
    assert result["candidates"]


class _EchoOnlineProvider:
    provider_id = "test-online-provider"
    deployment_scope = "EXTERNAL"
    model_version = "test-online-v1"
    default_timeout_seconds = 30.0
    max_retries = 0
    max_concurrency = 1

    def __init__(self) -> None:
        self.calls = 0

    def extract(self, request):
        self.calls += 1
        items = []
        for block in request.document_blocks:
            content = json.loads(block["content"])
            items.append(
                {
                    "target_path": content["target_path"],
                    "raw_value": content["raw_value"],
                    "evidence_id": block["block_id"],
                }
            )
        return ExtractionResponse(
            provider_id=self.provider_id,
            model_id="test-online",
            model_version=self.model_version,
            structured_output={"items": items},
            provider_request_id="test-request-001",
        )


def test_online_demo_requires_explicit_sharing_and_calls_provider_when_authorized() -> None:
    provider = _EchoOnlineProvider()
    online_workflow = SyntheticStage3Workflow(
        PROJECT_ROOT,
        snapshot_persister=persist_confirmed_snapshot,
        online_provider=provider,
    )
    blocked = online_workflow.run(condition_id="D00_CLEAN", provider_mode="online")
    assert blocked["online_demo"]["status"] == (
        "CONFIGURED_REQUIRES_EXPLICIT_EXTERNAL_SHARING_OPT_IN"
    )
    assert blocked["online_demo"]["external_call_made"] is False
    assert provider.calls == 0

    completed = online_workflow.run(
        condition_id="D00_CLEAN",
        provider_mode="online",
        allow_external_sharing=True,
    )
    online = completed["online_demo"]
    assert completed["gate"]["status"] == "BLOCKED"
    assert completed["snapshot"] is None
    assert online["status"] == "COMPLETED_REVIEW_REQUIRED"
    assert online["external_call_made"] is True
    assert online["external_sharing_allowed"] is True
    assert online["metrics"]["expected_count"] == 256
    assert online["metrics"]["correct_count"] == 256
    assert online["metrics"]["precision"] == 1.0
    assert online["metrics"]["recall"] == 1.0
    assert online["metrics"]["chunk_count"] == 18
    assert online["audit_records"]
    assert provider.calls == 18


def test_online_demo_extracts_only_human_selected_conflict_candidate() -> None:
    provider = _EchoOnlineProvider()
    online_workflow = SyntheticStage3Workflow(
        PROJECT_ROOT,
        snapshot_persister=persist_confirmed_snapshot,
        online_provider=provider,
    )
    completed = online_workflow.run(
        condition_id="D10_CONFLICT",
        provider_mode="online",
        decisions={"pipeline.operating_pressure_mpa": "BASE"},
        allow_external_sharing=True,
    )

    conflict = next(
        item
        for item in completed["review_workbench"]["items"]
        if item["kind"] == "VALUE_CONFLICT"
    )
    assert conflict["requires_resolution"] is False
    assert conflict["resolution_status"] == "HUMAN_CONFIRMED"
    assert completed["review_workbench"]["audit"]
    assert completed["online_demo"]["metrics"]["expected_count"] == 256
    assert completed["online_demo"]["metrics"]["correct_count"] == 256
    assert provider.calls == 18
