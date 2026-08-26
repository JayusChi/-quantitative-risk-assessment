from __future__ import annotations

import copy
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..contracts import (
    ConversionIssue,
    FieldLineage,
    IssueSeverity,
    RawRow,
    RawTable,
)
from .profile import MappingProfile, selector_matches, source_priority
from .values import ValueConversionError, convert_value


def normalize_header(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().casefold()
    return re.sub(r"[\s_\-—－:：()（）\[\]【】/\\]+", "", text)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


@dataclass(frozen=True)
class MappedTable:
    definition: dict[str, Any]
    records: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class MappingOutcome:
    defaults: dict[str, Any]
    tables: tuple[MappedTable, ...]
    issues: tuple[ConversionIssue, ...]
    lineage: tuple[FieldLineage, ...]
    matched_table_keys: tuple[tuple[str, str, str], ...]


class ProfileMapper:
    def __init__(self, profile: MappingProfile):
        self.profile = profile

    @staticmethod
    def _alias_map(table: dict[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for field in table["fields"]:
            for alias in field["aliases"]:
                normalized = normalize_header(alias)
                existing = result.get(normalized)
                if existing is not None and existing != field["target"]:
                    raise ValueError(f"映射{table['id']}的表头别名冲突：{alias}")
                result[normalized] = str(field["target"])
        return result

    @staticmethod
    def _detect_header(
        raw_table: RawTable, definition: dict[str, Any]
    ) -> tuple[RawRow | None, dict[str, int]]:
        alias_map = ProfileMapper._alias_map(definition)
        search_rows = int(definition.get("header_search_rows", 20))
        minimum_matches = int(definition.get("minimum_header_matches", 1))
        best: tuple[int, RawRow | None, dict[str, int]] = (0, None, {})
        for row in raw_table.rows[:search_rows]:
            columns: dict[str, int] = {}
            for index, value in enumerate(row.cells):
                target = alias_map.get(normalize_header(value))
                if target is not None and target not in columns:
                    columns[target] = index
            score = len(columns)
            if score > best[0]:
                best = (score, row, columns)
        if best[0] < minimum_matches:
            return None, {}
        return best[1], best[2]

    @staticmethod
    def _source_ref(raw_table: RawTable, row_number: int, profile_id: str) -> dict[str, Any]:
        return {
            "file_sha256": raw_table.source.checksum_sha256,
            "file_name": raw_table.source.source_path,
            "sheet_name": raw_table.sheet_name,
            "row_number": row_number,
            "mapping_profile": profile_id,
        }

    def _map_table(
        self, raw_table: RawTable, definition: dict[str, Any]
    ) -> tuple[MappedTable | None, list[ConversionIssue], list[FieldLineage]]:
        issues: list[ConversionIssue] = []
        lineage: list[FieldLineage] = []
        header, columns = self._detect_header(raw_table, definition)
        source_id = raw_table.source.source_id
        base_location = f"{raw_table.source.source_path}/{raw_table.sheet_name}"
        if header is None:
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    "HEADER_NOT_FOUND",
                    (
                        f"无法在前{definition.get('header_search_rows', 20)}行"
                        f"识别{definition['id']}表头"
                    ),
                    source_id,
                    base_location,
                    definition["target"],
                )
            )
            return None, issues, lineage

        field_by_target = {str(field["target"]): field for field in definition["fields"]}
        alias_map = self._alias_map(definition)
        source_columns_by_target: dict[str, list[int]] = {}
        for index, value in enumerate(header.cells):
            target = alias_map.get(normalize_header(value))
            if target is not None:
                source_columns_by_target.setdefault(target, []).append(index)
        for target, indexes in source_columns_by_target.items():
            if len(indexes) > 1:
                issues.append(
                    ConversionIssue(
                        IssueSeverity.ERROR,
                        "DUPLICATE_SOURCE_COLUMN",
                        f"多个源列同时映射到字段{target}",
                        source_id,
                        f"{base_location}!row {header.row_number}",
                        f"{definition['target']}[*].{target}",
                    )
                )
        for target, field in field_by_target.items():
            if field.get("required") and target not in columns:
                issues.append(
                    ConversionIssue(
                        IssueSeverity.ERROR,
                        "REQUIRED_COLUMN_MISSING",
                        f"缺少必填列：{target}",
                        source_id,
                        f"{base_location}!row {header.row_number}",
                        f"{definition['target']}[*].{target}",
                    )
                )

        matched_indexes = set(columns.values())
        for index, value in enumerate(header.cells):
            if index not in matched_indexes and not _is_blank(value):
                issues.append(
                    ConversionIssue(
                        IssueSeverity.INFO,
                        "UNMAPPED_COLUMN",
                        f"源列未使用：{value}",
                        source_id,
                        f"{base_location}!R{header.row_number}C{index + 1}",
                    )
                )

        records: list[dict[str, Any]] = []
        confidence = min(
            float(raw_table.confidence),
            float(definition.get("mapping_confidence", 1.0)),
        )
        threshold = float((self.profile.manual_review or {}).get("confidence_threshold", 0.8))
        needs_review = raw_table.requires_review or confidence < threshold
        priority = source_priority(
            self.profile,
            definition,
            raw_table.source.source_path,
            raw_table.sheet_name,
        )
        for row in raw_table.rows:
            if row.row_number <= header.row_number:
                continue
            if all(_is_blank(value) for value in row.cells):
                continue
            if not any(
                column_index < len(row.cells) and not _is_blank(row.cells[column_index])
                for column_index in columns.values()
            ):
                continue
            record: dict[str, Any] = {}
            row_has_mapped_value = False
            for target, field in field_by_target.items():
                column_index = columns.get(target)
                original = (
                    row.cells[column_index]
                    if column_index is not None and column_index < len(row.cells)
                    else None
                )
                if _is_blank(original):
                    if field.get("required"):
                        issues.append(
                            ConversionIssue(
                                IssueSeverity.ERROR,
                                "REQUIRED_FIELD_MISSING",
                                f"必填字段{target}为空",
                                source_id,
                                f"{base_location}!R{row.row_number}",
                                f"{definition['target']}[*].{target}",
                            )
                        )
                    continue
                row_has_mapped_value = True
                try:
                    normalized = convert_value(original, field)
                except ValueConversionError as exc:
                    issues.append(
                        ConversionIssue(
                            IssueSeverity.ERROR,
                            "VALUE_CONVERSION_FAILED",
                            f"字段{target}无法转换：{exc}",
                            source_id,
                            f"{base_location}!R{row.row_number}C{column_index + 1}",
                            f"{definition['target']}[*].{target}",
                        )
                    )
                    continue
                record[target] = normalized
                lineage.append(
                    FieldLineage(
                        source_id=source_id,
                        file_name=raw_table.source.source_path,
                        sheet_name=raw_table.sheet_name,
                        row_number=row.row_number,
                        column_name=str(header.cells[column_index]),
                        target_path=f"{definition['target']}[*].{target}",
                        original_value=original,
                        normalized_value=normalized,
                        source_unit=field.get("source_unit"),
                        target_unit=field.get("target_unit"),
                    )
                )
            if not row_has_mapped_value:
                continue
            record["source_ref"] = self._source_ref(
                raw_table, row.row_number, self.profile.profile_id
            )
            default_quality = "A" if confidence >= 0.95 else ("B" if confidence >= 0.8 else "C")
            record["quality"] = str(definition.get("quality", default_quality))
            record["review_status"] = "NEEDS_CONFIRMATION" if needs_review else "AUTO_MAPPED"
            record["_source_priority"] = priority
            record["_mapping_confidence"] = confidence
            record["_requires_review"] = needs_review
            record["_extraction_method"] = raw_table.extraction_method
            records.append(record)
        return MappedTable(definition, tuple(records)), issues, lineage

    def map(self, raw_tables: Iterable[RawTable]) -> MappingOutcome:
        mapped_tables: list[MappedTable] = []
        issues: list[ConversionIssue] = []
        lineage: list[FieldLineage] = []
        matched_keys: list[tuple[str, str, str]] = []
        match_counts = {str(definition["id"]): 0 for definition in self.profile.tables}
        for raw_table in raw_tables:
            matches = [
                definition
                for definition in self.profile.tables
                if selector_matches(
                    definition,
                    raw_table.source.source_path,
                    raw_table.sheet_name,
                )
            ]
            if not matches:
                if raw_table.rows:
                    issues.append(
                        ConversionIssue(
                            IssueSeverity.WARNING,
                            "SOURCE_TABLE_UNMATCHED",
                            "源表未匹配任何映射规则",
                            raw_table.source.source_id,
                            f"{raw_table.source.source_path}/{raw_table.sheet_name}",
                        )
                    )
                continue
            if len(matches) > 1:
                issues.append(
                    ConversionIssue(
                        IssueSeverity.ERROR,
                        "SOURCE_TABLE_MAPPING_AMBIGUOUS",
                        "源表同时匹配多个规则：" + ", ".join(str(row["id"]) for row in matches),
                        raw_table.source.source_id,
                        f"{raw_table.source.source_path}/{raw_table.sheet_name}",
                    )
                )
                continue
            definition = matches[0]
            mapped, table_issues, table_lineage = self._map_table(raw_table, definition)
            issues.extend(table_issues)
            lineage.extend(table_lineage)
            if mapped is not None:
                mapped_tables.append(mapped)
                match_counts[str(definition["id"])] += 1
                matched_keys.append(
                    (
                        raw_table.source.source_id,
                        raw_table.sheet_name,
                        str(definition["id"]),
                    )
                )
        for definition in self.profile.tables:
            if definition.get("required") and not match_counts[str(definition["id"])]:
                issues.append(
                    ConversionIssue(
                        IssueSeverity.ERROR,
                        "REQUIRED_TABLE_MISSING",
                        f"未找到必需源表：{definition['id']}",
                        target_path=str(definition["target"]),
                    )
                )
        return MappingOutcome(
            copy.deepcopy(self.profile.defaults),
            tuple(mapped_tables),
            tuple(issues),
            tuple(lineage),
            tuple(matched_keys),
        )


__all__ = ["MappedTable", "MappingOutcome", "ProfileMapper", "normalize_header"]
