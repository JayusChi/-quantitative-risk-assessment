from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

from .errors import InputValidationError, ValidationIssue
from .frequency_correction import resolve_segment_correction_factors
from .gas_properties import calculate_gas_mixture_properties
from .indicators import validate_indicator_set


TOLERANCE = 1e-12


@dataclass(frozen=True, slots=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]
    formal_report_allowed: bool

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "ERROR")

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "WARNING")

    @property
    def status(self) -> str:
        if self.errors:
            return "BLOCK"
        if self.warnings:
            return "PASS_WITH_WARNING"
        return "PASS"

    def raise_for_errors(self) -> None:
        if self.errors:
            raise InputValidationError(list(self.errors))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "formal_report_allowed": self.formal_report_allowed,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _is_probability(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
    )


def _is_finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _normalization_issue(code: str, path: str, values: Iterable[float], label: str) -> ValidationIssue | None:
    total = sum(values)
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=TOLERANCE):
        return ValidationIssue(code, path, f"{label}之和必须为1，当前为{total:.15g}")
    return None


def validate_case(case: dict[str, Any]) -> ValidationReport:
    issues: list[ValidationIssue] = []

    required_sections = (
        "metadata",
        "assessment",
        "pipeline",
        "segments",
        "frequency_library",
        "weather_joint_probability",
        "population_cells",
        "ignition_model",
    )
    for section in required_sections:
        if section not in case:
            issues.append(ValidationIssue("REQUIRED_SECTION_MISSING", section, f"缺少必填数据段：{section}"))
    if issues:
        return ValidationReport(tuple(issues), formal_report_allowed=False)

    expected_types: dict[str, type] = {
        "metadata": dict,
        "assessment": dict,
        "pipeline": dict,
        "segments": list,
        "frequency_library": dict,
        "weather_joint_probability": list,
        "population_cells": list,
        "ignition_model": dict,
    }
    for section, expected_type in expected_types.items():
        if not isinstance(case[section], expected_type):
            issues.append(
                ValidationIssue(
                    "SECTION_TYPE_INVALID",
                    section,
                    f"{section}必须为{'数组' if expected_type is list else '对象'}",
                )
            )
    if issues:
        return ValidationReport(tuple(issues), formal_report_allowed=False)

    pipeline = case["pipeline"]
    gas = pipeline.get("gas_composition_mole_fraction", {})
    gas_values = list(gas.values())
    if not gas_values or not all(_is_probability(value) for value in gas_values):
        issues.append(ValidationIssue("GAS_COMPOSITION_INVALID", "pipeline.gas_composition_mole_fraction", "天然气组分必须全部位于[0,1]"))
    else:
        issue = _normalization_issue(
            "GAS_COMPOSITION_NOT_NORMALIZED",
            "pipeline.gas_composition_mole_fraction",
            gas_values,
            "天然气摩尔组分",
        )
        if issue:
            issues.append(issue)
        else:
            try:
                calculate_gas_mixture_properties(gas)
            except ValueError as exc:
                issues.append(
                    ValidationIssue(
                        "GAS_COMPOSITION_UNSUPPORTED",
                        "pipeline.gas_composition_mole_fraction",
                        str(exc),
                    )
                )

    design_pressure = pipeline.get("design_pressure_mpa")
    operating_pressure = pipeline.get("operating_pressure_mpa")
    if not _is_finite_number(design_pressure) or design_pressure <= 0:
        issues.append(ValidationIssue("DESIGN_PRESSURE_INVALID", "pipeline.design_pressure_mpa", "设计压力必须大于0"))
    if not _is_finite_number(operating_pressure) or operating_pressure <= 0:
        issues.append(ValidationIssue("OPERATING_PRESSURE_INVALID", "pipeline.operating_pressure_mpa", "运行压力必须大于0"))
    elif _is_finite_number(design_pressure) and operating_pressure > design_pressure:
        issues.append(ValidationIssue("OPERATING_PRESSURE_EXCEEDS_DESIGN", "pipeline.operating_pressure_mpa", "运行压力不得高于设计压力"))

    segments = case["segments"]
    if not isinstance(segments, list) or not segments:
        issues.append(ValidationIssue("SEGMENTS_EMPTY", "segments", "至少需要一个计算区段"))
    else:
        seen_ids: set[str] = set()
        length_sum = 0.0
        previous_end: float | None = None
        for index, segment in enumerate(segments):
            path = f"segments[{index}]"
            if not isinstance(segment, dict):
                issues.append(
                    ValidationIssue(
                        "SEGMENT_TYPE_INVALID", path, "每个区段必须是JSON对象"
                    )
                )
                continue
            segment_id = segment.get("segment_id")
            if not segment_id or segment_id in seen_ids:
                issues.append(ValidationIssue("SEGMENT_ID_INVALID", f"{path}.segment_id", "区段ID缺失或重复"))
            else:
                seen_ids.add(segment_id)
            length = segment.get("length_km")
            start = segment.get("start_km")
            end = segment.get("end_km")
            if not _is_finite_number(length) or length <= 0:
                issues.append(ValidationIssue("SEGMENT_LENGTH_INVALID", f"{path}.length_km", "区段长度必须大于0"))
                continue
            length_sum += length
            if not all(_is_finite_number(value) for value in (start, end)) or end <= start:
                issues.append(ValidationIssue("SEGMENT_CHAINAGE_INVALID", path, "区段起止里程无效"))
            elif not math.isclose(end - start, length, rel_tol=0.0, abs_tol=1e-9):
                issues.append(ValidationIssue("SEGMENT_LENGTH_MISMATCH", path, "区段长度与起止里程不一致"))
            if previous_end is not None and _is_finite_number(start) and not math.isclose(start, previous_end, rel_tol=0.0, abs_tol=1e-9):
                issues.append(ValidationIssue("SEGMENT_NOT_CONTIGUOUS", f"{path}.start_km", "区段里程不连续"))
            previous_end = end if _is_finite_number(end) else previous_end
        total_length = pipeline.get("total_length_km")
        if _is_finite_number(total_length) and not math.isclose(length_sum, total_length, rel_tol=0.0, abs_tol=1e-9):
            issues.append(ValidationIssue("SEGMENT_LENGTH_SUM_MISMATCH", "segments", f"区段长度合计{length_sum} km与管线总长{total_length} km不一致"))

    frequency_library = case["frequency_library"]
    base_frequency = frequency_library.get("base_frequency_by_mechanism", {})
    loc_fractions = frequency_library.get("loc_fraction_by_mechanism", {})
    if frequency_library.get("unit") != "per_km_year":
        issues.append(ValidationIssue("FREQUENCY_UNIT_UNSUPPORTED", "frequency_library.unit", "首版只接受per_km_year"))
    for mechanism, base in base_frequency.items():
        if not _is_finite_number(base) or base < 0:
            issues.append(ValidationIssue("BASE_FREQUENCY_INVALID", f"frequency_library.base_frequency_by_mechanism.{mechanism}", "基准频率必须为非负有限数"))
        fractions = loc_fractions.get(mechanism)
        if not isinstance(fractions, dict) or not fractions:
            issues.append(ValidationIssue("LOC_FRACTION_MISSING", f"frequency_library.loc_fraction_by_mechanism.{mechanism}", "缺少孔径比例"))
            continue
        if not all(_is_probability(value) for value in fractions.values()):
            issues.append(ValidationIssue("LOC_FRACTION_INVALID", f"frequency_library.loc_fraction_by_mechanism.{mechanism}", "孔径比例必须位于[0,1]"))
            continue
        issue = _normalization_issue(
            "LOC_FRACTION_NOT_NORMALIZED",
            f"frequency_library.loc_fraction_by_mechanism.{mechanism}",
            fractions.values(),
            f"{mechanism}孔径比例",
        )
        if issue:
            issues.append(issue)

    try:
        correction_resolution = resolve_segment_correction_factors(case)
        correction = correction_resolution.factors_by_segment
    except (KeyError, TypeError, ValueError) as exc:
        issues.append(
            ValidationIssue(
                "FREQUENCY_CORRECTION_MODEL_INVALID",
                "frequency_correction_model",
                str(exc),
            )
        )
        correction_resolution = None
        correction = {}
    for segment in segments:
        segment_id = segment.get("segment_id")
        factors = correction.get(segment_id, {})
        for mechanism in base_frequency:
            value = factors.get(mechanism)
            if not _is_finite_number(value) or value < 0:
                issues.append(ValidationIssue("CORRECTION_FACTOR_INVALID", f"segment_correction_factor.{segment_id}.{mechanism}", "区段修正系数必须为非负有限数"))

    if (
        correction_resolution is not None
        and not correction_resolution.diagnostics["approved_for_formal_qra"]
    ):
        issues.append(
            ValidationIssue(
                "FREQUENCY_CORRECTION_MODEL_NOT_APPROVED",
                "frequency_correction_model.status",
                "失效频率修正模型未批准，只允许试算",
                "WARNING",
            )
        )

    weather = case["weather_joint_probability"]
    weather_probabilities = [
        row.get("probability") if isinstance(row, dict) else None for row in weather
    ]
    if not weather or not all(_is_probability(value) for value in weather_probabilities):
        issues.append(ValidationIssue("WEATHER_PROBABILITY_INVALID", "weather_joint_probability", "气象联合概率必须位于[0,1]"))
    else:
        issue = _normalization_issue(
            "WEATHER_PROBABILITY_NOT_NORMALIZED",
            "weather_joint_probability",
            weather_probabilities,
            "气象联合概率",
        )
        if issue:
            issues.append(issue)

    ignition = case["ignition_model"]
    ignition_probabilities: list[tuple[str, Any]] = []
    for activity, rows in ignition.get("immediate_ignition_probability", {}).items():
        for index, row in enumerate(rows):
            ignition_probabilities.append((f"ignition_model.immediate_ignition_probability.{activity}[{index}].probability", row.get("probability")))
    for field in ("delayed_ignition_test_probability", "vce_given_delayed_test_probability"):
        for activity, value in ignition.get(field, {}).items():
            ignition_probabilities.append((f"ignition_model.{field}.{activity}", value))
    for path, probability in ignition_probabilities:
        if not _is_probability(probability):
            issues.append(ValidationIssue("IGNITION_PROBABILITY_INVALID", path, "点火事件树概率必须位于[0,1]"))

    for index, cell in enumerate(case["population_cells"]):
        if not isinstance(cell, dict):
            issues.append(
                ValidationIssue(
                    "POPULATION_CELL_TYPE_INVALID",
                    f"population_cells[{index}]",
                    "人口受体必须是JSON对象",
                )
            )
            continue
        for period in ("population_day", "population_night"):
            value = cell.get(period)
            if not _is_finite_number(value) or value < 0:
                issues.append(ValidationIssue("POPULATION_INVALID", f"population_cells[{index}].{period}", "人口必须为非负有限数"))
        for period in ("day", "night"):
            field = f"outdoor_fraction_{period}"
            if field in cell and not _is_probability(cell[field]):
                issues.append(
                    ValidationIssue(
                        "OUTDOOR_FRACTION_INVALID",
                        f"population_cells[{index}].{field}",
                        "室外人口比例必须位于[0,1]",
                    )
                )

    indicator_issues, indicator_coverage = validate_indicator_set(case)
    issues.extend(indicator_issues)

    criteria = case["assessment"].get("criteria_set_by_domain", {})
    for domain in case["assessment"].get("enabled_consequence_domains", []):
        if criteria.get(domain) is None:
            issues.append(ValidationIssue("RISK_CRITERIA_MISSING", f"assessment.criteria_set_by_domain.{domain}", f"{domain}域可计算但不能进行可接受性判定", "WARNING"))

    metadata = case["metadata"]
    synthetic_sources = (
        metadata.get("data_classification") == "SYNTHETIC_TEST_ONLY"
        or frequency_library.get("data_classification") == "SYNTHETIC_TEST_ONLY"
        or case.get("damage_model", {}).get("status") == "SYNTHETIC_TEST_ONLY"
        or ignition.get("model_status") == "TEST_PARAMETER_SET"
    )
    if metadata.get("run_profile") == "strict_standard" and synthetic_sources:
        issues.append(ValidationIssue("SYNTHETIC_MODEL_IN_STRICT_RUN", "metadata.run_profile", "合成模型或参数不能用于strict_standard运行"))

    formal_report_allowed = (
        not synthetic_sources
        and not any(issue.severity == "ERROR" for issue in issues)
        and indicator_coverage["required_coverage_fraction"] == 1.0
        and correction_resolution is not None
        and correction_resolution.diagnostics["approved_for_formal_qra"]
    )
    return ValidationReport(tuple(issues), formal_report_allowed=formal_report_allowed)


def validate_import_contract(case: Any) -> ValidationReport:
    """Validate the shared minimum contract used by preview and import APIs."""
    issues: list[ValidationIssue] = []
    if not isinstance(case, dict):
        return ValidationReport(
            (
                ValidationIssue(
                    "ROOT_TYPE_INVALID", "$", "输入JSON顶层必须是对象"
                ),
            ),
            formal_report_allowed=False,
        )

    for section in ("metadata", "pipeline", "segments"):
        if section not in case:
            issues.append(
                ValidationIssue(
                    "IMPORT_SECTION_MISSING", section, f"导入数据缺少必填段：{section}"
                )
            )
    if issues:
        return ValidationReport(tuple(issues), formal_report_allowed=False)

    metadata = case.get("metadata")
    pipeline = case.get("pipeline")
    segments = case.get("segments")
    if not isinstance(metadata, dict):
        issues.append(ValidationIssue("SECTION_TYPE_INVALID", "metadata", "metadata必须是对象"))
    elif not str(metadata.get("case_id") or metadata.get("project_name") or "").strip():
        issues.append(
            ValidationIssue(
                "CASE_IDENTITY_MISSING",
                "metadata.case_id",
                "metadata必须提供case_id或project_name",
            )
        )
    if not isinstance(pipeline, dict):
        issues.append(ValidationIssue("SECTION_TYPE_INVALID", "pipeline", "pipeline必须是对象"))
    if not isinstance(segments, list):
        issues.append(ValidationIssue("SECTION_TYPE_INVALID", "segments", "segments必须是数组"))
        segments = []
    if not segments:
        issues.append(ValidationIssue("SEGMENTS_EMPTY", "segments", "至少需要一个计算区段"))

    seen_ids: set[str] = set()
    length_sum = 0.0
    for index, segment in enumerate(segments):
        path = f"segments[{index}]"
        if not isinstance(segment, dict):
            issues.append(ValidationIssue("SEGMENT_TYPE_INVALID", path, "每个区段必须是JSON对象"))
            continue
        segment_id = segment.get("segment_id")
        if not isinstance(segment_id, str) or not segment_id.strip():
            issues.append(ValidationIssue("SEGMENT_ID_MISSING", f"{path}.segment_id", "区段ID不能为空"))
        elif segment_id in seen_ids:
            issues.append(ValidationIssue("SEGMENT_ID_DUPLICATE", f"{path}.segment_id", f"区段ID重复：{segment_id}"))
        else:
            seen_ids.add(segment_id)
        start = segment.get("start_km")
        end = segment.get("end_km")
        length = segment.get("length_km")
        if not _is_finite_number(start) or not _is_finite_number(end) or end <= start:
            issues.append(ValidationIssue("SEGMENT_CHAINAGE_INVALID", path, "区段起止里程必须为有限数且end_km大于start_km"))
        if not _is_finite_number(length) or length <= 0.0:
            issues.append(ValidationIssue("SEGMENT_LENGTH_INVALID", f"{path}.length_km", "区段长度必须为大于0的有限数"))
        else:
            length_sum += float(length)
            if _is_finite_number(start) and _is_finite_number(end) and not math.isclose(
                float(end) - float(start), float(length), rel_tol=0.0, abs_tol=1.0e-9
            ):
                issues.append(ValidationIssue("SEGMENT_LENGTH_MISMATCH", path, "区段长度与起止里程不一致"))

    if isinstance(pipeline, dict):
        total_length = pipeline.get("total_length_km")
        if total_length is not None:
            if not _is_finite_number(total_length) or total_length <= 0.0:
                issues.append(ValidationIssue("PIPELINE_LENGTH_INVALID", "pipeline.total_length_km", "管线总长必须为大于0的有限数"))
            elif segments and not math.isclose(length_sum, float(total_length), rel_tol=0.0, abs_tol=1.0e-9):
                issues.append(ValidationIssue("SEGMENT_LENGTH_SUM_MISMATCH", "segments", f"区段长度合计{length_sum} km与管线总长{total_length} km不一致"))
        composition = pipeline.get("gas_composition_mole_fraction")
        if composition is not None:
            try:
                calculate_gas_mixture_properties(composition)
            except ValueError as exc:
                issues.append(ValidationIssue("GAS_COMPOSITION_UNSUPPORTED", "pipeline.gas_composition_mole_fraction", str(exc)))

    frequency_library = case.get("frequency_library")
    if frequency_library is not None:
        if not isinstance(frequency_library, dict):
            issues.append(ValidationIssue("SECTION_TYPE_INVALID", "frequency_library", "frequency_library必须是对象"))
        elif frequency_library.get("unit") != "per_km_year":
            issues.append(ValidationIssue("FREQUENCY_UNIT_UNSUPPORTED", "frequency_library.unit", "失效频率单位必须为per_km_year"))

    category_manifest = case.get("data_category_manifest")
    if category_manifest is not None:
        if not isinstance(category_manifest, dict):
            issues.append(ValidationIssue("CATEGORY_MANIFEST_INVALID", "data_category_manifest", "数据类别清单必须是对象"))
        else:
            categories = category_manifest.get("categories")
            if not isinstance(categories, list) or not categories:
                issues.append(ValidationIssue("CATEGORY_MANIFEST_INVALID", "data_category_manifest.categories", "数据类别清单必须是非空数组"))
            else:
                category_ids = [row.get("category_id") if isinstance(row, dict) else None for row in categories]
                if any(not isinstance(category_id, str) or not category_id for category_id in category_ids):
                    issues.append(ValidationIssue("CATEGORY_ID_INVALID", "data_category_manifest.categories", "每个数据类别必须有category_id"))
                elif len(set(category_ids)) != len(category_ids):
                    issues.append(ValidationIssue("CATEGORY_ID_DUPLICATE", "data_category_manifest.categories", "数据类别ID不得重复"))
                declared_count = category_manifest.get("category_count")
                if declared_count is not None and declared_count != len(categories):
                    issues.append(ValidationIssue("CATEGORY_COUNT_MISMATCH", "data_category_manifest.category_count", f"category_count应为{len(categories)}"))

    full_sections = {
        "metadata",
        "assessment",
        "pipeline",
        "segments",
        "frequency_library",
        "weather_joint_probability",
        "population_cells",
        "ignition_model",
    }
    if full_sections.issubset(case):
        full_report = validate_case(case)
        existing = {(issue.code, issue.path) for issue in issues}
        issues.extend(
            issue
            for issue in full_report.errors
            if (issue.code, issue.path) not in existing
        )

    return ValidationReport(tuple(issues), formal_report_allowed=False)
