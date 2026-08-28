"""Version-bound filesystem cache for canonical parsing artifacts."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from .contracts import canonical_json


def parsing_cache_key(
    *,
    source_sha256: str,
    parser_id: str,
    parser_version: str,
    ocr_provider_id: str,
    ocr_model_version: str,
    preprocessing_profile: str,
    contract_version: str,
    ocr_parameters: dict[str, object] | None = None,
) -> str:
    value = {
        "source_sha256": source_sha256,
        "parser_id": parser_id,
        "parser_version": parser_version,
        "ocr_provider_id": ocr_provider_id,
        "ocr_model_version": ocr_model_version,
        "preprocessing_profile": preprocessing_profile,
        "contract_version": contract_version,
        "ocr_parameters": ocr_parameters or {},
    }
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class ParsingCache:
    def __init__(self, root: Path):
        self.root = root

    def entry(self, key: str) -> Path:
        if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
            raise ValueError("解析缓存键无效")
        return self.root / key[:2] / key

    def restore(self, key: str, target: Path) -> bool:
        source = self.entry(key)
        required = ("parsed_document.json", "quality_report.json", "preview_manifest.json")
        if not source.is_dir() or not all((source / name).is_file() for name in required):
            return False
        manifest = json.loads((source / "cache_manifest.json").read_text(encoding="utf-8"))
        if manifest.get("cache_key") != key:
            return False
        recorded_files = manifest.get("files")
        if not isinstance(recorded_files, dict):
            return False
        for relative_text, expected_hash in recorded_files.items():
            relative = Path(str(relative_text))
            cached_file = (source / relative).resolve()
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not cached_file.is_relative_to(source.resolve())
                or not cached_file.is_file()
                or hashlib.sha256(cached_file.read_bytes()).hexdigest() != expected_hash
            ):
                return False
        target.mkdir(parents=True, exist_ok=True)
        for relative_text in recorded_files:
            relative = Path(str(relative_text))
            item = source / relative
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item, destination)
        return True

    def store(self, key: str, source: Path) -> None:
        target = self.entry(key)
        target.mkdir(parents=True, exist_ok=True)
        file_hashes: dict[str, str] = {}
        for item in source.rglob("*"):
            if item.is_file() and item.name != "cache_manifest.json":
                relative = item.relative_to(source)
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(item, destination)
                file_hashes[relative.as_posix()] = hashlib.sha256(
                    destination.read_bytes()
                ).hexdigest()
        (target / "cache_manifest.json").write_text(
            json.dumps(
                {"cache_key": key, "files": file_hashes},
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


__all__ = ["ParsingCache", "parsing_cache_key"]
