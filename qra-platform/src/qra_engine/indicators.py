from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .errors import ValidationIssue
from .model_registry import MODEL_SPEC_ROOT


DEFAULT_CATALOG_PATH = MODEL_SPEC_ROOT / "qra_indicator_catalog_v1.json"
ALLOWED_QUALITY_GRADES = {"A", "B", "C", "D", "UNKNOWN"}


@dataclass(frozen=True, slots=True)
class IndicatorDefinition:
    indicator_id: str
    group_id: str
    group_name_zh: str
    scope: str
    roles: tuple[str, ...]
    mechanisms: tuple[str, ...]
    source_refs: tuple[str, ...]
    name_zh: str
    value_type: str
    unit: str | None
    requirement: str
    case_path: str | None


@dataclass(frozen=True, slots=True)
class IndicatorCatalog:
    catalog_id: str
    version: str
    status: str
    definitions: tuple[IndicatorDefinition, ...]

    @property
    def by_id(self) -> dict[str, IndicatorDefinition]:
        return {definition.indicator_id: definition for definition in self.definitions}


def load_indicator_catalog(path: Path = DEFAULT_CATALOG_PATH) -> IndicatorCatalog:
    payload = json.loads(path.read_text(encoding="utf-8"))
    columns = payload["field_columns"]
    definitions: list[IndicatorDefinition] = []
    for group in payload["groups"]:
        for raw_values in group["fields"]:
            values = dict(zip(columns, raw_values, strict=True))
            definitions.append(
                IndicatorDefinition(
                    indicator_id=f"{group['group_id']}.{values['field_id']}",
                    group_id=group["group_id"],
                    group_name_zh=group["name_zh"],
                    scope=group["scope"],
                    roles=tuple(group["roles"]),
                    mechanisms=tuple(group["mechanisms"]),
                    source_refs=tuple(group["source_refs"]),
                    name_zh=values["name_zh"],
                    value_type=values["value_type"],
                    unit=values["unit"],
                    requirement=values["requirement"],
                    case_path=values["case_path"],
                )
            )
    ids = [definition.indicator_id for definition in definitions]
    if len(ids) != len(set(ids)):
        raise ValueError("indicator catalog contains duplicate indicator IDs")
    return IndicatorCatalog(
        catalog_id=payload["catalog_id"],
        version=payload["version"],
        status=payload["status"],
        definitions=tuple(definitions),
    )


def _resolve_case_path(root: Any, path: str) -> list[Any]:
    values = [root]
    for token in path.split("."):
        next_values: list[Any] = []
        for value in values:
            if token == "*":
                if isinstance(value, list):
                    next_values.extend(value)
                elif isinstance(value, dict):
                    next_values.extend(value.values())
                continue
            if isinstance(value, dict) and token in value:
                next_values.append(value[token])
        values = next_values
        if not values:
            break
    return [value for value in values if value is not None]


def resolve_indicator_value(
    case: dict[str, Any],
    indicator_id: str,
    *,
    segment_id: str | None = None,
    catalog: IndicatorCatalog | None = None,
) -> Any:
    """Resolve one model input from explicit observations or a catalog case path."""
    catalog = catalog or load_indicator_catalog()
    definition = catalog.by_id.get(indicator_id)
    if definition is None:
        raise KeyError(f"指标ID未登记：{indicator_id}")

    indicator_set = case.get("engineering_indicators", {})
    if segment_id is not None:
        segment_observations = indicator_set.get("observations_by_segment", {}).get(
            segment_id, {}
        )
        if indicator_id in segment_observations:
            observation = segment_observations[indicator_id]
            return observation.get("value") if isinstance(observation, dict) else observation
        archetype_id = indicator_set.get("segment_archetype", {}).get(segment_id)
        archetype_observations = indicator_set.get(
            "observations_by_archetype", {}
        ).get(archetype_id, {})
        if indicator_id in archetype_observations:
            observation = archetype_observations[indicator_id]
            return observation.get("value") if isinstance(observation, dict) else observation

    global_observations = indicator_set.get("observations_global", {})
    if indicator_id in global_observations:
        observation = global_observations[indicator_id]
        return observation.get("value") if isinstance(observation, dict) else observation

    path = definition.case_path
    if not path:
        raise KeyError(f"指标{indicator_id}没有显式观测或直接数据路径")
    if path.startswith("segments.*."):
        if segment_id is None:
            raise ValueError(f"指标{indicator_id}需要segment_id")
        segment = next(
            (
                row
                for row in case.get("segments", [])
                if row.get("segment_id") == segment_id
            ),
            None,
        )
        if segment is None:
            raise KeyError(f"未知管段：{segment_id}")
        values = _resolve_case_path(segment, path.removeprefix("segments.*."))
    else:
        values = _resolve_case_path(case, path)
    if len(values) != 1:
        raise ValueError(
            f"指标{indicator_id}的数据路径应解析为一个值，当前为{len(values)}个"
        )
    return values[0]


def _expected_slots(definition: IndicatorDefinition, case: dict[str, Any]) -> int:
    if definition.case_path:
        if "segments.*" in definition.case_path:
            return len(case.get("segments", []))
        if "weather_joint_probability.*" in definition.case_path:
            return len(case.get("weather_joint_probability", []))
        if "population_cells.*" in definition.case_path:
            return len(case.get("population_cells", []))
        return 1
    scope = definition.scope
    if "segment" in scope:
        return len(case.get("segments", []))
    if scope == "population_cell":
        return len(case.get("population_cells", []))
    if "weather" in scope:
        return len(case.get("weather_joint_probability", []))
    return 1


def _explicit_observation_count(
    indicator_id: str,
    indicator_set: dict[str, Any],
) -> int:
    count = 1 if indicator_id in indicator_set.get("observations_global", {}) else 0
    count += sum(
        indicator_id in observations
        for observations in indicator_set.get("observations_by_segment", {}).values()
    )
    archetypes = indicator_set.get("observations_by_archetype", {})
    count += sum(
        indicator_id in archetypes.get(archetype_id, {})
        for archetype_id in indicator_set.get("segment_archetype", {}).values()
    )
    return count


def build_indicator_coverage(
    case: dict[str, Any],
    catalog: IndicatorCatalog | None = None,
) -> dict[str, Any]:
    catalog = catalog or load_indicator_catalog()
    indicator_set = case.get("engineering_indicators", {})
    group_stats: dict[str, dict[str, Any]] = {}
    missing_required: list[str] = []
    total_expected_slots = 0
    total_covered_slots = 0
    required_expected_slots = 0
    required_covered_slots = 0

    for definition in catalog.definitions:
        expected = _expected_slots(definition, case)
        mapped = (
            len(_resolve_case_path(case, definition.case_path))
            if definition.case_path
            else 0
        )
        explicit = _explicit_observation_count(
            definition.indicator_id,
            indicator_set,
        )
        covered = min(expected, max(mapped, explicit))
        total_expected_slots += expected
        total_covered_slots += covered
        if definition.requirement == "REQUIRED":
            required_expected_slots += expected
            required_covered_slots += covered
            if covered < expected:
                missing_required.append(definition.indicator_id)
        stats = group_stats.setdefault(
            definition.group_id,
            {
                "name_zh": definition.group_name_zh,
                "indicator_definition_count": 0,
                "expected_observation_slots": 0,
                "covered_observation_slots": 0,
            },
        )
        stats["indicator_definition_count"] += 1
        stats["expected_observation_slots"] += expected
        stats["covered_observation_slots"] += covered

    for stats in group_stats.values():
        expected = stats["expected_observation_slots"]
        stats["coverage_fraction"] = (
            stats["covered_observation_slots"] / expected if expected else 1.0
        )

    return {
        "catalog_id": catalog.catalog_id,
        "catalog_version": catalog.version,
        "catalog_status": catalog.status,
        "indicator_definition_count": len(catalog.definitions),
        "required_indicator_definition_count": sum(
            definition.requirement == "REQUIRED"
            for definition in catalog.definitions
        ),
        "expected_observation_slots": total_expected_slots,
        "covered_observation_slots": total_covered_slots,
        "coverage_fraction": (
            total_covered_slots / total_expected_slots
            if total_expected_slots
            else 1.0
        ),
        "required_expected_observation_slots": required_expected_slots,
        "required_covered_observation_slots": required_covered_slots,
        "required_coverage_fraction": (
            required_covered_slots / required_expected_slots
            if required_expected_slots
            else 1.0
        ),
        "missing_required_indicator_ids": missing_required,
        "group_coverage": group_stats,
        "method_note": (
            "覆盖率统计原始数据和证据是否存在，不代表指标已经被赋权。"
            "指标只有通过已批准的频率修正模型才能改变失效频率。"
        ),
    }


def _valid_observation_value(value: Any, value_type: str) -> bool:
    if value_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if value_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type == "boolean":
        return isinstance(value, bool)
    if value_type in {"string", "date"}:
        return isinstance(value, str) and bool(value)
    if value_type == "array":
        return isinstance(value, list)
    return value is not None


def _iter_explicit_observations(
    indicator_set: dict[str, Any],
) -> Iterable[tuple[str, str, Any]]:
    for indicator_id, observation in indicator_set.get(
        "observations_global", {}
    ).items():
        yield f"engineering_indicators.observations_global.{indicator_id}", indicator_id, observation
    for segment_id, observations in indicator_set.get(
        "observations_by_segment", {}
    ).items():
        for indicator_id, observation in observations.items():
            yield (
                f"engineering_indicators.observations_by_segment.{segment_id}.{indicator_id}",
                indicator_id,
                observation,
            )
    for archetype_id, observations in indicator_set.get(
        "observations_by_archetype", {}
    ).items():
        for indicator_id, observation in observations.items():
            yield (
                f"engineering_indicators.observations_by_archetype.{archetype_id}.{indicator_id}",
                indicator_id,
                observation,
            )


def validate_indicator_set(
    case: dict[str, Any],
    catalog: IndicatorCatalog | None = None,
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    catalog = catalog or load_indicator_catalog()
    indicator_set = case.get("engineering_indicators")
    issues: list[ValidationIssue] = []
    if not isinstance(indicator_set, dict):
        coverage = build_indicator_coverage(case, catalog)
        issues.append(
            ValidationIssue(
                "ENGINEERING_INDICATOR_SET_MISSING",
                "engineering_indicators",
                "缺少标准化工程指标集；只允许试算，不能签发正式QRA报告",
                "WARNING",
            )
        )
        return issues, coverage

    if indicator_set.get("catalog_id") != catalog.catalog_id:
        issues.append(
            ValidationIssue(
                "INDICATOR_CATALOG_ID_MISMATCH",
                "engineering_indicators.catalog_id",
                f"工程指标目录必须为{catalog.catalog_id}",
            )
        )
    if indicator_set.get("catalog_version") != catalog.version:
        issues.append(
            ValidationIssue(
                "INDICATOR_CATALOG_VERSION_MISMATCH",
                "engineering_indicators.catalog_version",
                f"工程指标目录版本必须为{catalog.version}",
            )
        )

    segment_ids = {segment["segment_id"] for segment in case.get("segments", [])}
    supplied_segment_ids = set(indicator_set.get("observations_by_segment", {}))
    unknown_segments = sorted(supplied_segment_ids - segment_ids)
    if unknown_segments:
        issues.append(
            ValidationIssue(
                "INDICATOR_SEGMENT_UNKNOWN",
                "engineering_indicators.observations_by_segment",
                f"指标观测引用未知管段：{', '.join(unknown_segments)}",
            )
        )
    archetypes = set(indicator_set.get("observations_by_archetype", {}))
    archetype_map = indicator_set.get("segment_archetype", {})
    unknown_archetype_segments = sorted(set(archetype_map) - segment_ids)
    unknown_archetype_ids = sorted(set(archetype_map.values()) - archetypes)
    if unknown_archetype_segments:
        issues.append(
            ValidationIssue(
                "INDICATOR_ARCHETYPE_SEGMENT_UNKNOWN",
                "engineering_indicators.segment_archetype",
                f"指标原型映射引用未知管段：{', '.join(unknown_archetype_segments)}",
            )
        )
    if unknown_archetype_ids:
        issues.append(
            ValidationIssue(
                "INDICATOR_ARCHETYPE_UNKNOWN",
                "engineering_indicators.segment_archetype",
                f"指标原型未定义：{', '.join(unknown_archetype_ids)}",
            )
        )

    definitions = catalog.by_id
    for path, indicator_id, observation in _iter_explicit_observations(indicator_set):
        definition = definitions.get(indicator_id)
        if definition is None:
            issues.append(
                ValidationIssue(
                    "INDICATOR_ID_UNKNOWN",
                    path,
                    f"指标ID未在目录登记：{indicator_id}",
                )
            )
            continue
        if not isinstance(observation, dict) or "value" not in observation:
            issues.append(
                ValidationIssue(
                    "INDICATOR_OBSERVATION_INVALID",
                    path,
                    "指标观测必须是包含value的对象",
                )
            )
            continue
        if not _valid_observation_value(observation["value"], definition.value_type):
            issues.append(
                ValidationIssue(
                    "INDICATOR_VALUE_TYPE_INVALID",
                    f"{path}.value",
                    f"{indicator_id}要求{definition.value_type}类型",
                )
            )
        quality = observation.get("quality", "UNKNOWN")
        if quality not in ALLOWED_QUALITY_GRADES:
            issues.append(
                ValidationIssue(
                    "INDICATOR_QUALITY_INVALID",
                    f"{path}.quality",
                    f"数据质量等级必须属于{sorted(ALLOWED_QUALITY_GRADES)}",
                )
            )
        for evidence_field in ("source_ref", "as_of"):
            if not observation.get(evidence_field):
                issues.append(
                    ValidationIssue(
                        "INDICATOR_EVIDENCE_MISSING",
                        f"{path}.{evidence_field}",
                        f"显式指标观测缺少{evidence_field}",
                        "WARNING",
                    )
                )

    coverage = build_indicator_coverage(case, catalog)
    if coverage["missing_required_indicator_ids"]:
        issues.append(
            ValidationIssue(
                "REQUIRED_ENGINEERING_INDICATORS_INCOMPLETE",
                "engineering_indicators",
                (
                    "正式QRA必需指标不完整："
                    f"{len(coverage['missing_required_indicator_ids'])}个定义存在缺口，"
                    f"必需观测槽覆盖率为{coverage['required_coverage_fraction']:.1%}"
                ),
                "WARNING",
            )
        )
    return issues, coverage


__all__ = [
    "DEFAULT_CATALOG_PATH",
    "IndicatorCatalog",
    "IndicatorDefinition",
    "build_indicator_coverage",
    "load_indicator_catalog",
    "resolve_indicator_value",
    "validate_indicator_set",
]
