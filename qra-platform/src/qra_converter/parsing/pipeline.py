"""Fixed stage-three parsing pipeline and artifact materialization."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import replace
from pathlib import Path, PurePosixPath

from ..contracts import IssueSeverity, SourceReference
from ..image_processing.preprocess import PREPROCESSING_VERSION
from ..ocr.aliyun_bailian import AliyunBailianOcrProvider
from ..ocr.disabled import DisabledOcrProvider
from ..ocr.http_provider import JsonHttpOcrProvider
from ..ocr.ports import OcrProvider
from ..ocr.service import OcrService
from .cache import ParsingCache, parsing_cache_key
from .contracts import (
    PARSING_CONTRACT_VERSION,
    ParsedDocument,
    ParseExecution,
    ParseIssue,
    PreviewResource,
    parsed_document_from_dict,
)
from .quality import build_quality_report, link_table_continuations, validate_locations
from .registry import ParseContext, ParsingRegistry, standalone_source


def configured_ocr_provider(*, model_version: str | None = None) -> OcrProvider:
    provider_name = os.environ.get("QRA_OCR_PROVIDER", "").strip().casefold()
    if provider_name in {"aliyun", "aliyun-bailian", "bailian"}:
        dashscope_url = os.environ.get("QRA_ALIYUN_DASHSCOPE_URL", "").strip()
        api_key = os.environ.get("QRA_ALIYUN_API_KEY", "").strip()
        selected_model = (
            str(model_version or "").strip()
            or os.environ.get("QRA_OCR_MODEL_VERSION", "qwen3.5-ocr").strip()
        )
        if not dashscope_url or not api_key or not selected_model:
            return DisabledOcrProvider()
        return AliyunBailianOcrProvider(
            dashscope_url=dashscope_url,
            api_key=api_key,
            model_version=selected_model,
        )
    endpoint = os.environ.get("QRA_OCR_ENDPOINT", "").strip()
    api_key = os.environ.get("QRA_OCR_API_KEY", "").strip()
    selected_model = (
        str(model_version or "").strip()
        or os.environ.get("QRA_OCR_MODEL_VERSION", "").strip()
    )
    if not endpoint or not api_key or not selected_model:
        return DisabledOcrProvider()
    return JsonHttpOcrProvider(
        endpoint=endpoint,
        api_key=api_key,
        model_version=selected_model,
    )


def real_ocr_configured() -> bool:
    provider_name = os.environ.get("QRA_OCR_PROVIDER", "").strip().casefold()
    if provider_name in {"aliyun", "aliyun-bailian", "bailian"}:
        return all(
            os.environ.get(name)
            for name in ("QRA_ALIYUN_DASHSCOPE_URL", "QRA_ALIYUN_API_KEY")
        )
    return all(
        os.environ.get(name)
        for name in ("QRA_OCR_ENDPOINT", "QRA_OCR_API_KEY", "QRA_OCR_MODEL_VERSION")
    )


def _safe_resource_path(value: str) -> PurePosixPath:
    relative = PurePosixPath(value.replace("\\", "/"))
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("解析预览资源路径无效")
    return relative


class ParsingPipeline:
    def __init__(
        self,
        *,
        output_root: Path,
        cache_root: Path | None = None,
        registry: ParsingRegistry | None = None,
        ocr_provider: OcrProvider | None = None,
        cancel_check=None,
        page_progress=None,
    ):
        self.output_root = output_root
        self.cache = ParsingCache(cache_root) if cache_root else None
        self.registry = registry or ParsingRegistry()
        self.ocr_provider = ocr_provider or configured_ocr_provider()
        self.cancel_check = cancel_check
        self.page_progress = page_progress

    @staticmethod
    def _source_directory_name(source_id: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9_.-]{1,180}", source_id):
            return source_id
        return hashlib.sha256(source_id.encode("utf-8")).hexdigest()

    def _artifact_dir(self, source_id: str) -> Path:
        return self.output_root / "parsed" / self._source_directory_name(source_id)

    def _write_artifacts(
        self,
        directory: Path,
        document: ParsedDocument,
        quality_report: dict[str, object],
        resources: tuple[PreviewResource, ...],
    ) -> dict[str, object]:
        directory.mkdir(parents=True, exist_ok=True)
        manifest_resources: list[dict[str, object]] = []
        for resource in resources:
            relative = _safe_resource_path(resource.path)
            target = directory.joinpath(*relative.parts).resolve()
            if not target.is_relative_to(directory.resolve()):
                raise ValueError("解析预览资源路径超出产物目录")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(resource.content)
            manifest_resources.append(
                {
                    "path": relative.as_posix(),
                    "content_type": resource.content_type,
                    "byte_count": len(resource.content),
                    "sha256": hashlib.sha256(resource.content).hexdigest(),
                }
            )
        preview_manifest: dict[str, object] = {
            "document_id": document.document_id,
            "source_sha256": document.source.checksum_sha256,
            "parse_sha256": document.parse_sha256,
            "parsing_provenance": document.metadata.get("parsing_provenance", {}),
            "resources": sorted(manifest_resources, key=lambda item: str(item["path"])),
        }
        documents = {
            "parsed_document.json": document.to_dict(),
            "quality_report.json": quality_report,
            "preview_manifest.json": preview_manifest,
        }
        for name, payload in documents.items():
            (directory / name).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        return preview_manifest

    def _with_provenance(
        self,
        document: ParsedDocument,
        *,
        cache_key: str,
        ocr_parameters: dict[str, object],
    ) -> ParsedDocument:
        provenance = {
            "source_sha256": document.source.checksum_sha256,
            "parser_id": document.parser_id,
            "parser_version": document.parser_version,
            "ocr_provider_id": self.ocr_provider.provider_id,
            "ocr_model_version": self.ocr_provider.model_version,
            "preprocessing_profile": PREPROCESSING_VERSION,
            "contract_version": PARSING_CONTRACT_VERSION,
            "ocr_parameters": ocr_parameters,
            "cache_key": cache_key,
        }
        return replace(
            document,
            metadata={**document.metadata, "parsing_provenance": provenance},
            parse_sha256="",
        )

    def parse_path(
        self,
        path: Path,
        *,
        detected_media_type: str | None = None,
        source: SourceReference | None = None,
    ) -> ParseExecution:
        if self.cancel_check:
            self.cancel_check()
        media_type = self.registry.media_type_for(path, detected_media_type)
        reader = self.registry.reader_for(media_type)
        effective_source = source or standalone_source(path, reader.reader_id)
        effective_source = replace(effective_source, reader_id=reader.reader_id)
        artifact_dir = self._artifact_dir(effective_source.source_id)
        try:
            ocr_timeout_seconds = float(os.environ.get("QRA_OCR_TIMEOUT_SECONDS", "120"))
        except ValueError:
            ocr_timeout_seconds = 120.0
        if ocr_timeout_seconds <= 0:
            ocr_timeout_seconds = 120.0
        ocr_parameters: dict[str, object] = {
            "languages": ["zh-Hans", "en"],
            "detect_tables": True,
            "timeout_seconds": ocr_timeout_seconds,
        }
        cache_key = parsing_cache_key(
            source_sha256=effective_source.checksum_sha256,
            parser_id=reader.reader_id,
            parser_version=reader.parser_version,
            ocr_provider_id=self.ocr_provider.provider_id,
            ocr_model_version=self.ocr_provider.model_version,
            preprocessing_profile=PREPROCESSING_VERSION,
            contract_version=PARSING_CONTRACT_VERSION,
            ocr_parameters=ocr_parameters,
        )
        started = time.perf_counter()
        if self.cache and self.cache.restore(cache_key, artifact_dir):
            raw = json.loads((artifact_dir / "parsed_document.json").read_text(encoding="utf-8"))
            cached = parsed_document_from_dict(raw)
            rebound = replace(
                cached,
                document_id=effective_source.source_id,
                source=effective_source,
            )
            document = self._with_provenance(
                rebound,
                cache_key=cache_key,
                ocr_parameters=ocr_parameters,
            ).finalized()
            quality = build_quality_report(document, round((time.perf_counter() - started) * 1000))
            resources: list[PreviewResource] = []
            old_manifest = json.loads(
                (artifact_dir / "preview_manifest.json").read_text(encoding="utf-8")
            )
            for item in old_manifest.get("resources", []):
                relative = _safe_resource_path(str(item["path"]))
                content = artifact_dir.joinpath(*relative.parts).read_bytes()
                resources.append(
                    PreviewResource(relative.as_posix(), str(item["content_type"]), content)
                )
            preview = self._write_artifacts(artifact_dir, document, quality, tuple(resources))
            return ParseExecution(
                document=document,
                quality_report=quality,
                preview_manifest=preview,
                artifact_dir=str(artifact_dir),
                cache_hit=True,
                succeeded=not document.has_errors,
            )

        ocr_cache_dir = self.cache.root / "ocr" if self.cache else None
        try:
            max_retries = max(0, int(os.environ.get("QRA_OCR_MAX_RETRIES", "2")))
        except ValueError:
            max_retries = 2
        ocr_service = OcrService(
            self.ocr_provider,
            cache_dir=ocr_cache_dir,
            max_retries=max_retries,
            cancel_check=self.cancel_check,
        )
        context = ParseContext(
            source=effective_source,
            media_type=media_type,
            ocr_service=ocr_service,
            ocr_timeout_seconds=ocr_timeout_seconds,
            cancel_check=self.cancel_check,
            page_progress=self.page_progress,
        )
        try:
            output = reader.parse(path, context)
        except (OSError, RuntimeError, ValueError) as exc:
            document = ParsedDocument(
                document_id=effective_source.source_id,
                source=effective_source,
                media_type=media_type,
                document_kind="UNREADABLE",
                parser_id=reader.reader_id,
                parser_version=reader.parser_version,
                page_count=0,
                issues=(
                    ParseIssue(
                        "PARSE.DOCUMENT_CORRUPT",
                        str(exc),
                        IssueSeverity.ERROR,
                    ),
                ),
            )
            output_resources: tuple[PreviewResource, ...] = ()
        else:
            document = output.document
            output_resources = output.resources
        document = self._with_provenance(
            validate_locations(link_table_continuations(document)),
            cache_key=cache_key,
            ocr_parameters=ocr_parameters,
        ).finalized()
        quality = build_quality_report(document, round((time.perf_counter() - started) * 1000))
        preview = self._write_artifacts(artifact_dir, document, quality, output_resources)
        if self.cache:
            self.cache.store(cache_key, artifact_dir)
        return ParseExecution(
            document=document,
            quality_report=quality,
            preview_manifest=preview,
            artifact_dir=str(artifact_dir),
            cache_hit=False,
            succeeded=not document.has_errors,
        )


__all__ = ["ParsingPipeline", "configured_ocr_provider", "real_ocr_configured"]
