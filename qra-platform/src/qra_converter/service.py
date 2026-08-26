from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .assembly import assemble_case
from .contracts import (
    ConversionIssue,
    ConversionResult,
    IssueSeverity,
    RawTable,
    SourceReference,
)
from .mapping import ProfileMapper, load_profile
from .readers import ReaderRegistry
from .reporting import write_conversion_outputs
from .review import auxiliary_review_items, load_review_bundle, merge_mapped_tables
from .validation import validate_conversion_quality

CONVERTER_VERSION = "0.2.0"
ContractValidator = Callable[[Any], Any]
CapabilityPlanner = Callable[[dict[str, Any]], dict[str, Any]]


def discover_sources(source_dir: Path, registry: ReaderRegistry) -> tuple[Path, ...]:
    root = source_dir.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"源目录不存在：{source_dir}")
    return tuple(
        sorted(
            (
                path
                for path in root.rglob("*")
                if path.is_file() and path.suffix.lower() in registry.supported_suffixes
            ),
            key=lambda path: path.relative_to(root).as_posix().casefold(),
        )
    )


def _contract_issues(report: Any) -> tuple[list[ConversionIssue], str | None]:
    if report is None:
        return [], None
    status = str(getattr(report, "status", "UNKNOWN"))
    converted = []
    for issue in getattr(report, "issues", ()):
        severity_text = str(getattr(issue, "severity", "ERROR"))
        try:
            severity = IssueSeverity(severity_text)
        except ValueError:
            severity = IssueSeverity.ERROR
        converted.append(
            ConversionIssue(
                severity,
                f"IMPORT_CONTRACT.{getattr(issue, 'code', 'INVALID')}",
                str(getattr(issue, "message", "输入合同预检失败")),
                target_path=str(getattr(issue, "path", "$")),
            )
        )
    return converted, status


def _relocate_tables(
    tables: tuple[RawTable, ...], path: Path, source_root: Path
) -> tuple[RawTable, ...]:
    relative_path = path.resolve().relative_to(source_root.resolve()).as_posix()
    relocated: list[RawTable] = []
    for table in tables:
        source = table.source
        source_id = hashlib.sha256(
            f"{source.checksum_sha256}\0{relative_path}".encode()
        ).hexdigest()
        relocated.append(
            replace(
                table,
                source=replace(
                    source,
                    source_id=source_id,
                    source_path=relative_path,
                ),
            )
        )
    return tuple(relocated)


def convert_sources(
    *,
    source_dir: Path,
    profile_path: Path,
    output_dir: Path,
    case_id: str | None = None,
    project_name: str | None = None,
    contract_validator: ContractValidator | None = None,
    capability_planner: CapabilityPlanner | None = None,
    review_decisions_path: Path | None = None,
) -> dict[str, Any]:
    registry = ReaderRegistry()
    profile = load_profile(profile_path)
    review_bundle = load_review_bundle(review_decisions_path)
    paths = discover_sources(source_dir, registry)
    raw_tables: list[RawTable] = []
    issues: list[ConversionIssue] = []
    if not paths:
        issues.append(
            ConversionIssue(
                IssueSeverity.ERROR,
                "SOURCE_FILES_EMPTY",
                "源目录中没有XLS、XLSX、CSV、DOCX或PDF文件",
            )
        )
    for path in paths:
        try:
            tables = tuple(registry.read(path))
            raw_tables.extend(_relocate_tables(tables, path, source_dir))
        except (OSError, RuntimeError, ValueError) as exc:
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    "SOURCE_READ_FAILED",
                    f"读取{path.name}失败：{exc}",
                    location=path.name,
                )
            )
    outcome = ProfileMapper(profile).map(raw_tables)
    issues.extend(outcome.issues)
    merge_result = merge_mapped_tables(outcome, review_bundle)
    issues.extend(merge_result.issues)
    auxiliary_items, auxiliary_audit, auxiliary_used = auxiliary_review_items(
        raw_tables, outcome.matched_table_keys, review_bundle
    )
    review_items = (*merge_result.review_items, *auxiliary_items)
    review_audit = (*merge_result.audit, *auxiliary_audit)
    used_review_ids = set(merge_result.used_review_ids) | set(auxiliary_used)
    for item in review_items:
        issues.append(
            ConversionIssue(
                IssueSeverity.WARNING,
                "MANUAL_REVIEW_PENDING",
                item.reason,
                target_path=item.target_path,
            )
        )
    for review_id in sorted(set(review_bundle.decisions) - used_review_ids):
        issues.append(
            ConversionIssue(
                IssueSeverity.ERROR,
                "REVIEW_DECISION_STALE",
                f"复核决定未匹配当前转换结果：{review_id}",
                target_path="review_decisions",
            )
        )
    case, assembly_issues = assemble_case(
        merge_result.outcome,
        case_id=case_id,
        project_name=project_name,
        fallback_project_name=source_dir.resolve().name,
    )
    issues.extend(assembly_issues)
    issues.extend(validate_conversion_quality(case))
    capability_plan: dict[str, Any] | None = None
    if capability_planner is not None:
        try:
            capability_plan = capability_planner(case)
        except Exception as exc:  # planner boundary must not hide conversion output
            issues.append(
                ConversionIssue(
                    IssueSeverity.WARNING,
                    "CAPABILITY_PLAN_FAILED",
                    f"计算能力盘点失败：{exc}",
                    target_path="$",
                )
            )
    contract_status: str | None = None
    if contract_validator is not None:
        try:
            contract_report = contract_validator(case)
        except Exception as exc:  # validator boundary must become a report issue
            issues.append(
                ConversionIssue(
                    IssueSeverity.ERROR,
                    "IMPORT_CONTRACT_VALIDATOR_FAILED",
                    f"现有输入合同校验器执行失败：{exc}",
                    target_path="$",
                )
            )
            contract_status = "BLOCK"
        else:
            converted, contract_status = _contract_issues(contract_report)
            issues.extend(converted)
    unique_sources: dict[str, SourceReference] = {}
    for table in raw_tables:
        unique_sources[table.source.source_id] = table.source
    result = ConversionResult(
        payload=case,
        mapping_version=f"{profile.profile_id}/{profile.version}",
        sources=tuple(
            sorted(
                unique_sources.values(),
                key=lambda source: (
                    source.source_path.casefold(),
                    source.checksum_sha256,
                ),
            )
        ),
        issues=tuple(issues),
        lineage=outcome.lineage,
        contract_status=contract_status,
        review_items=tuple(review_items),
        review_audit=tuple(review_audit),
        review_decision_source=review_bundle.source,
        capability_plan=capability_plan,
    )
    return write_conversion_outputs(
        output_dir,
        result,
        tuple(raw_tables),
        profile,
        CONVERTER_VERSION,
        outcome,
    )


__all__ = ["CONVERTER_VERSION", "convert_sources", "discover_sources"]
