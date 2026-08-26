from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from qra_converter.mapping import load_profile
from qra_converter.service import CONVERTER_VERSION, convert_sources
from qra_engine.dynamic import plan_dynamic_flow
from qra_engine.validation import validate_import_contract

from .database import QraDatabase
from .paths import DEFAULT_RUNTIME_ROOT, PROJECT_ROOT

MAPPING_ROOT = (PROJECT_ROOT / "resources" / "mappings").resolve()
SUPPORTED_SOURCE_SUFFIXES = frozenset({".csv", ".xls", ".xlsx", ".docx", ".pdf"})
SUPPORTED_UPLOAD_SUFFIXES = SUPPORTED_SOURCE_SUFFIXES | {".zip"}
MAX_SOURCE_FILES = 100
MAX_UPLOAD_FILE_BYTES = 18 * 1024 * 1024
MAX_UPLOAD_TOTAL_BYTES = 18 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 200
MAX_ARCHIVE_EXPANDED_BYTES = 100 * 1024 * 1024


def _clean_actor(value: str | None) -> str:
    actor = str(value or "local-admin").strip()
    if not actor:
        actor = "local-admin"
    if len(actor) > 120 or any(ord(character) < 32 for character in actor):
        raise ValueError("操作人标识无效")
    return actor


def _safe_upload_name(value: str) -> str:
    name = str(value).strip()
    if not name or name in {".", ".."}:
        raise ValueError("源文件名不能为空")
    if Path(name).name != name or "/" in name or "\\" in name:
        raise ValueError(f"源文件名不能包含路径：{name}")
    if len(name) > 180 or any(ord(character) < 32 for character in name):
        raise ValueError(f"源文件名无效：{name}")
    if Path(name).suffix.casefold() not in SUPPORTED_UPLOAD_SUFFIXES:
        allowed = "、".join(sorted(SUPPORTED_UPLOAD_SUFFIXES))
        raise ValueError(f"不支持的源文件类型：{name}；允许{allowed}")
    return name


def validate_source_uploads(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not files:
        raise ValueError("至少上传一个源文件或ZIP资料包")
    if len(files) > MAX_SOURCE_FILES:
        raise ValueError(f"一次最多上传{MAX_SOURCE_FILES}个源文件")
    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    total_bytes = 0
    for item in files:
        name = _safe_upload_name(str(item.get("file_name") or ""))
        name_key = name.casefold()
        if name_key in names:
            raise ValueError(f"源文件名重复：{name}")
        names.add(name_key)
        content = item.get("content")
        if not isinstance(content, bytes):
            raise ValueError(f"源文件内容必须是字节：{name}")
        if not content:
            raise ValueError(f"源文件为空：{name}")
        if len(content) > MAX_UPLOAD_FILE_BYTES:
            raise ValueError(f"源文件超过18 MB限制：{name}")
        total_bytes += len(content)
        if total_bytes > MAX_UPLOAD_TOTAL_BYTES:
            raise ValueError("本批源文件合计超过18 MB限制")
        normalized.append(
            {
                "file_name": name,
                "media_type": str(
                    item.get("media_type")
                    or mimetypes.guess_type(name)[0]
                    or "application/octet-stream"
                ),
                "byte_count": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "content": content,
            }
        )
    return normalized


def list_mapping_profiles() -> list[dict[str, Any]]:
    profiles = []
    for path in sorted(MAPPING_ROOT.rglob("*.json")):
        profile = load_profile(path)
        profiles.append(
            {
                "profile_id": profile.profile_id,
                "version": profile.version,
                "schema_version": profile.schema_version,
                "sha256": profile.checksum_sha256,
                "mapping_version": f"{profile.profile_id}/{profile.version}",
                "relative_path": path.relative_to(MAPPING_ROOT).as_posix(),
            }
        )
    return profiles


def resolve_mapping_profile(value: str) -> tuple[Path, dict[str, Any]]:
    selected = str(value).strip()
    if not selected:
        raise ValueError("必须选择映射配置")
    matches = [
        profile
        for profile in list_mapping_profiles()
        if selected
        in {
            profile["profile_id"],
            profile["mapping_version"],
            Path(profile["relative_path"]).stem,
            profile["relative_path"],
        }
    ]
    if len(matches) != 1:
        raise ValueError(f"找不到唯一映射配置：{selected}")
    metadata = matches[0]
    path = (MAPPING_ROOT / metadata["relative_path"]).resolve()
    if not path.is_relative_to(MAPPING_ROOT) or not path.is_file():
        raise ValueError("映射配置路径超出受控目录")
    return path, metadata


def conversion_dedupe_key(sources: list[dict[str, Any]], profile_sha256: str) -> str:
    fingerprint = {
        "mapping_sha256": profile_sha256,
        "sources": sorted(
            (
                {
                    "file_name": str(source["file_name"]).casefold(),
                    "sha256": str(source["sha256"]),
                }
                for source in sources
            ),
            key=lambda row: (row["file_name"], row["sha256"]),
        ),
    }
    payload = json.dumps(
        fingerprint, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def submit_conversion(
    database: QraDatabase,
    *,
    profile: str,
    files: list[dict[str, Any]],
    case_id: str | None = None,
    project_name: str | None = None,
    review_decisions: dict[str, Any] | None = None,
    batch_id: str | None = None,
    actor: str | None = None,
) -> tuple[str, bool]:
    sources = validate_source_uploads(files)
    profile_path, metadata = resolve_mapping_profile(profile)
    job_id, created = database.create_conversion_job(
        dedupe_key=conversion_dedupe_key(sources, str(metadata["sha256"])),
        profile_id=str(metadata["profile_id"]),
        profile_version=str(metadata["version"]),
        profile_sha256=str(metadata["sha256"]),
        profile_path=str(profile_path),
        converter_version=CONVERTER_VERSION,
        sources=sources,
        case_id=str(case_id).strip()[:160] if case_id else None,
        project_name=str(project_name).strip()[:160] if project_name else None,
        review_decisions=review_decisions,
        batch_id=batch_id,
        actor=_clean_actor(actor),
    )
    return job_id, created


def _safe_archive_member(info: zipfile.ZipInfo) -> PurePosixPath:
    member = PurePosixPath(info.filename.replace("\\", "/"))
    if member.is_absolute() or not member.parts or ".." in member.parts:
        raise ValueError(f"ZIP包含不安全路径：{info.filename}")
    if info.flag_bits & 0x1:
        raise ValueError(f"ZIP包含加密文件，无法受控解析：{info.filename}")
    mode = info.external_attr >> 16
    if mode and stat.S_ISLNK(mode):
        raise ValueError(f"ZIP包含符号链接：{info.filename}")
    return member


def _expand_zip(
    content: bytes,
    source_dir: Path,
    archive_name: str,
    used_paths: set[str],
) -> int:
    extracted = 0
    supported_count = 0
    with zipfile.ZipFile(io.BytesIO(content), mode="r") as archive:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ValueError(f"ZIP文件条目超过{MAX_ARCHIVE_MEMBERS}个")
        expanded_bytes = sum(info.file_size for info in members if not info.is_dir())
        if expanded_bytes > MAX_ARCHIVE_EXPANDED_BYTES:
            raise ValueError("ZIP解压后超过100 MB限制")
        for info in members:
            if info.is_dir():
                continue
            member = _safe_archive_member(info)
            suffix = Path(member.name).suffix.casefold()
            if suffix not in SUPPORTED_SOURCE_SUFFIXES:
                continue
            target = source_dir.joinpath(*member.parts).resolve()
            if not target.is_relative_to(source_dir.resolve()):
                raise ValueError(f"ZIP成员超出解压目录：{info.filename}")
            path_key = target.relative_to(source_dir.resolve()).as_posix().casefold()
            if path_key in used_paths:
                raise ValueError(f"源文件路径冲突：{info.filename}")
            used_paths.add(path_key)
            target.parent.mkdir(parents=True, exist_ok=True)
            data = archive.read(info)
            if len(data) != info.file_size:
                raise ValueError(f"ZIP成员长度校验失败：{info.filename}")
            target.write_bytes(data)
            extracted += len(data)
            supported_count += 1
    if supported_count == 0:
        raise ValueError(f"ZIP资料包没有可转换文件：{archive_name}")
    return extracted


def _materialize_sources(sources: list[dict[str, Any]], source_dir: Path) -> None:
    used_paths: set[str] = set()
    for source in sources:
        content = bytes(source["content"])
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != source["sha256"] or len(content) != source["byte_count"]:
            raise ValueError(f"持久化源文件完整性校验失败：{source['file_name']}")
        name = str(source["file_name"])
        if Path(name).suffix.casefold() == ".zip":
            _expand_zip(content, source_dir, name, used_paths)
            continue
        key = name.casefold()
        if key in used_paths:
            raise ValueError(f"源文件名冲突：{name}")
        used_paths.add(key)
        (source_dir / name).write_bytes(content)


def _read_json_document(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"转换输出必须是JSON对象：{path.name}")
    return value


def run_conversion_job(
    database: QraDatabase,
    job_id: str,
    *,
    runtime_root: Path | str | None = None,
) -> dict[str, Any]:
    database.set_conversion_running(job_id)
    try:
        job = database.get_conversion_job(job_id)
        sources = database.conversion_source_contents(job_id)
        profile_path = Path(str(job["profile_path"])).resolve()
        if not profile_path.is_relative_to(MAPPING_ROOT):
            raise ValueError("转换任务映射配置超出受控目录")
        effective_profile = load_profile(profile_path)
        if (
            effective_profile.profile_id != job["profile_id"]
            or effective_profile.version != job["profile_version"]
            or effective_profile.checksum_sha256 != job["profile_sha256"]
        ):
            raise ValueError("映射配置已变化，不能按旧版本任务继续转换")
        work_parent = Path(runtime_root or DEFAULT_RUNTIME_ROOT).resolve() / "conversions"
        work_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"{job_id}-", dir=work_parent) as temporary:
            root = Path(temporary)
            source_dir = root / "sources"
            output_dir = root / "outputs"
            source_dir.mkdir()
            database.update_conversion_progress(job_id, 20, "正在校验并展开源资料")
            _materialize_sources(sources, source_dir)
            decisions_path = None
            if job.get("review_decisions") is not None:
                decisions_path = root / "review_decisions.json"
                decisions_path.write_text(
                    json.dumps(
                        job["review_decisions"],
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            database.update_conversion_progress(job_id, 45, "正在读取、映射与合并数据")
            summary = convert_sources(
                source_dir=source_dir,
                profile_path=profile_path,
                output_dir=output_dir,
                case_id=job.get("case_id"),
                project_name=job.get("project_name"),
                contract_validator=validate_import_contract,
                capability_planner=lambda case: plan_dynamic_flow(case, None),
                review_decisions_path=decisions_path,
            )
            database.update_conversion_progress(job_id, 90, "正在固化转换预览与审计资料")
            payload = _read_json_document(output_dir / "case.json")
            report = _read_json_document(output_dir / "conversion_report.json")
            manifest = _read_json_document(output_dir / "source_manifest.json")
            preview = _read_json_document(output_dir / "conversion_preview.json")
            review_audit = _read_json_document(output_dir / "review_audit.json")
            blocked = summary.get("status") != "READY_FOR_REVIEW"
            database.complete_conversion(
                job_id,
                payload=payload,
                case_sha256=str(summary["case_sha256"]),
                source_manifest=manifest,
                conversion_report=report,
                preview=preview,
                review_audit=review_audit,
                blocked=blocked,
            )
    except Exception as exc:
        database.fail_conversion(
            job_id,
            {
                "code": "CONVERSION_EXECUTION_FAILED",
                "stage": "worker",
                "type": type(exc).__name__,
                "message": str(exc),
            },
        )
    return database.get_conversion_job(job_id, detailed=False)


__all__ = [
    "MAX_SOURCE_FILES",
    "MAX_UPLOAD_FILE_BYTES",
    "MAX_UPLOAD_TOTAL_BYTES",
    "SUPPORTED_UPLOAD_SUFFIXES",
    "conversion_dedupe_key",
    "list_mapping_profiles",
    "resolve_mapping_profile",
    "run_conversion_job",
    "submit_conversion",
    "validate_source_uploads",
]
