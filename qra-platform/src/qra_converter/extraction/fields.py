"""Candidate construction from deterministic lineage and constrained model fields."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..contracts import FieldLineage
from ..parsing.contracts import ParsedDocument


def _stable_id(prefix: str, value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()[:24]}"


def _box(value: Any) -> list[float] | None:
    if value is None:
        return None
    return [float(value.x), float(value.y), float(value.width), float(value.height)]


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    evidence: tuple[dict[str, Any], ...]
    blocks: tuple[dict[str, Any], ...]
    text_by_id: dict[str, str]
    block_id_to_evidence_id: dict[str, str]


def chunk_document_blocks(
    blocks: tuple[dict[str, Any], ...],
    *,
    max_characters: int = 60_000,
    overlap_blocks: int = 1,
) -> tuple[tuple[dict[str, Any], ...], ...]:
    if max_characters < 1 or overlap_blocks < 0:
        raise ValueError("分块参数无效")
    batches: list[tuple[dict[str, Any], ...]] = []
    current: list[dict[str, Any]] = []
    characters = 0
    for block in blocks:
        size = len(str(block.get("text") or ""))
        if current and characters + size > max_characters:
            batches.append(tuple(current))
            current = current[-overlap_blocks:] if overlap_blocks else []
            characters = sum(len(str(item.get("text") or "")) for item in current)
        current.append(block)
        characters += size
    if current:
        batches.append(tuple(current))
    return tuple(batches)


def evidence_from_documents(documents: tuple[ParsedDocument, ...]) -> EvidenceBundle:
    evidence: dict[str, dict[str, Any]] = {}
    blocks: list[dict[str, Any]] = []
    text_by_id: dict[str, str] = {}
    block_map: dict[str, str] = {}
    for document in documents:
        source_id = document.source.source_id
        media = document.media_type.casefold()
        unique_blocks = {block.block_id: block for block in document.text_blocks}
        for page in document.pages:
            for block in page.text_blocks:
                unique_blocks.setdefault(block.block_id, block)
        for block in sorted(unique_blocks.values(), key=lambda item: item.reading_order):
            evidence_id = _stable_id("EVD", [source_id, "block", block.block_id])
            block_map[block.block_id] = evidence_id
            text = block.text
            if "pdf" in media and block.bbox is not None:
                location = {
                    "kind": "PDF",
                    "file_id": source_id,
                    "page": int(block.page_number or 1),
                    "bbox": _box(block.bbox),
                    "page_size": [float(block.page_width or 1), float(block.page_height or 1)],
                    "coordinate_system": str(
                        block.coordinate_space.value
                        if block.coordinate_space
                        else "PDF_POINTS_TOP_LEFT"
                    ),
                }
                source_type = "PDF"
            elif media.startswith("image/") and block.bbox is not None:
                location = {
                    "kind": "IMAGE",
                    "file_id": source_id,
                    "bbox": _box(block.bbox),
                    "image_size": [float(block.page_width or 1), float(block.page_height or 1)],
                    "coordinate_system": str(
                        block.coordinate_space.value
                        if block.coordinate_space
                        else "IMAGE_PIXELS_TOP_LEFT"
                    ),
                }
                source_type = "IMAGE"
            else:
                location = {
                    "kind": "DOCX",
                    "file_id": source_id,
                    "ooxml_part": str(block.structure_location or "document"),
                    "paragraph_index": max(0, int(block.reading_order)),
                    "coordinate_system": str(
                        block.coordinate_space.value if block.coordinate_space else "DOCX_STRUCTURE"
                    ),
                }
                source_type = "DOCX"
            evidence[evidence_id] = {
                "evidence_id": evidence_id,
                "source_type": source_type,
                "location": location,
                "excerpt": text[:500],
                "checksum_sha256": block.source_fragment_sha256,
            }
            blocks.append(
                {
                    "evidence_id": evidence_id,
                    "source_id": source_id,
                    "content_type": block.block_type,
                    "text": text,
                }
            )
            text_by_id[evidence_id] = text
        for table in document.tables:
            for cell in table.cells:
                evidence_id = _stable_id("EVD", [source_id, "cell", table.table_id, cell.address])
                text = cell.display_text
                evidence[evidence_id] = {
                    "evidence_id": evidence_id,
                    "source_type": "TABLE",
                    "location": {
                        "kind": "TABLE",
                        "file_id": source_id,
                        "sheet_name": str(table.sheet_name or table.table_id),
                        "row": cell.row_index,
                        "column": cell.column_index,
                        "cell_text": text,
                        "coordinate_system": str(
                            cell.coordinate_space.value
                            if cell.coordinate_space
                            else "WORKSHEET_GRID"
                        ),
                    },
                    "excerpt": text[:500],
                    "checksum_sha256": cell.source_fragment_sha256,
                }
                text_by_id[evidence_id] = text
    return EvidenceBundle(
        evidence=tuple(evidence[key] for key in sorted(evidence)),
        blocks=tuple(blocks),
        text_by_id=text_by_id,
        block_id_to_evidence_id=block_map,
    )


def _path_candidates(target_path: str) -> tuple[str, ...]:
    canonical = target_path.replace("[*]", ".*")
    canonical = re.sub(r"\.+", ".", canonical)
    values = [canonical]
    if canonical.startswith("segments.*."):
        values.append("segment.*." + canonical.removeprefix("segments.*."))
    if canonical.startswith("population_cells.*."):
        values.append("population_cell.*." + canonical.removeprefix("population_cells.*."))
    return tuple(values)


def field_index(field_dictionary: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["field_id"]): dict(item) for item in field_dictionary["fields"]}


def target_path_index(field_dictionary: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["target_path"]): dict(item) for item in field_dictionary["fields"]}


def deterministic_candidates_from_lineage(
    lineage: tuple[FieldLineage, ...],
    *,
    field_dictionary: Mapping[str, Any],
    mapping_version: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_target = target_path_index(field_dictionary)
    candidates: list[dict[str, Any]] = []
    evidence: dict[str, dict[str, Any]] = {}
    for row in lineage:
        definition = next(
            (by_target[path] for path in _path_candidates(row.target_path) if path in by_target),
            None,
        )
        if definition is None:
            continue
        evidence_id = _stable_id(
            "EVD",
            [row.source_id, row.sheet_name, row.row_number, row.column_name],
        )
        evidence[evidence_id] = {
            "evidence_id": evidence_id,
            "source_type": "TABLE",
            "location": {
                "kind": "TABLE",
                "file_id": row.source_id,
                "sheet_name": row.sheet_name,
                "row": row.row_number,
                "column": 1,
                "cell_text": str(row.original_value),
                "coordinate_system": "WORKSHEET_GRID",
            },
            "excerpt": str(row.original_value)[:500],
            "checksum_sha256": None,
        }
        entity_type = str(definition["entity_type"])
        entity_key = f"{entity_type}:{row.source_id}:{row.sheet_name}:{row.row_number}"
        candidate_seed = [definition["field_id"], entity_key, evidence_id, row.original_value]
        candidates.append(
            {
                "candidate_id": _stable_id("CAND", candidate_seed),
                "field_id": str(definition["field_id"]),
                "entity": {"entity_type": entity_type, "entity_key": entity_key},
                "raw_value": row.original_value,
                "parsed_value": row.normalized_value,
                "normalized_value": row.normalized_value,
                "source_unit": row.source_unit,
                "canonical_unit": definition.get("canonical_unit") or row.target_unit,
                "confidence": 1.0,
                "extraction_method": "STRUCTURED_TABLE",
                "evidence_ids": [evidence_id],
                "quality_status": "PASS",
                "review_status": "PENDING",
                "model_or_rule_versions": {
                    "mapping": mapping_version,
                    "normalization": "mapping.values/1.0.0",
                    "source_rank": 100,
                },
            }
        )
    return candidates, [evidence[key] for key in sorted(evidence)]


def model_candidates(
    items: list[dict[str, Any]],
    *,
    entities: dict[str, dict[str, Any]],
    model_versions: dict[str, str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        if item.get("not_found") is True:
            continue
        entity = entities[str(item["entity_id"])]
        candidate_id = str(item.get("candidate_id") or "")
        if not candidate_id.startswith("CAND-"):
            candidate_id = _stable_id(
                "CAND",
                [
                    item["field_id"],
                    item["entity_id"],
                    sorted(item.get("evidence_ids") or []),
                    item.get("raw_value"),
                ],
            )
        result.append(
            {
                "candidate_id": candidate_id,
                "field_id": str(item["field_id"]),
                "entity": {
                    "entity_type": str(entity["entity_type"]),
                    "entity_key": str(entity.get("business_key") or entity["entity_id"]),
                },
                "raw_value": item.get("raw_value"),
                "parsed_value": item.get("raw_value"),
                "normalized_value": item.get("normalized_value"),
                "source_unit": item.get("source_unit"),
                "canonical_unit": None,
                "confidence": float(item.get("confidence", 0.0)),
                "extraction_method": "MODEL_EXTRACTION",
                "evidence_ids": list(item.get("evidence_ids") or []),
                "quality_status": "PENDING_REVIEW",
                "review_status": "PENDING",
                "model_or_rule_versions": model_versions,
            }
        )
    return result


__all__ = [
    "EvidenceBundle",
    "deterministic_candidates_from_lineage",
    "chunk_document_blocks",
    "evidence_from_documents",
    "field_index",
    "model_candidates",
    "target_path_index",
]
