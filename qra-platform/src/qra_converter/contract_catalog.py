"""Versioned Part 1 contract catalog loading and integrity verification."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_MANIFEST_KEYS = {
    "contract_id",
    "version",
    "status",
    "released_at",
    "compatible_engine_contract",
    "field_dictionary",
    "schemas",
    "files_sha256",
    "supersedes",
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"合同文件必须使用UTF-8编码：{path.name}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"合同文件不是合法JSON：{path.name}: {exc}") from exc


def _safe_contract_file(root: Path, relative: str) -> Path:
    value = str(relative).strip()
    if not value or "\\" in value:
        raise ValueError(f"合同相对路径无效：{relative!r}")
    candidate = (root / value).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"合同路径越界：{relative}")
    if not candidate.is_file():
        raise FileNotFoundError(f"合同文件不存在：{relative}")
    return candidate


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class ContractCatalog:
    """Verified, immutable metadata for one contract directory."""

    root: Path
    contract_id: str
    version: str
    status: str
    manifest_sha256: str
    compatible_engine_contract: str
    manifest: Mapping[str, Any]
    field_dictionary: Mapping[str, Any]
    schemas: Mapping[str, Path]

    def schema_path(self, name: str) -> Path:
        try:
            return self.schemas[name]
        except KeyError as exc:
            raise KeyError(f"合同未登记Schema：{name}") from exc

    def read_schema(self, name: str) -> dict[str, Any]:
        value = _read_json(self.schema_path(name))
        if not isinstance(value, dict):
            raise ValueError(f"Schema顶层必须是对象：{name}")
        return value

    def assert_identity(
        self,
        *,
        contract_id: str | None = None,
        version: str | None = None,
        manifest_sha256: str | None = None,
    ) -> None:
        if contract_id is not None and contract_id != self.contract_id:
            raise ValueError(f"合同ID不匹配：期望{contract_id}，实际{self.contract_id}")
        if version is not None and version != self.version:
            raise ValueError(f"合同版本不匹配：期望{version}，实际{self.version}")
        if manifest_sha256 is not None and manifest_sha256 != self.manifest_sha256:
            raise ValueError("合同清单哈希不匹配，任务创建后合同已变化")


def load_contract_catalog(
    contract_dir: Path | str,
    *,
    expected_contract_id: str | None = None,
    expected_version: str | None = None,
    expected_manifest_sha256: str | None = None,
) -> ContractCatalog:
    """Load a contract only after every declared file passes SHA-256 checks."""

    root = Path(contract_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"合同目录不存在：{contract_dir}")
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"合同清单不存在：{manifest_path}")
    manifest_bytes = manifest_path.read_bytes()
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError("合同清单顶层必须是对象")
    missing = sorted(_REQUIRED_MANIFEST_KEYS - set(manifest))
    if missing:
        raise ValueError(f"合同清单缺少字段：{', '.join(missing)}")

    contract_id = str(manifest["contract_id"]).strip()
    version = str(manifest["version"]).strip()
    if not contract_id:
        raise ValueError("合同清单contract_id不能为空")
    if not _SEMVER.fullmatch(version):
        raise ValueError(f"合同版本必须是SemVer：{version}")
    files_sha256 = manifest["files_sha256"]
    if not isinstance(files_sha256, dict) or not files_sha256:
        raise ValueError("合同清单files_sha256必须是非空对象")
    for relative, expected_hash in sorted(files_sha256.items()):
        if not isinstance(relative, str) or not _SHA256.fullmatch(str(expected_hash)):
            raise ValueError(f"合同文件哈希声明无效：{relative}")
        actual_hash = sha256_file(_safe_contract_file(root, relative))
        if actual_hash != expected_hash:
            raise ValueError(
                f"合同文件哈希不匹配：{relative}；期望{expected_hash}，实际{actual_hash}"
            )

    field_dictionary_relative = str(manifest["field_dictionary"])
    if field_dictionary_relative not in files_sha256:
        raise ValueError("字段字典未纳入files_sha256")
    dictionary = _read_json(_safe_contract_file(root, field_dictionary_relative))
    if not isinstance(dictionary, dict) or not isinstance(dictionary.get("fields"), list):
        raise ValueError("字段字典顶层必须包含fields数组")
    if dictionary.get("dictionary_id") != contract_id:
        raise ValueError("字段字典dictionary_id与合同ID不一致")
    if dictionary.get("version") != version:
        raise ValueError("字段字典version与合同版本不一致")

    schema_values = manifest["schemas"]
    if not isinstance(schema_values, dict) or not schema_values:
        raise ValueError("合同清单schemas必须是非空对象")
    schemas: dict[str, Path] = {}
    for name, relative in schema_values.items():
        if not isinstance(name, str) or not isinstance(relative, str):
            raise ValueError("合同清单schemas名称和路径必须是字符串")
        if relative not in files_sha256:
            raise ValueError(f"Schema未纳入files_sha256：{name}")
        schemas[name] = _safe_contract_file(root, relative)

    manifest_sha256 = sha256_bytes(manifest_bytes)
    catalog = ContractCatalog(
        root=root,
        contract_id=contract_id,
        version=version,
        status=str(manifest["status"]),
        manifest_sha256=manifest_sha256,
        compatible_engine_contract=str(manifest["compatible_engine_contract"]),
        manifest=_freeze(manifest),
        field_dictionary=_freeze(dictionary),
        schemas=MappingProxyType(schemas),
    )
    catalog.assert_identity(
        contract_id=expected_contract_id,
        version=expected_version,
        manifest_sha256=expected_manifest_sha256,
    )
    return catalog


__all__ = [
    "ContractCatalog",
    "load_contract_catalog",
    "sha256_bytes",
    "sha256_file",
]
