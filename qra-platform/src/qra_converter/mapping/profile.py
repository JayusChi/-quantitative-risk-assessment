from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROFILE_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class MappingProfile:
    profile_id: str
    version: str
    schema_version: str
    defaults: dict[str, Any]
    tables: tuple[dict[str, Any], ...]
    path: Path
    checksum_sha256: str
    source_priorities: tuple[dict[str, Any], ...] = ()
    manual_review: dict[str, Any] | None = None
    inherited_profiles: tuple[str, ...] = ()


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _merge_profiles(base: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    result = _deep_merge(base, {key: value for key, value in child.items() if key != "tables"})
    by_id = {str(table["id"]): dict(table) for table in base.get("tables", [])}
    order = [str(table["id"]) for table in base.get("tables", [])]
    for table in child.get("tables", []):
        table_id = str(table.get("id", ""))
        if table_id not in by_id:
            order.append(table_id)
        by_id[table_id] = dict(table)
    result["tables"] = [by_id[table_id] for table_id in order]
    result["source_priorities"] = [
        *base.get("source_priorities", []),
        *child.get("source_priorities", []),
    ]
    result.pop("extends", None)
    return result


def _load_effective_payload(
    path: Path, seen: tuple[Path, ...] = ()
) -> tuple[dict[str, Any], tuple[str, ...]]:
    resolved = path.resolve()
    if resolved in seen:
        chain = " -> ".join(str(item) for item in (*seen, resolved))
        raise ValueError(f"映射配置继承形成循环：{chain}")
    data = resolved.read_bytes()
    payload = json.loads(data.decode("utf-8"))
    parent_value = payload.get("extends") if isinstance(payload, dict) else None
    if not parent_value:
        return payload, ()
    parent_path = Path(str(parent_value))
    if not parent_path.is_absolute():
        parent_path = (resolved.parent / parent_path).resolve()
    if not parent_path.is_file():
        raise FileNotFoundError(f"映射配置继承文件不存在：{parent_path}")
    parent, inherited = _load_effective_payload(parent_path, (*seen, resolved))
    return _merge_profiles(parent, payload), (*inherited, str(parent_path))


def _validate_profile(payload: Any, path: Path) -> None:
    if not isinstance(payload, dict):
        raise ValueError(f"映射配置根节点必须是对象：{path}")
    required = ("profile_id", "version", "schema_version", "tables")
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"映射配置缺少字段：{', '.join(missing)}")
    if payload["schema_version"] != PROFILE_SCHEMA_VERSION:
        raise ValueError(f"映射配置schema_version必须为{PROFILE_SCHEMA_VERSION}")
    if not isinstance(payload["tables"], list) or not payload["tables"]:
        raise ValueError("映射配置tables必须是非空数组")
    priorities = payload.get("source_priorities", [])
    if not isinstance(priorities, list):
        raise ValueError("映射配置source_priorities必须是数组")
    for rule in priorities:
        if not isinstance(rule, dict) or not isinstance(rule.get("priority"), int):
            raise ValueError("每项来源优先级必须是对象并声明整数priority")
        if not rule.get("file_patterns"):
            raise ValueError("每项来源优先级必须声明file_patterns")
    review = payload.get("manual_review") or {}
    threshold = float(review.get("confidence_threshold", 0.8))
    if threshold < 0.0 or threshold > 1.0:
        raise ValueError("manual_review.confidence_threshold必须在[0,1]内")
    table_ids: set[str] = set()
    for table in payload["tables"]:
        if not isinstance(table, dict):
            raise ValueError("每项表格映射必须是对象")
        table_id = str(table.get("id", "")).strip()
        if not table_id or table_id in table_ids:
            raise ValueError(f"表格映射id缺失或重复：{table_id or '空'}")
        table_ids.add(table_id)
        if not table.get("target"):
            raise ValueError(f"表格映射{table_id}缺少target")
        fields = table.get("fields")
        if not isinstance(fields, list) or not fields:
            raise ValueError(f"表格映射{table_id}的fields必须是非空数组")
        targets: set[str] = set()
        record_key = table.get("record_key")
        if record_key is not None and (
            not isinstance(record_key, list)
            or not record_key
            or not all(isinstance(item, str) and item.strip() for item in record_key)
        ):
            raise ValueError(f"表格映射{table_id}的record_key必须是非空字符串数组")
        confidence = float(table.get("mapping_confidence", 1.0))
        if confidence < 0.0 or confidence > 1.0:
            raise ValueError(f"表格映射{table_id}的mapping_confidence必须在[0,1]内")
        for field in fields:
            target = str(field.get("target", "")).strip()
            aliases = field.get("aliases")
            if not target or target in targets:
                raise ValueError(f"表格映射{table_id}字段target缺失或重复：{target}")
            if not isinstance(aliases, list) or not aliases:
                raise ValueError(f"字段{table_id}.{target}必须声明aliases")
            targets.add(target)


def load_profile(path: Path | str) -> MappingProfile:
    profile_path = Path(path).resolve()
    payload, inherited = _load_effective_payload(profile_path)
    _validate_profile(payload, profile_path)
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return MappingProfile(
        profile_id=str(payload["profile_id"]),
        version=str(payload["version"]),
        schema_version=str(payload["schema_version"]),
        defaults=dict(payload.get("defaults") or {}),
        tables=tuple(payload["tables"]),
        path=profile_path,
        checksum_sha256=hashlib.sha256(canonical).hexdigest(),
        source_priorities=tuple(payload.get("source_priorities") or ()),
        manual_review=dict(payload.get("manual_review") or {}),
        inherited_profiles=inherited,
    )


def source_priority(
    profile: MappingProfile,
    definition: dict[str, Any],
    file_name: str,
    sheet_name: str,
) -> int:
    """Return the highest matching deterministic source-priority score."""

    rules = [*profile.source_priorities, *(definition.get("source_priorities") or [])]
    priorities = [0]
    for rule in rules:
        file_match = any(
            fnmatch.fnmatch(file_name.casefold(), str(pattern).casefold())
            for pattern in rule.get("file_patterns", ["*"])
        )
        sheet_match = any(
            re.fullmatch(str(pattern), sheet_name, flags=re.IGNORECASE)
            for pattern in rule.get("sheet_patterns", [".*"])
        )
        if file_match and sheet_match:
            priorities.append(int(rule["priority"]))
    return max(priorities)


def resolve_profile_path(value: str | Path, search_root: Path | None = None) -> Path:
    direct = Path(value)
    if direct.is_file():
        return direct.resolve()
    root = (search_root or Path.cwd()).resolve()
    candidates = sorted((root / "resources" / "mappings").rglob("*.json"))
    for candidate in candidates:
        if candidate.stem == str(value):
            return candidate.resolve()
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if payload.get("profile_id") == str(value):
            return candidate.resolve()
    raise FileNotFoundError(f"找不到映射配置：{value}")


def selector_matches(table: dict[str, Any], file_name: str, sheet_name: str) -> bool:
    selectors = table.get("selectors") or [
        {
            "file_patterns": table.get("file_patterns", ["*"]),
            "sheet_patterns": table.get("sheet_patterns", ["*"]),
        }
    ]
    for selector in selectors:
        file_patterns = selector.get("file_patterns") or ["*"]
        sheet_patterns = selector.get("sheet_patterns") or ["*"]
        file_match = any(
            fnmatch.fnmatch(file_name.casefold(), str(pattern).casefold())
            for pattern in file_patterns
        )
        sheet_match = any(
            re.fullmatch(str(pattern), sheet_name, flags=re.IGNORECASE)
            for pattern in sheet_patterns
        )
        if file_match and sheet_match:
            return True
    return False


__all__ = [
    "MappingProfile",
    "PROFILE_SCHEMA_VERSION",
    "load_profile",
    "resolve_profile_path",
    "selector_matches",
    "source_priority",
]
