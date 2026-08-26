from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .contracts import (
    ConversionIssue,
    IssueSeverity,
    RawTable,
    ReviewAuditEntry,
    ReviewItem,
)
from .mapping.mapper import MappedTable, MappingOutcome
from .mapping.values import ValueConversionError, convert_value

REVIEW_SCHEMA_VERSION = "1.0.0"
_HELPER_FIELDS = {
    "source_ref",
    "source_refs",
    "quality",
    "review_status",
}


@dataclass(frozen=True)
class ReviewDecision:
    review_id: str
    action: str
    reviewer: str
    reviewed_at: str
    reason: str
    value: Any = None


@dataclass(frozen=True)
class ReviewBundle:
    decisions: dict[str, ReviewDecision]
    source: dict[str, Any] | None = None


@dataclass(frozen=True)
class MergeResult:
    outcome: MappingOutcome
    issues: tuple[ConversionIssue, ...]
    review_items: tuple[ReviewItem, ...]
    audit: tuple[ReviewAuditEntry, ...]
    used_review_ids: frozenset[str]


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _review_id(value: dict[str, Any]) -> str:
    digest = hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()
    return f"REV-{digest[:20].upper()}"


def _validate_reviewed_at(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("复核决定必须声明reviewed_at")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"reviewed_at不是ISO 8601时间：{text}") from exc
    if parsed.tzinfo is None:
        raise ValueError("reviewed_at必须包含时区")
    return parsed.isoformat()


def load_review_bundle(path: Path | None) -> ReviewBundle:
    if path is None:
        return ReviewBundle({})
    review_path = path.resolve()
    data = review_path.read_bytes()
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("复核决定文件根节点必须是对象")
    if payload.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise ValueError(f"复核决定schema_version必须为{REVIEW_SCHEMA_VERSION}")
    rows = payload.get("decisions")
    if not isinstance(rows, list):
        raise ValueError("复核决定decisions必须是数组")
    default_reviewer = str(payload.get("reviewer") or "").strip()
    default_reviewed_at = payload.get("reviewed_at")
    decisions: dict[str, ReviewDecision] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("每项复核决定必须是对象")
        review_id = str(row.get("review_id") or "").strip()
        if not review_id or review_id in decisions:
            raise ValueError(f"复核ID缺失或重复：{review_id or '空'}")
        reviewer = str(row.get("reviewer") or default_reviewer).strip()
        reason = str(row.get("reason") or "").strip()
        action = str(row.get("action") or "").strip().upper()
        if not reviewer or not reason or not action:
            raise ValueError(f"复核决定{review_id}必须包含action、reviewer和reason")
        decisions[review_id] = ReviewDecision(
            review_id=review_id,
            action=action,
            reviewer=reviewer,
            reviewed_at=_validate_reviewed_at(row.get("reviewed_at", default_reviewed_at)),
            reason=reason,
            value=row.get("value"),
        )
    return ReviewBundle(
        decisions,
        {
            "file_name": review_path.name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "decision_count": len(decisions),
        },
    )


def _record_key_fields(target: str, definition: dict[str, Any]) -> tuple[str, ...]:
    configured = definition.get("record_key")
    if configured:
        return tuple(str(item) for item in configured)
    if target == "pipeline":
        return ()
    if target == "segments":
        return ("segment_id",)
    if target == "population_cells":
        return ("target_id",)
    return ("record_id",)


def _record_key(
    target: str,
    fields: tuple[str, ...],
    record: dict[str, Any],
    fallback_index: int,
) -> tuple[str, dict[str, Any]]:
    if not fields:
        value = {"$": "singleton"}
        return _canonical(value), value
    value = {field: record.get(field) for field in fields}
    if any(item in (None, "") for item in value.values()):
        source = record.get("source_ref") or {}
        value["$unkeyed"] = {
            "source": source.get("file_sha256"),
            "sheet": source.get("sheet_name"),
            "row": source.get("row_number"),
            "index": fallback_index,
        }
    return _canonical(value), value


def _candidate_sort_key(record: dict[str, Any]) -> tuple[Any, ...]:
    source = record.get("source_ref") or {}
    return (
        -int(record.get("_source_priority", 0)),
        str(source.get("file_name", "")).casefold(),
        str(source.get("sheet_name", "")).casefold(),
        int(source.get("row_number") or 0),
        str(source.get("file_sha256", "")),
    )


def _source_refs(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_value: dict[str, dict[str, Any]] = {}
    for record in records:
        for source in [record.get("source_ref"), *(record.get("source_refs") or [])]:
            if isinstance(source, dict):
                by_value[_canonical(source)] = source
    return [by_value[key] for key in sorted(by_value)]


def _business_fields(record: dict[str, Any]) -> set[str]:
    return {key for key in record if key not in _HELPER_FIELDS and not key.startswith("_")}


def _target_path(target: str, key: dict[str, Any], field: str | None = None) -> str:
    key_text = ",".join(f"{name}={value}" for name, value in sorted(key.items()))
    path = f"{target}[{key_text}]"
    return f"{path}.{field}" if field else path


def _candidate_document(record: dict[str, Any], value: Any) -> dict[str, Any]:
    return {
        "value": value,
        "priority": int(record.get("_source_priority", 0)),
        "confidence": float(record.get("_mapping_confidence", 1.0)),
        "extraction_method": record.get("_extraction_method"),
        "source_ref": record.get("source_ref"),
    }


def _make_review_item(
    *,
    kind: str,
    reason: str,
    target_path: str,
    record_key: dict[str, Any],
    proposed_value: Any,
    candidates: list[dict[str, Any]],
    confidence: float | None = None,
    blocking: bool = True,
) -> ReviewItem:
    identity = {
        "kind": kind,
        "target_path": target_path,
        "record_key": record_key,
        "candidates": candidates,
    }
    return ReviewItem(
        review_id=_review_id(identity),
        kind=kind,
        reason=reason,
        target_path=target_path,
        record_key=record_key,
        proposed_value=proposed_value,
        candidates=tuple(candidates),
        confidence=confidence,
        blocking=blocking,
    )


def _audit(
    item: ReviewItem,
    decision: ReviewDecision,
    before: Any,
    after: Any,
) -> ReviewAuditEntry:
    return ReviewAuditEntry(
        review_id=item.review_id,
        action=decision.action,
        reviewer=decision.reviewer,
        reviewed_at=decision.reviewed_at,
        reason=decision.reason,
        target_path=item.target_path,
        before_value=before,
        after_value=after,
    )


def _validated_replacement(value: Any, field: dict[str, Any] | None) -> Any:
    if field is None:
        return value
    if str(field.get("type", "string")) == "enum":
        allowed = list((field.get("enum_map") or {}).values())
        if value not in allowed:
            raise ValueConversionError(f"复核值不在标准枚举中：{value}")
        return value
    normalized_field = dict(field)
    if normalized_field.get("target_unit") is not None:
        normalized_field["source_unit"] = normalized_field["target_unit"]
    return convert_value(value, normalized_field)


def merge_mapped_tables(outcome: MappingOutcome, bundle: ReviewBundle) -> MergeResult:
    grouped: dict[str, list[MappedTable]] = {}
    for table in outcome.tables:
        grouped.setdefault(str(table.definition["target"]), []).append(table)

    merged_tables: list[MappedTable] = []
    issues: list[ConversionIssue] = []
    review_items: list[ReviewItem] = []
    audit: list[ReviewAuditEntry] = []
    used: set[str] = set()

    for target in sorted(grouped):
        tables = grouped[target]
        definition = tables[0].definition
        key_fields = _record_key_fields(target, definition)
        incompatible = [
            table
            for table in tables[1:]
            if _record_key_fields(target, table.definition) != key_fields
        ]
        if incompatible:
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    "MERGE_RECORD_KEY_CONFLICT",
                    f"目标{target}的映射规则声明了不同record_key",
                    target_path=target,
                )
            )
        records = [record for table in tables for record in table.records]
        by_key: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
        for index, record in enumerate(records):
            token, key = _record_key(target, key_fields, record, index)
            by_key.setdefault(token, (key, []))[1].append(record)

        target_records: list[dict[str, Any]] = []
        field_definitions = {
            str(field["target"]): field for table in tables for field in table.definition["fields"]
        }
        for token in sorted(by_key):
            key, candidates = by_key[token]
            ordered = sorted(candidates, key=_candidate_sort_key)
            merged: dict[str, Any] = {}
            drop_record = False
            conflict_pending = False
            human_resolved = False
            record_review_start = len(review_items)
            for field_name in sorted(set().union(*(_business_fields(row) for row in ordered))):
                with_value = [row for row in ordered if field_name in row]
                if not with_value:
                    continue
                by_value: dict[str, list[dict[str, Any]]] = {}
                for row in with_value:
                    by_value.setdefault(_canonical(row[field_name]), []).append(row)
                selected = with_value[0]
                selected_value = selected[field_name]
                if len(by_value) == 1:
                    merged[field_name] = selected_value
                    continue
                candidate_docs = [_candidate_document(row, row[field_name]) for row in with_value]
                item = _make_review_item(
                    kind="SOURCE_VALUE_CONFLICT",
                    reason=(
                        f"字段{field_name}存在{len(by_value)}个不同标准化值；"
                        "来源优先级只用于生成建议值，不能代替人工确认"
                    ),
                    target_path=_target_path(target, key, field_name),
                    record_key=key,
                    proposed_value=selected_value,
                    candidates=candidate_docs,
                    confidence=min(
                        float(row.get("_mapping_confidence", 1.0)) for row in with_value
                    ),
                )
                decision = bundle.decisions.get(item.review_id)
                if decision is None:
                    merged[field_name] = selected_value
                    review_items.append(item)
                    conflict_pending = True
                    continue
                used.add(item.review_id)
                try:
                    if decision.action == "ACCEPT_PROPOSED":
                        resolved = selected_value
                    elif decision.action == "REPLACE_VALUE":
                        resolved = _validated_replacement(
                            decision.value, field_definitions.get(field_name)
                        )
                    elif decision.action == "REJECT_RECORD":
                        resolved = None
                        drop_record = True
                    else:
                        raise ValueError(
                            "冲突复核action必须为ACCEPT_PROPOSED、REPLACE_VALUE或REJECT_RECORD"
                        )
                except (ValueError, ValueConversionError) as exc:
                    issues.append(
                        ConversionIssue(
                            IssueSeverity.ERROR,
                            "REVIEW_DECISION_INVALID",
                            f"复核决定{item.review_id}无效：{exc}",
                            target_path=item.target_path,
                        )
                    )
                    merged[field_name] = selected_value
                    review_items.append(item)
                    conflict_pending = True
                else:
                    if not drop_record:
                        merged[field_name] = resolved
                    audit.append(_audit(item, decision, selected_value, resolved))
                    human_resolved = True
            if drop_record:
                del review_items[record_review_start:]
                continue

            primary = ordered[0]
            merged["source_ref"] = primary.get("source_ref")
            merged["source_refs"] = _source_refs(ordered)
            needs_review = any(bool(row.get("_requires_review")) for row in ordered)
            confidence = min(float(row.get("_mapping_confidence", 1.0)) for row in ordered)
            if needs_review:
                item = _make_review_item(
                    kind="LOW_CONFIDENCE_MAPPING",
                    reason="记录来自辅助文档、OCR/文本提取或低置信度映射，进入计算前必须确认",
                    target_path=_target_path(target, key),
                    record_key=key,
                    proposed_value={
                        name: value for name, value in merged.items() if name not in _HELPER_FIELDS
                    },
                    candidates=[
                        _candidate_document(
                            row,
                            {
                                name: value
                                for name, value in row.items()
                                if name not in _HELPER_FIELDS and not name.startswith("_")
                            },
                        )
                        for row in ordered
                        if row.get("_requires_review")
                    ],
                    confidence=confidence,
                )
                decision = bundle.decisions.get(item.review_id)
                if decision is None:
                    review_items.append(item)
                    merged["review_status"] = "NEEDS_CONFIRMATION"
                else:
                    used.add(item.review_id)
                    if decision.action in {"CONFIRM_RECORD", "ACCEPT_PROPOSED"}:
                        merged["review_status"] = "HUMAN_CONFIRMED"
                        audit.append(
                            _audit(item, decision, item.proposed_value, item.proposed_value)
                        )
                    elif decision.action == "REJECT_RECORD":
                        audit.append(_audit(item, decision, item.proposed_value, None))
                        continue
                    else:
                        issues.append(
                            ConversionIssue(
                                IssueSeverity.ERROR,
                                "REVIEW_DECISION_INVALID",
                                f"低置信度复核{item.review_id}只接受CONFIRM_RECORD或REJECT_RECORD",
                                target_path=item.target_path,
                            )
                        )
                        review_items.append(item)
                        merged["review_status"] = "NEEDS_CONFIRMATION"
            elif conflict_pending:
                merged["review_status"] = "NEEDS_CONFIRMATION"
            elif human_resolved or any(
                row.get("review_status") == "HUMAN_CONFIRMED" for row in ordered
            ):
                merged["review_status"] = "HUMAN_CONFIRMED"
            else:
                merged["review_status"] = "AUTO_MAPPED"
            merged["quality"] = "C" if needs_review or conflict_pending else "A"
            target_records.append(merged)
        merged_tables.append(MappedTable(definition, tuple(target_records)))

    merged_outcome = replace(outcome, tables=tuple(merged_tables))
    return MergeResult(
        merged_outcome,
        tuple(issues),
        tuple(review_items),
        tuple(audit),
        frozenset(used),
    )


def auxiliary_review_items(
    raw_tables: Iterable[RawTable],
    matched_table_keys: Iterable[tuple[str, str, str]],
    bundle: ReviewBundle,
) -> tuple[
    tuple[ReviewItem, ...],
    tuple[ReviewAuditEntry, ...],
    frozenset[str],
]:
    matched = {(source_id, sheet) for source_id, sheet, _ in matched_table_keys}
    pending: list[ReviewItem] = []
    audit: list[ReviewAuditEntry] = []
    used: set[str] = set()
    for table in raw_tables:
        if not table.requires_review or (table.source.source_id, table.sheet_name) in matched:
            continue
        source = table.source
        candidates = [
            {
                "source_ref": {
                    "file_sha256": source.checksum_sha256,
                    "file_name": source.source_path,
                    "sheet_name": table.sheet_name,
                },
                "extraction_method": table.extraction_method,
                "row_count": len(table.rows),
                "sample": [list(row.cells) for row in table.rows[:3]],
            }
        ]
        item = _make_review_item(
            kind="UNMAPPED_AUXILIARY_CONTENT",
            reason="辅助文档内容未匹配确定性映射，已排除在case.json之外，需人工决定是否补充映射",
            target_path=f"unmapped:{source.source_path}/{table.sheet_name}",
            record_key={"source_id": source.source_id, "sheet_name": table.sheet_name},
            proposed_value=None,
            candidates=candidates,
            confidence=table.confidence,
            blocking=False,
        )
        decision = bundle.decisions.get(item.review_id)
        if decision is None:
            pending.append(item)
        else:
            used.add(item.review_id)
            if decision.action not in {"ACKNOWLEDGE_NOT_IMPORTED", "IGNORE_SOURCE"}:
                pending.append(item)
            else:
                audit.append(_audit(item, decision, None, None))
    return tuple(pending), tuple(audit), frozenset(used)


__all__ = [
    "MergeResult",
    "REVIEW_SCHEMA_VERSION",
    "ReviewBundle",
    "auxiliary_review_items",
    "load_review_bundle",
    "merge_mapped_tables",
]
