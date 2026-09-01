"""Versioned pilot manifests used by platform orchestration."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from qra_engine.dynamic import dynamic_node_catalog

from .paths import PROJECT_ROOT

PILOT_ROOT = (PROJECT_ROOT / "resources" / "pilots").resolve()


def load_pilot_manifest(pilot_id: str) -> dict[str, Any]:
    safe_id = str(pilot_id).strip()
    if not safe_id or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-." for character in safe_id
    ):
        raise ValueError("试点ID无效")
    path = (PILOT_ROOT / safe_id / "pilot-manifest.json").resolve()
    if not path.is_relative_to(PILOT_ROOT) or not path.is_file():
        raise KeyError(f"试点清单不存在：{safe_id}")
    content = path.read_bytes()
    manifest = json.loads(content.decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("pilot_id") != safe_id:
        raise ValueError("试点清单身份不一致")
    target_node_ids = manifest.get("target_node_ids")
    if not isinstance(target_node_ids, list) or not target_node_ids:
        raise ValueError("试点清单必须声明目标节点")
    known = {str(row["node_id"]) for row in dynamic_node_catalog()["nodes"]}
    targets = [str(value) for value in target_node_ids]
    unknown = sorted(set(targets) - known)
    if unknown:
        raise ValueError(f"试点清单包含未知目标节点：{', '.join(unknown)}")
    if len(targets) != len(set(targets)):
        raise ValueError("试点清单目标节点不能重复")
    return {
        **manifest,
        "target_node_ids": targets,
        "manifest_sha256": hashlib.sha256(content).hexdigest(),
        "manifest_relative_path": path.relative_to(PROJECT_ROOT).as_posix(),
    }


def resolve_pilot_for_profile(profile_id: str) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    if not PILOT_ROOT.is_dir():
        return None
    for path in sorted(PILOT_ROOT.glob("*/pilot-manifest.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and str(raw.get("mapping_profile_id") or "") == profile_id:
            matches.append(load_pilot_manifest(str(raw.get("pilot_id") or "")))
    if len(matches) > 1:
        raise ValueError(f"映射配置关联了多个试点清单：{profile_id}")
    return matches[0] if matches else None


__all__ = ["PILOT_ROOT", "load_pilot_manifest", "resolve_pilot_for_profile"]
