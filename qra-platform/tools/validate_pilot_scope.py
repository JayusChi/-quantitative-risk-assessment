from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_PILOT_ID = "jiujiang-qra-screening-pilot-v1"
SCREENING_REPORT_TIER = "EVIDENCE_CONDITIONED_SCREENING_ESTIMATE"
MISSING_SOURCE_STATUSES = {"MISSING", "UNSUPPORTED"}
SYNTHETIC_CLASSIFICATIONS = {
    "SYNTHETIC_TEST_ONLY",
    "SANITIZED_SHAPE_SAMPLE",
    "FIXTURE_GOLDEN",
}
REAL_DATA_STATUSES = {
    "REAL_PROJECT_DATA",
    "REAL_PROJECT_CONVERTED_SOURCE_WITH_EXPLICIT_GAPS",
    "VERIFIED_REAL_PROJECT_DATA",
}
FALSE_VALUES = {"", "0", "false", "no", "n"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON根节点必须是对象：{path}")
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return [dict(row) for row in csv.DictReader(stream)]


def _split(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split("|") if item.strip()]


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in FALSE_VALUES


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _issue(code: str, message: str, location: str = "") -> dict[str, str]:
    return {"code": code, "message": message, "location": location}


def _load_dynamic_nodes(repo_root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    src_root = repo_root / "src"
    inserted = False
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
        inserted = True
    try:
        from qra_engine.dynamic import dynamic_node_catalog

        catalog = dynamic_node_catalog()
    finally:
        if inserted:
            sys.path.remove(str(src_root))
    nodes = {str(row["node_id"]): row for row in catalog.get("nodes", [])}
    return nodes, catalog


def _find_mapping_profile(
    repo_root: Path, profile_id: str
) -> tuple[Path | None, dict[str, Any] | None]:
    for path in sorted((repo_root / "resources" / "mappings").rglob("*.json")):
        try:
            value = _read_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if str(value.get("profile_id", "")) == profile_id:
            return path, value
    return None, None


def _find_contract(
    repo_root: Path, contract_id: str
) -> tuple[Path | None, dict[str, Any] | None]:
    root = repo_root / "resources" / "contracts"
    for path in sorted(root.rglob("manifest.json")):
        try:
            value = _read_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if str(value.get("contract_id", "")) == contract_id:
            return path, value
    return None, None


def _source_is_available(row: dict[str, str]) -> bool:
    return str(row.get("current_status", "")).strip().upper() not in MISSING_SOURCE_STATUSES


def _field_is_available(row: dict[str, str]) -> bool:
    return str(row.get("current_availability", "")).strip().upper().startswith("AVAILABLE")


def _gap_is_closed(row: dict[str, str]) -> bool:
    status = str(row.get("status", "")).strip().upper()
    return status.startswith(("CLOSED", "RESOLVED", "ACCEPTED"))


def _prohibited_real_data_matches(
    repo_root: Path,
    sources: Iterable[dict[str, str]],
) -> list[str]:
    real_hashes = {
        str(row.get("sha256", "")).strip().lower()
        for row in sources
        if str(row.get("data_classification", "")).startswith("REAL_PROJECT")
        and SHA256_RE.fullmatch(str(row.get("sha256", "")).strip().lower())
    }
    if not real_hashes:
        return []
    matches: list[str] = []
    for relative_root in ("tests", "resources", "docs"):
        root = repo_root / relative_root
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                if _sha256_file(path) in real_hashes:
                    matches.append(path.relative_to(repo_root).as_posix())
            except OSError:
                continue
    return matches


def validate_pilot_scope(
    pilot_dir: Path,
    *,
    repo_root: Path,
    verify_source_files: bool = True,
) -> dict[str, Any]:
    pilot_dir = pilot_dir.resolve()
    repo_root = repo_root.resolve()
    manifest_path = pilot_dir / "pilot-manifest.json"
    inventory_path = pilot_dir / "source-inventory.csv"
    matrix_path = pilot_dir / "field-node-matrix.csv"
    gaps_path = pilot_dir / "data-gap-register.csv"
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    required_files = (manifest_path, inventory_path, matrix_path, gaps_path)
    missing_files = [str(path) for path in required_files if not path.is_file()]
    if missing_files:
        return {
            "status": "BLOCKED",
            "validation_status": "FAIL",
            "validation_errors": [
                _issue("PILOT_FILE_MISSING", "试点定义文件不存在", path)
                for path in missing_files
            ],
            "validation_warnings": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    try:
        manifest = _read_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "BLOCKED",
            "validation_status": "FAIL",
            "validation_errors": [
                _issue("MANIFEST_INVALID_JSON", str(exc), manifest_path.name)
            ],
            "validation_warnings": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    inventory = _read_csv(inventory_path)
    matrix = _read_csv(matrix_path)
    gaps = _read_csv(gaps_path)

    pilot_id = str(manifest.get("pilot_id", "")).strip()
    version = str(manifest.get("version", "")).strip()
    if not pilot_id:
        errors.append(_issue("PILOT_ID_MISSING", "pilot_id不能为空", manifest_path.name))
    if not version:
        errors.append(_issue("PILOT_VERSION_MISSING", "version不能为空", manifest_path.name))

    mapping_id = str(manifest.get("mapping_profile_id", "")).strip()
    mapping_version = str(manifest.get("mapping_profile_version", "")).strip()
    mapping_path, mapping = _find_mapping_profile(repo_root, mapping_id)
    if mapping is None:
        errors.append(
            _issue("MAPPING_PROFILE_NOT_FOUND", f"映射配置不存在：{mapping_id}", manifest_path.name)
        )
    elif str(mapping.get("version", "")) != mapping_version:
        errors.append(
            _issue(
                "MAPPING_PROFILE_VERSION_MISMATCH",
                f"映射配置版本为{mapping.get('version')}，manifest声明为{mapping_version}",
                manifest_path.name,
            )
        )

    contract_id = str(manifest.get("contract_id", "")).strip()
    contract_version = str(manifest.get("contract_version", "")).strip()
    contract_path, contract = _find_contract(repo_root, contract_id)
    if contract is None or contract_path is None:
        errors.append(
            _issue("CONTRACT_NOT_FOUND", f"输入合同不存在：{contract_id}", manifest_path.name)
        )
        field_dictionary_path = (
            repo_root
            / "resources"
            / "contracts"
            / "part1"
            / "v1"
            / "field_dictionary.json"
        )
    else:
        if str(contract.get("version", "")) != contract_version:
            errors.append(
                _issue(
                    "CONTRACT_VERSION_MISMATCH",
                    f"合同版本为{contract.get('version')}，manifest声明为{contract_version}",
                    manifest_path.name,
                )
            )
        field_dictionary_path = contract_path.parent / str(
            contract.get("field_dictionary", "field_dictionary.json")
        )

    try:
        field_dictionary = _read_json(field_dictionary_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        field_dictionary = {"fields": []}
        errors.append(
            _issue("FIELD_DICTIONARY_UNREADABLE", str(exc), str(field_dictionary_path))
        )
    fields_by_id = {
        str(row.get("field_id", "")): row for row in field_dictionary.get("fields", [])
    }

    try:
        nodes_by_id, node_catalog = _load_dynamic_nodes(repo_root)
    except Exception as exc:  # pragma: no cover - reported to the user with context
        nodes_by_id, node_catalog = {}, {"nodes": []}
        errors.append(
            _issue(
                "DYNAMIC_NODE_REGISTRY_UNREADABLE",
                str(exc),
                "src/qra_engine/dynamic.py",
            )
        )

    source_ids: set[str] = set()
    sources_by_id: dict[str, dict[str, str]] = {}
    for index, row in enumerate(inventory, start=2):
        source_id = str(row.get("source_id", "")).strip()
        if not source_id:
            errors.append(
                _issue(
                    "SOURCE_ID_MISSING",
                    "source_id不能为空",
                    f"source-inventory.csv:{index}",
                )
            )
            continue
        if source_id in source_ids:
            errors.append(
                _issue(
                    "DUPLICATE_SOURCE_ID",
                    f"source_id重复：{source_id}",
                    f"source-inventory.csv:{index}",
                )
            )
        source_ids.add(source_id)
        sources_by_id[source_id] = row
        status = str(row.get("current_status", "")).strip().upper()
        digest = str(row.get("sha256", "")).strip().lower()
        if status not in MISSING_SOURCE_STATUSES and not SHA256_RE.fullmatch(digest):
            errors.append(
                _issue(
                    "AVAILABLE_SOURCE_HASH_INVALID",
                    f"可用来源{source_id}必须登记合法SHA-256",
                    f"source-inventory.csv:{index}",
                )
            )
        if "/" in str(row.get("sanitized_file_name", "")) or "\\" in str(
            row.get("sanitized_file_name", "")
        ):
            errors.append(
                _issue(
                    "SANITIZED_FILE_NAME_NOT_BASENAME",
                    f"脱敏文件名不得包含路径：{source_id}",
                    f"source-inventory.csv:{index}",
                )
            )

    gap_ids: set[str] = set()
    gaps_by_id: dict[str, dict[str, str]] = {}
    for index, row in enumerate(gaps, start=2):
        gap_id = str(row.get("gap_id", "")).strip()
        if not gap_id:
            errors.append(
                _issue(
                    "GAP_ID_MISSING",
                    "gap_id不能为空",
                    f"data-gap-register.csv:{index}",
                )
            )
            continue
        if gap_id in gap_ids:
            errors.append(
                _issue(
                    "DUPLICATE_GAP_ID",
                    f"gap_id重复：{gap_id}",
                    f"data-gap-register.csv:{index}",
                )
            )
        gap_ids.add(gap_id)
        gaps_by_id[gap_id] = row
        for node_id in _split(row.get("affected_node_ids")):
            if node_id not in nodes_by_id:
                errors.append(
                    _issue(
                        "UNKNOWN_NODE_ID",
                        f"缺口登记引用未知node_id：{node_id}",
                        f"data-gap-register.csv:{index}",
                    )
                )

    matrix_by_path: dict[str, list[dict[str, str]]] = {}
    for index, row in enumerate(matrix, start=2):
        field_id = str(row.get("field_id", "")).strip()
        dictionary_row = fields_by_id.get(field_id)
        if dictionary_row is None:
            errors.append(
                _issue(
                    "UNKNOWN_FIELD_ID",
                    f"字段字典中不存在：{field_id}",
                    f"field-node-matrix.csv:{index}",
                )
            )
        else:
            expected_path = str(dictionary_row.get("target_path", ""))
            actual_path = str(row.get("target_path", "")).strip()
            if actual_path != expected_path:
                errors.append(
                    _issue(
                        "TARGET_PATH_MISMATCH",
                        f"{field_id}的target_path应为{expected_path}，实际为{actual_path}",
                        f"field-node-matrix.csv:{index}",
                    )
                )
            conflict_sensitive = bool(
                dictionary_row.get("conflict_policy", {}).get("blocking")
            ) or str(dictionary_row.get("sensitivity", "")) == "SENSITIVE"
            manual_policy = "MANUAL" in str(row.get("conflict_policy", "")).upper()
            if conflict_sensitive and (
                not _as_bool(row.get("review_required")) or not manual_policy
            ):
                errors.append(
                    _issue(
                        "CONFLICT_FIELD_REVIEW_REQUIRED",
                        f"冲突敏感字段{field_id}必须阻断并人工复核",
                        f"field-node-matrix.csv:{index}",
                    )
                )

        path = str(row.get("target_path", "")).strip()
        matrix_by_path.setdefault(path, []).append(row)
        for source_id in [
            str(row.get("preferred_source_id", "")).strip(),
            *_split(row.get("alternative_source_ids")),
        ]:
            if source_id and source_id not in source_ids:
                errors.append(
                    _issue(
                        "UNKNOWN_SOURCE_ID",
                        f"字段{field_id}引用不存在的source_id：{source_id}",
                        f"field-node-matrix.csv:{index}",
                    )
                )
        row_gap_id = str(row.get("gap_id", "")).strip()
        if row_gap_id and row_gap_id not in gap_ids:
            errors.append(
                _issue(
                    "UNKNOWN_GAP_ID",
                    f"字段{field_id}引用不存在的gap_id：{row_gap_id}",
                    f"field-node-matrix.csv:{index}",
                )
            )
        critical = str(row.get("criticality", "")).upper() == "CRITICAL" or _as_bool(
            row.get("required_for_pilot")
        )
        evidence = str(row.get("evidence_requirement", "")).strip().upper()
        if critical and evidence in {"", "NONE", "NOT_REQUIRED"}:
            errors.append(
                _issue(
                    "CRITICAL_FIELD_EVIDENCE_REQUIRED",
                    f"关键字段{field_id}缺少证据要求",
                    f"field-node-matrix.csv:{index}",
                )
            )
        missing_policy = str(row.get("missing_policy", "")).strip().upper()
        if any(token in missing_policy for token in ("FILL_ZERO", "SILENT", "DEFAULT_VALUE")):
            errors.append(
                _issue(
                    "SILENT_MISSING_VALUE_POLICY",
                    f"字段{field_id}采用了禁止的缺失值策略：{missing_policy}",
                    f"field-node-matrix.csv:{index}",
                )
            )
        if _as_bool(row.get("default_allowed")):
            contract_default_allowed = bool(
                (dictionary_row or {}).get("constraints", {}).get("default_allowed", False)
            )
            if not contract_default_allowed:
                errors.append(
                    _issue(
                        "DEFAULT_NOT_ALLOWED",
                        f"字段{field_id}没有合同授权的默认值",
                        f"field-node-matrix.csv:{index}",
                    )
                )
        for node_id in _split(row.get("affected_node_ids")):
            if node_id not in nodes_by_id:
                errors.append(
                    _issue(
                        "UNKNOWN_NODE_ID",
                        f"字段{field_id}引用未知node_id：{node_id}",
                        f"field-node-matrix.csv:{index}",
                    )
                )

    target_node_ids = [str(value).strip() for value in manifest.get("target_node_ids", [])]
    for node_id in target_node_ids:
        if node_id not in nodes_by_id:
            errors.append(
                _issue("UNKNOWN_NODE_ID", f"manifest引用未知node_id：{node_id}", manifest_path.name)
            )

    required_input_accounting: list[dict[str, Any]] = []
    unaccounted_requirement_count = 0
    for node_id in target_node_ids:
        node = nodes_by_id.get(node_id)
        if node is None:
            continue
        for requirement in node.get("required_inputs", []):
            path = str(requirement.get("path", ""))
            rows = matrix_by_path.get(path, [])
            source_accounted = any(
                _field_is_available(row)
                and bool(str(row.get("preferred_source_id", "")).strip())
                and _source_is_available(
                    sources_by_id.get(str(row.get("preferred_source_id", "")).strip(), {})
                )
                for row in rows
            )
            gap_accounted = any(
                str(row.get("gap_id", "")).strip() in gap_ids for row in rows
            )
            accounted = source_accounted or gap_accounted
            required_input_accounting.append(
                {
                    "node_id": node_id,
                    "path": path,
                    "source_accounted": source_accounted,
                    "gap_accounted": gap_accounted,
                    "accounted": accounted,
                }
            )
            if not accounted:
                unaccounted_requirement_count += 1
                errors.append(
                    _issue(
                        "TARGET_NODE_INPUT_UNACCOUNTED",
                        f"目标节点{node_id}必需输入{path}既无可用来源也无明确gap",
                        matrix_path.name,
                    )
                )

    report_tier = str(manifest.get("target_report_tier", "")).strip()
    if report_tier != SCREENING_REPORT_TIER:
        errors.append(
            _issue(
                "FORMAL_REPORT_TIER_FORBIDDEN",
                f"第一版报告层级必须是{SCREENING_REPORT_TIER}，实际为{report_tier}",
                manifest_path.name,
            )
        )

    if _as_bool(manifest.get("external_sharing_allowed")):
        errors.append(
            _issue("EXTERNAL_SHARING_FORBIDDEN", "真实试点不得允许外发", manifest_path.name)
        )

    approved_root = (
        str(manifest.get("approved_real_source_root", ""))
        .replace("\\", "/")
        .strip("/")
    )
    expected_root = f"workspace/pilots/{pilot_id}/sources"
    if approved_root != expected_root:
        errors.append(
            _issue(
                "APPROVED_SOURCE_ROOT_INVALID",
                f"真实资料批准目录必须是{expected_root}",
                manifest_path.name,
            )
        )

    custody_sources = [
        row
        for row in inventory
        if str(row.get("current_status", "")).strip().upper()
        == "AVAILABLE_IN_APPROVED_SOURCE_ROOT"
    ]
    custody_hashes: set[str] = set()
    if verify_source_files and approved_root == expected_root:
        custody_root = repo_root / approved_root
        if custody_root.is_dir():
            for path in custody_root.iterdir():
                if path.is_file():
                    try:
                        custody_hashes.add(_sha256_file(path))
                    except OSError:
                        continue
        elif custody_sources:
            errors.append(
                _issue(
                    "APPROVED_SOURCE_ROOT_MISSING",
                    "已声明资料可用，但批准的真实资料目录不存在",
                    approved_root,
                )
            )
    matched_custody_sources = list(custody_sources) if not verify_source_files else []
    if verify_source_files:
        for row in custody_sources:
            source_id = str(row.get("source_id", "")).strip()
            digest = str(row.get("sha256", "")).strip().lower()
            if digest in custody_hashes:
                matched_custody_sources.append(row)
            else:
                errors.append(
                    _issue(
                        "APPROVED_SOURCE_FILE_MISSING",
                        f"批准目录中未找到与{source_id}登记哈希一致的文件",
                        approved_root,
                    )
                )

    real_data_status = str(manifest.get("real_data_status", "")).strip().upper()
    if real_data_status in REAL_DATA_STATUSES or real_data_status.startswith("REAL_PROJECT"):
        for index, row in enumerate(inventory, start=2):
            classification = str(row.get("data_classification", "")).strip().upper()
            if classification in SYNTHETIC_CLASSIFICATIONS:
                errors.append(
                    _issue(
                        "SYNTHETIC_MISLABELED_REAL",
                        f"{row.get('source_id')}为{classification}，不得随manifest标记为真实工程数据",
                        f"source-inventory.csv:{index}",
                    )
                )

    for path in _prohibited_real_data_matches(repo_root, inventory):
        errors.append(
            _issue(
                "REAL_DATA_IN_PROHIBITED_DIRECTORY",
                "真实资料哈希出现在tests、resources或docs目录",
                path,
            )
        )

    allowed_file_types = {
        str(value).strip().lower().lstrip(".") for value in manifest.get("allowed_file_types", [])
    }
    for row in inventory:
        file_type = str(row.get("file_type", "")).strip().lower().lstrip(".")
        if (
            file_type
            and file_type not in allowed_file_types
            and str(row.get("current_status", "")).strip().upper() == "MISSING"
        ):
            warnings.append(
                _issue(
                    "REQUESTED_PACKAGE_MUST_BE_UNPACKED",
                    f"{row.get('source_id')}的{file_type}不在白名单；应在受控区解包后逐文件登记",
                    inventory_path.name,
                )
            )

    blocking_gaps = [
        row
        for row in gaps
        if row.get("severity") == "P0_BLOCKS_PILOT" and not _gap_is_closed(row)
    ]
    available_sources = [row for row in inventory if _source_is_available(row)]
    available_fields = [row for row in matrix if _field_is_available(row)]
    blocking_field_count = sum(
        1
        for row in matrix
        if _as_bool(row.get("required_for_pilot"))
        and (
            not _field_is_available(row)
            or str(row.get("gap_id", ""))
            in {str(gap.get("gap_id", "")) for gap in blocking_gaps}
        )
    )

    support_by_node: dict[str, str] = {}
    support_rank = {"FULLY_SUPPORTED": 0, "PARTIALLY_SUPPORTED": 1, "UNSUPPORTED": 2}
    for node_id in target_node_ids:
        node = nodes_by_id.get(node_id)
        if node is None:
            support_by_node[node_id] = "UNSUPPORTED"
            continue
        state = "FULLY_SUPPORTED"
        for dependency in node.get("dependencies", []):
            dependency_state = support_by_node.get(str(dependency), "UNSUPPORTED")
            if support_rank[dependency_state] > support_rank[state]:
                state = dependency_state
        for requirement in node.get("required_inputs", []):
            rows = matrix_by_path.get(str(requirement.get("path", "")), [])
            if not rows:
                state = "UNSUPPORTED"
                continue
            requirement_full = False
            requirement_accounted = False
            for row in rows:
                source_id = str(row.get("preferred_source_id", "")).strip()
                gap = gaps_by_id.get(str(row.get("gap_id", "")).strip())
                source_ok = bool(source_id) and _source_is_available(
                    sources_by_id.get(source_id, {})
                )
                gap_blocks_pilot = bool(
                    gap
                    and gap.get("severity") == "P0_BLOCKS_PILOT"
                    and not _gap_is_closed(gap)
                )
                if _field_is_available(row) and source_ok and not gap_blocks_pilot:
                    requirement_full = True
                if source_ok or gap is not None:
                    requirement_accounted = True
            requirement_state = (
                "FULLY_SUPPORTED"
                if requirement_full
                else "PARTIALLY_SUPPORTED"
                if requirement_accounted
                else "UNSUPPORTED"
            )
            if support_rank[requirement_state] > support_rank[state]:
                state = requirement_state
        support_by_node[node_id] = state

    owners_clear = all(
        str(manifest.get(key, "")).strip().upper() not in {"", "TBD", "UNASSIGNED", "UNKNOWN"}
        for key in ("business_owner", "data_owner", "qra_reviewer")
    )
    segment_status = str(manifest.get("segment_scope", {}).get("status", "")).upper()
    segment_signed_off = "UNVERIFIED" not in segment_status and "PENDING" not in segment_status
    validation_passed = not errors
    ready = (
        validation_passed
        and not blocking_gaps
        and owners_clear
        and segment_signed_off
        and unaccounted_requirement_count == 0
        and bool(target_node_ids)
        and bool(allowed_file_types)
        and bool(custody_sources)
        and len(matched_custody_sources) == len(custody_sources)
        and not _as_bool(manifest.get("external_sharing_allowed"))
    )

    blocking_reasons: list[str] = []
    blocking_reasons.extend(str(row.get("description", "")) for row in blocking_gaps)
    if not owners_clear and not any("business_owner" in reason for reason in blocking_reasons):
        blocking_reasons.append("业务负责人、数据负责人或QRA复核人尚未明确")
    if not segment_signed_off and not any("权威" in reason for reason in blocking_reasons):
        blocking_reasons.append("权威管段范围尚未签批")
    blocking_reasons.extend(error["message"] for error in errors)
    blocking_reasons = list(dict.fromkeys(reason for reason in blocking_reasons if reason))

    input_hashes = {
        "pilot_manifest": _sha256_file(manifest_path),
        "source_inventory": _sha256_file(inventory_path),
        "field_node_matrix": _sha256_file(matrix_path),
        "data_gap_register": _sha256_file(gaps_path),
        "field_dictionary": _sha256_file(field_dictionary_path)
        if field_dictionary_path.is_file()
        else None,
        "mapping_profile": _sha256_file(mapping_path) if mapping_path else None,
        "dynamic_node_registry": _sha256_json(node_catalog),
    }
    node_states = [
        {"node_id": node_id, "support_status": support_by_node.get(node_id, "UNSUPPORTED")}
        for node_id in target_node_ids
    ]
    return {
        "status": "READY" if ready else "BLOCKED",
        "validation_status": "PASS" if validation_passed else "FAIL",
        "pilot_id": pilot_id,
        "pilot_version": version,
        "source_count": len(inventory),
        "available_source_count": len(available_sources),
        "approved_source_file_count": len(custody_hashes),
        "approved_source_match_count": len(matched_custody_sources),
        "source_file_verification_performed": verify_source_files,
        "target_field_count": len(matrix),
        "available_field_count": len(available_fields),
        "missing_field_count": len(matrix) - len(available_fields),
        "blocking_field_count": blocking_field_count,
        "gap_count": len(gaps),
        "blocking_gap_count": len(blocking_gaps),
        "target_node_count": len(target_node_ids),
        "fully_supported_node_count": sum(
            row["support_status"] == "FULLY_SUPPORTED" for row in node_states
        ),
        "partially_supported_node_count": sum(
            row["support_status"] == "PARTIALLY_SUPPORTED" for row in node_states
        ),
        "unsupported_node_count": sum(
            row["support_status"] == "UNSUPPORTED" for row in node_states
        ),
        "node_support": node_states,
        "report_tier": report_tier,
        "required_input_accounting": required_input_accounting,
        "blocking_reasons": blocking_reasons,
        "validation_errors": errors,
        "validation_warnings": warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_hashes": input_hashes,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只读校验真实资料QRA试点范围并生成机器可读就绪报告"
    )
    parser.add_argument(
        "--pilot-dir",
        type=Path,
        help="试点资源目录；默认使用resources/pilots/jiujiang-qra-screening-pilot-v1",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="就绪报告路径；默认使用workspace/pilots/<pilot_id>/pilot-readiness.json",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="只输出到标准输出，不写就绪报告",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    pilot_dir = args.pilot_dir or (
        repo_root / "resources" / "pilots" / DEFAULT_PILOT_ID
    )
    report = validate_pilot_scope(pilot_dir, repo_root=repo_root)
    if not args.no_write:
        output = args.output or (
            repo_root
            / "workspace"
            / "pilots"
            / str(report.get("pilot_id") or DEFAULT_PILOT_ID)
            / "pilot-readiness.json"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("validation_status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
