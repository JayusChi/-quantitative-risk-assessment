from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from qra_converter.contract_catalog import load_contract_catalog
from qra_converter.contracts import SourceReference
from qra_converter.extraction.fields import evidence_from_documents
from qra_converter.extraction.fixture_provider import FixtureExtractionProvider
from qra_converter.orchestration.workflow import Stage4Workflow
from qra_converter.parsing.contracts import ParsedDocument, TextBlock, source_fragment_sha256

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = PROJECT_ROOT / "resources" / "contracts" / "part1" / "v1"
LABELLED_GOLDEN = (
    PROJECT_ROOT / "tests" / "fixtures" / "extraction_stage4" / "labelled_golden.json"
)


def run_labelled_golden() -> dict[str, Any]:
    labels = json.loads(LABELLED_GOLDEN.read_text(encoding="utf-8"))
    text = str(labels["document_text"])
    block = TextBlock(
        block_id="BLOCK-LABELLED-GOLDEN",
        text=text,
        normalized_text=text,
        reading_order=0,
        block_type="PARAGRAPH",
        extraction_method="NATIVE_TEXT",
        source_fragment_sha256=source_fragment_sha256(text),
    )
    document = ParsedDocument(
        document_id="DOC-LABELLED-GOLDEN",
        source=SourceReference(
            "SRC-LABELLED-GOLDEN",
            "labelled-golden.docx",
            "fixture",
            source_fragment_sha256(text),
        ),
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        document_kind="DOCX",
        parser_id="fixture",
        parser_version="1.0.0",
        page_count=1,
        text_blocks=(block,),
    ).finalized()
    evidence_id = evidence_from_documents((document,)).blocks[0]["evidence_id"]
    entities = [
        {
            **entity,
            "time_range": None,
            "chainage_range": None,
            "coordinate_range": None,
            "evidence_ids": [evidence_id],
            "confidence": 0.99,
            "source_id": "SRC-LABELLED-GOLDEN",
        }
        for entity in labels["entities"]
    ]
    field_items = [
        {
            "field_id": fact["field_id"],
            "entity_id": fact["entity_id"],
            "raw_value": fact["raw_value"],
            "source_unit": fact["source_unit"],
            "normalized_value": fact["expected_normalized_value"],
            "confidence": 0.99,
            "evidence_ids": [evidence_id],
            "not_found": False,
        }
        for fact in labels["facts"]
    ]
    provider = FixtureExtractionProvider(
        {
            "CLASSIFY": {
                "items": [
                    {
                        "source_id": "SRC-LABELLED-GOLDEN",
                        "primary_category": "PIPELINE_SEGMENT_REGISTER",
                        "secondary_categories": ["OPERATING_EVENT"],
                        "confidence": 0.99,
                        "evidence_ids": [evidence_id],
                    }
                ]
            },
            "EXTRACT_ENTITIES": {"items": entities},
            "EXTRACT_FIELDS": {"items": field_items},
            "EXTRACT_RELATIONSHIPS": {
                "items": [
                    {
                        "relation_type": "BELONGS_TO",
                        "source_entity_id": "ENT-SEG-A-S1",
                        "target_entity_id": "ENT-PIPE-A",
                        "confidence": 0.99,
                        "evidence_ids": [evidence_id],
                    }
                ]
            },
        }
    )
    golden_candidates = [
        {
            "field_id": fact["field_id"],
            "entity": {"entity_key": fact["entity_key"]},
            "normalized_value": fact["expected_normalized_value"],
        }
        for fact in labels["facts"]
    ]
    result = Stage4Workflow(
        catalog=load_contract_catalog(CONTRACT_ROOT),
        provider=provider,
    ).run(
        job_id="STAGE4-LABELLED-GOLDEN",
        documents=(document,),
        mapping_version="fixture-labelled/1.0.0",
        field_subset=tuple(fact["field_id"] for fact in labels["facts"]),
        golden_candidates=golden_candidates,
    )
    metrics = dict(result.metrics)
    metrics.update(
        {
            "golden_set_id": labels["golden_set_id"],
            "labelled_fact_count": len(labels["facts"]),
            "provider": "fixture",
        }
    )
    return metrics


class Stage4LabelledGoldenTests(unittest.TestCase):
    def test_labelled_fixture_meets_candidate_and_normalization_gates(self) -> None:
        metrics = run_labelled_golden()
        self.assertGreaterEqual(metrics["precision"], 0.95)
        self.assertGreaterEqual(metrics["recall"], 0.90)
        self.assertEqual(metrics["evidence_binding_rate"], 1.0)
        self.assertEqual(metrics["candidate_count"], metrics["labelled_fact_count"])


if __name__ == "__main__":
    unittest.main()
