from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from qra_converter.contract_catalog import load_contract_catalog
from qra_converter.extraction.aliyun_bailian import configured_extraction_provider
from qra_converter.mapping import load_profile
from qra_converter.orchestration.contracts import StepResult
from qra_converter.parsing.pipeline import configured_ocr_provider
from qra_converter.service import CONVERTER_VERSION, convert_sources
from qra_engine.dynamic import plan_dynamic_flow
from qra_engine.validation import validate_import_contract

from .database import QraDatabase
from .file_intake import (
    INTAKE_RULES_VERSION,
    MAX_SOURCE_FILES,
    MAX_UPLOAD_FILE_BYTES,
    MAX_UPLOAD_TOTAL_BYTES,
    SUPPORTED_SOURCE_SUFFIXES,
    SUPPORTED_UPLOAD_SUFFIXES,
    intake_files,
)
from .ocr_settings import SUPPORTED_EXTRACTION_MODELS, SUPPORTED_OCR_MODELS
from .paths import DEFAULT_RUNTIME_ROOT, PROJECT_ROOT

MAPPING_ROOT = (PROJECT_ROOT / "resources" / "mappings").resolve()
CONTRACT_ROOT = (PROJECT_ROOT / "resources" / "contracts" / "part1" / "v1").resolve()


def _clean_actor(value: str | None) -> str:
    actor = str(value or "local-admin").strip()
    if not actor:
        actor = "local-admin"
    if len(actor) > 120 or any(ord(character) < 32 for character in actor):
        raise ValueError("操作人标识无效")
    return actor


def validate_source_uploads(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return intake_files(files).sources


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
                "contract_id": profile.contract_id,
                "contract_version": profile.contract_version,
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


def conversion_dedupe_key(
    sources: list[dict[str, Any]],
    profile_sha256: str,
    contract_sha256: str | None = None,
    intake_rules_version: str = INTAKE_RULES_VERSION,
    external_sharing_allowed: bool = False,
    ocr_provider_id: str | None = None,
    ocr_model_version: str | None = None,
    extraction_provider_id: str | None = None,
    extraction_model_version: str | None = None,
) -> str:
    fingerprint = {
        "mapping_sha256": profile_sha256,
        "contract_sha256": contract_sha256,
        "intake_rules_version": intake_rules_version,
        "external_sharing_allowed": bool(external_sharing_allowed),
        "ocr_provider_id": ocr_provider_id,
        "ocr_model_version": ocr_model_version,
        "extraction_provider_id": extraction_provider_id,
        "extraction_model_version": extraction_model_version,
        "sources": sorted(
            (
                {
                    "file_name": str(source.get("relative_path") or source["file_name"]).casefold(),
                    "sha256": str(source["sha256"]),
                    "archive_name": str(source.get("archive_name") or "").casefold(),
                }
                for source in sources
            ),
            key=lambda row: (row["archive_name"], row["file_name"], row["sha256"]),
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
    contract: str | None = None,
    failure_policy: str = "ALL_OR_NOTHING",
    external_sharing_allowed: bool = False,
    ocr_model_version: str | None = None,
    extraction_model_version: str | None = None,
) -> tuple[str, bool]:
    contract_request = contract
    if failure_policy not in {"ALL_OR_NOTHING", "QUARANTINE_AND_CONTINUE"}:
        raise ValueError("failure_policy必须为ALL_OR_NOTHING或QUARANTINE_AND_CONTINUE")
    intake = intake_files(files)
    sources = intake.sources
    profile_path, metadata = resolve_mapping_profile(profile)
    if not metadata.get("contract_id") or not metadata.get("contract_version"):
        raise ValueError("平台转换映射必须声明contract_id/contract_version")
    catalog = load_contract_catalog(
        CONTRACT_ROOT,
        expected_contract_id=str(metadata["contract_id"]),
        expected_version=str(metadata["contract_version"]),
    )
    expected_contract = f"{catalog.contract_id}/{catalog.version}"
    if contract_request is not None:
        requested_contract = str(contract_request).strip()
        if requested_contract and requested_contract != expected_contract:
            raise ValueError(f"请求合同与映射固化合同不一致：应为{expected_contract}")
    quarantined = intake.quarantined_count > 0
    initial_status = (
        "BLOCKED"
        if intake.ready_count == 0 or (quarantined and failure_policy == "ALL_OR_NOTHING")
        else "QUEUED"
    )
    requested_ocr_model = str(ocr_model_version or "").strip() or None
    if requested_ocr_model is not None and requested_ocr_model not in SUPPORTED_OCR_MODELS:
        raise ValueError("OCR模型不在当前业务空间允许的模型中")
    requested_extraction_model = str(extraction_model_version or "").strip() or None
    if (
        requested_extraction_model is not None
        and requested_extraction_model not in SUPPORTED_EXTRACTION_MODELS
    ):
        raise ValueError("信息提取模型不在当前业务空间允许的模型中")
    ocr_provider = configured_ocr_provider(model_version=requested_ocr_model)
    ocr_provider_id = (
        ocr_provider.provider_id if ocr_provider.provider_id != "disabled" else None
    )
    selected_ocr_model = (
        ocr_provider.model_version if ocr_provider_id is not None else requested_ocr_model
    )
    extraction_provider = configured_extraction_provider(
        model_version=requested_extraction_model
    )
    extraction_provider_id = (
        extraction_provider.provider_id if extraction_provider is not None else None
    )
    selected_extraction_model = (
        extraction_provider.model_version
        if extraction_provider is not None
        else requested_extraction_model
    )
    job_id, created = database.create_conversion_job(
        dedupe_key=conversion_dedupe_key(
            sources,
            str(metadata["sha256"]),
            catalog.manifest_sha256,
            external_sharing_allowed=external_sharing_allowed,
            ocr_provider_id=ocr_provider_id,
            ocr_model_version=selected_ocr_model,
            extraction_provider_id=extraction_provider_id,
            extraction_model_version=selected_extraction_model,
        ),
        profile_id=str(metadata["profile_id"]),
        profile_version=str(metadata["version"]),
        profile_sha256=str(metadata["sha256"]),
        profile_path=str(profile_path),
        contract_id=catalog.contract_id,
        contract_version=catalog.version,
        contract_sha256=catalog.manifest_sha256,
        contract_path=str(catalog.root),
        failure_policy=failure_policy,
        intake_rules_version=INTAKE_RULES_VERSION,
        file_manifest_sha256=intake.file_manifest_sha256,
        intake_issues=[issue.to_dict() for issue in intake.issues],
        converter_version=CONVERTER_VERSION,
        sources=sources,
        case_id=str(case_id).strip()[:160] if case_id else None,
        project_name=str(project_name).strip()[:160] if project_name else None,
        external_sharing_allowed=external_sharing_allowed,
        ocr_provider_id=ocr_provider_id,
        ocr_model_version=selected_ocr_model,
        extraction_provider_id=extraction_provider_id,
        extraction_model_version=selected_extraction_model,
        review_decisions=review_decisions,
        batch_id=batch_id,
        actor=_clean_actor(actor),
        initial_status=initial_status,
    )
    return job_id, created


class _ConversionCancelled(RuntimeError):
    pass


class _DatabaseWorkflowStore:
    def __init__(self, database: QraDatabase) -> None:
        self.database = database

    def load_step(self, job_id: str, step: str, input_sha256: str) -> StepResult | None:
        value = self.database.get_extraction_step(job_id, step, input_sha256)
        return StepResult(**value) if value is not None else None

    def save_step(self, job_id: str, result: StepResult) -> None:
        self.database.save_extraction_step(job_id, result.to_dict())

    def is_cancel_requested(self, job_id: str) -> bool:
        return self.database.is_conversion_cancel_requested(job_id)


def _check_cancel(database: QraDatabase, job_id: str) -> None:
    if database.is_conversion_cancel_requested(job_id):
        raise _ConversionCancelled("转换任务取消请求已生效")


def _safe_relative_path(value: str) -> PurePosixPath:
    raw = value.replace("\\", "/")
    member = PurePosixPath(raw)
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{index}" for prefix in ("COM", "LPT") for index in range(1, 10)
    }
    if (
        member.is_absolute()
        or raw.startswith("//")
        or re.match(r"^[A-Za-z]:($|/)", raw)
        or not member.parts
        or any(
            part in {"", ".", ".."}
            or part.endswith((" ", "."))
            or part.partition(".")[0].casefold().upper() in reserved
            or any(character in '<>:"|?*' or ord(character) < 32 for character in part)
            for part in member.parts
        )
    ):
        raise ValueError("登记的源文件相对路径无效")
    return member


def _materialize_sources(
    sources: list[dict[str, Any]],
    source_dir: Path,
    *,
    cancel_check: Any | None = None,
) -> dict[str, dict[str, Any]]:
    used_paths: set[str] = set()
    metadata_by_path: dict[str, dict[str, Any]] = {}
    for source in sources:
        if cancel_check is not None:
            cancel_check()
        content = bytes(source["content"])
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != source["sha256"] or len(content) != source["byte_count"]:
            raise ValueError("持久化源文件完整性校验失败")
        member = _safe_relative_path(str(source.get("relative_path") or source["file_name"]))
        target = source_dir.joinpath(*member.parts).resolve()
        key = target.relative_to(source_dir.resolve()).as_posix().casefold()
        if key in used_paths:
            archive_name = str(source.get("archive_name") or "")
            archive_key = hashlib.sha256(archive_name.encode("utf-8")).hexdigest()[:12]
            target = source_dir.joinpath("__archives__", archive_key, *member.parts).resolve()
            key = target.relative_to(source_dir.resolve()).as_posix().casefold()
        if not target.is_relative_to(source_dir.resolve()) or key in used_paths:
            raise ValueError("登记的源文件路径冲突或超出任务目录")
        used_paths.add(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        relative_target = target.relative_to(source_dir.resolve()).as_posix()
        metadata_by_path[relative_target] = {
            key: value for key, value in source.items() if key != "content"
        }
    return metadata_by_path


def _parse_artifacts(execution: Any) -> list[dict[str, Any]]:
    if not execution.artifact_dir:
        return []
    root = Path(str(execution.artifact_dir)).resolve()
    required = {"parsed_document.json", "quality_report.json", "preview_manifest.json"}
    if not root.is_dir() or not required.issubset(
        {path.name for path in root.iterdir() if path.is_file()}
    ):
        raise ValueError("解析产物目录缺少必需清单")
    artifacts: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        if path.suffix.casefold() == ".json":
            content_type = "application/json"
        elif path.suffix.casefold() == ".png":
            content_type = "image/png"
        elif path.suffix.casefold() in {".jpg", ".jpeg"}:
            content_type = "image/jpeg"
        else:
            content_type = "application/octet-stream"
        kind = {
            "parsed_document.json": "PARSED_DOCUMENT",
            "quality_report.json": "QUALITY_REPORT",
            "preview_manifest.json": "PREVIEW_MANIFEST",
        }.get(relative, "PREVIEW_RESOURCE")
        artifacts.append(
            {
                "path": relative,
                "artifact_kind": kind,
                "content_type": content_type,
                "content": content,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    return artifacts


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
    try:
        database.set_conversion_running(job_id)
        _check_cancel(database, job_id)
        job = database.get_conversion_job(job_id)
        sources = database.conversion_source_contents(job_id, ready_only=True)
        if not sources:
            raise ValueError("转换任务没有可交给解析器的源文件")
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
        contract_path = Path(str(job["contract_path"])).resolve()
        if contract_path != CONTRACT_ROOT:
            raise ValueError("转换任务合同路径超出受控目录")
        load_contract_catalog(
            contract_path,
            expected_contract_id=str(job["contract_id"]),
            expected_version=str(job["contract_version"]),
            expected_manifest_sha256=str(job["contract_sha256"]),
        )
        work_parent = Path(runtime_root or DEFAULT_RUNTIME_ROOT).resolve() / "conversions"
        work_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"{job_id}-", dir=work_parent) as temporary:
            root = Path(temporary)
            source_dir = root / "sources"
            output_dir = root / "outputs"
            source_dir.mkdir()
            database.update_conversion_progress(job_id, 12, "正在复核并物化源资料")
            source_metadata = _materialize_sources(
                sources,
                source_dir,
                cancel_check=lambda: _check_cancel(database, job_id),
            )
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
            database.update_conversion_progress(job_id, 20, "正在启动逐文件解析")
            _check_cancel(database, job_id)

            def parse_progress(
                file_index: int,
                file_total: int,
                page: int,
                page_total: int,
                message: str,
            ) -> None:
                _check_cancel(database, job_id)
                file_fraction = (file_index - 1) + page / max(1, page_total)
                progress = 20 + round(55 * file_fraction / max(1, file_total))
                database.update_conversion_progress(
                    job_id,
                    progress,
                    f"{message}（文件{file_index}/{file_total}，页/结构{page}/{page_total}）",
                )

            def parsed_source_callback(path: Path, execution: Any) -> None:
                _check_cancel(database, job_id)
                first_error = next(
                    (
                        issue
                        for issue in execution.document.issues
                        if issue.severity.value == "ERROR"
                    ),
                    None,
                )
                database.record_conversion_source_parse(
                    job_id,
                    execution.document.source.source_id,
                    succeeded=bool(execution.succeeded),
                    parser_id=execution.document.parser_id,
                    parser_version=execution.document.parser_version,
                    parse_sha256=execution.document.parse_sha256,
                    quality_summary={
                        **dict(execution.quality_report.get("summary") or {}),
                        "cache_hit": bool(execution.cache_hit),
                    },
                    artifacts=_parse_artifacts(execution),
                    issue_code=first_error.code if first_error else None,
                    issue_message=first_error.message if first_error else None,
                )

            summary = convert_sources(
                source_dir=source_dir,
                profile_path=profile_path,
                output_dir=output_dir,
                case_id=job.get("case_id"),
                project_name=job.get("project_name"),
                contract_validator=validate_import_contract,
                capability_planner=lambda case: plan_dynamic_flow(case, None),
                review_decisions_path=decisions_path,
                contract_dir=contract_path,
                source_metadata=source_metadata,
                parse_cache_dir=work_parent / "parse-cache",
                cancel_check=lambda: _check_cancel(database, job_id),
                parse_result_callback=parsed_source_callback,
                parse_progress_callback=parse_progress,
                ocr_provider=configured_ocr_provider(
                    model_version=job.get("ocr_model_version")
                ),
                enable_stage4=True,
                stage4_job_id=job_id,
                extraction_provider=configured_extraction_provider(
                    model_version=job.get("extraction_model_version")
                ),
                stage4_external_sharing_allowed=bool(
                    job.get("external_sharing_allowed")
                ),
                stage4_store=_DatabaseWorkflowStore(database),
                stage4_result_callback=lambda result: database.save_stage4_result(
                    job_id, result
                ),
            )
            _check_cancel(database, job_id)
            database.update_conversion_progress(job_id, 88, "正在固化转换预览与审计资料")
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
    except _ConversionCancelled:
        database.finalize_conversion_cancel(job_id)
    except Exception as exc:
        message = re.sub(
            r"(?:[A-Za-z]:[\\/]|/)(?:[^\s:]+[\\/])*[^\s:]*",
            "[受控路径]",
            str(exc),
        )
        database.fail_conversion(
            job_id,
            {
                "code": "CONVERSION_EXECUTION_FAILED",
                "stage": "worker",
                "type": type(exc).__name__,
                "message": message,
            },
        )
    return database.get_conversion_job(job_id, detailed=False)


__all__ = [
    "INTAKE_RULES_VERSION",
    "MAX_SOURCE_FILES",
    "MAX_UPLOAD_FILE_BYTES",
    "MAX_UPLOAD_TOTAL_BYTES",
    "SUPPORTED_SOURCE_SUFFIXES",
    "SUPPORTED_UPLOAD_SUFFIXES",
    "conversion_dedupe_key",
    "list_mapping_profiles",
    "resolve_mapping_profile",
    "run_conversion_job",
    "submit_conversion",
    "validate_source_uploads",
]
