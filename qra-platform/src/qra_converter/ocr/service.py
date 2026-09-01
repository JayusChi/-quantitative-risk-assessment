"""Retry, validation and cache boundary around an OCR provider."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..model_audit import ModelAuditCallback, sanitized_error_message
from ..parsing.contracts import BoundingBox, canonical_json
from .ports import (
    OcrCell,
    OcrContractInvalid,
    OcrProvider,
    OcrProviderError,
    OcrRequest,
    OcrRequestTooLarge,
    OcrResponse,
    OcrTable,
    OcrTextBlock,
)


def ocr_cache_key(request: OcrRequest, provider: OcrProvider) -> str:
    payload = {
        "image_sha256": hashlib.sha256(request.image_bytes).hexdigest(),
        "provider_id": provider.provider_id,
        "model_version": provider.model_version,
        "languages": list(request.languages),
        "detect_tables": request.detect_tables,
        "task_type": request.task_type,
        "region_kind": request.region_kind,
        "region_id": request.region_id,
        "tile_id": request.tile_id,
        "payload_policy_version": request.payload_policy_version,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class OcrService:
    def __init__(
        self,
        provider: OcrProvider,
        *,
        cache_dir: Path | None = None,
        max_retries: int = 2,
        backoff_seconds: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
        cancel_check: Callable[[], None] | None = None,
        audit_callback: ModelAuditCallback | None = None,
        job_id: str | None = None,
    ):
        self.provider = provider
        self.cache_dir = cache_dir
        self.max_retries = max(0, int(max_retries))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self._sleep = sleep
        self._cancel_check = cancel_check
        self._audit_callback = audit_callback
        self._job_id = job_id

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def request_bytes(self, request: OcrRequest) -> bytes:
        builder = getattr(self.provider, "build_request_bytes", None)
        if callable(builder):
            return bytes(builder(request))
        return request.image_bytes

    def request_byte_count(self, request: OcrRequest) -> int:
        return len(self.request_bytes(request))

    def _audit(self, value: dict[str, object]) -> None:
        if self._audit_callback is not None:
            self._audit_callback(value)

    @staticmethod
    def _validate(response: OcrResponse) -> None:
        if not response.provider_id or not response.model_version:
            raise OcrContractInvalid("OCR响应缺少提供方或模型版本")
        for block in response.text_blocks:
            if not 0 <= block.confidence <= 1 or block.bbox.width < 0 or block.bbox.height < 0:
                raise OcrContractInvalid("OCR文本块置信度或坐标非法")
        for table in response.tables:
            if table.row_count < 0 or table.column_count < 0:
                raise OcrContractInvalid("OCR表格尺寸非法")
            if any(
                cell.row_index < 0
                or cell.column_index < 0
                or cell.row_index >= table.row_count
                or cell.column_index >= table.column_count
                for cell in table.cells
            ):
                raise OcrContractInvalid("OCR单元格索引越界")

    @staticmethod
    def _dump(response: OcrResponse) -> dict[str, object]:
        return {
            "provider_id": response.provider_id,
            "model_version": response.model_version,
            "warnings": list(response.warnings),
            "raw_response_sha256": response.raw_response_sha256,
            "provider_request_id": response.provider_request_id,
            "finish_reason": response.finish_reason,
            "usage": response.usage,
            "text_blocks": [
                {
                    "text": block.text,
                    "bbox": [block.bbox.x, block.bbox.y, block.bbox.width, block.bbox.height],
                    "confidence": block.confidence,
                    "language": block.language,
                    "block_type": block.block_type,
                }
                for block in response.text_blocks
            ],
            "tables": [
                {
                    "row_count": table.row_count,
                    "column_count": table.column_count,
                    "bbox": (
                        [table.bbox.x, table.bbox.y, table.bbox.width, table.bbox.height]
                        if table.bbox
                        else None
                    ),
                    "confidence": table.confidence,
                    "cells": [
                        {
                            "row_index": cell.row_index,
                            "column_index": cell.column_index,
                            "text": cell.text,
                            "bbox": (
                                [cell.bbox.x, cell.bbox.y, cell.bbox.width, cell.bbox.height]
                                if cell.bbox
                                else None
                            ),
                            "confidence": cell.confidence,
                            "row_span": cell.row_span,
                            "column_span": cell.column_span,
                        }
                        for cell in table.cells
                    ],
                }
                for table in response.tables
            ],
        }

    @staticmethod
    def _load(value: dict[str, object]) -> OcrResponse:
        def bbox(raw: object) -> BoundingBox | None:
            if raw is None:
                return None
            return BoundingBox(*(float(item) for item in raw))  # type: ignore[arg-type]

        def required_bbox(raw: object) -> BoundingBox:
            value = bbox(raw)
            if value is None:
                raise OcrContractInvalid("OCR缓存文本块缺少bbox")
            return value

        return OcrResponse(
            provider_id=str(value["provider_id"]),
            model_version=str(value["model_version"]),
            warnings=tuple(str(item) for item in value.get("warnings", [])),  # type: ignore[arg-type]
            raw_response_sha256=str(value.get("raw_response_sha256") or ""),
            provider_request_id=(
                str(value["provider_request_id"]) if value.get("provider_request_id") else None
            ),
            finish_reason=str(value.get("finish_reason") or "stop"),
            usage=(dict(value.get("usage") or {})),  # type: ignore[arg-type]
            text_blocks=tuple(
                OcrTextBlock(
                    text=str(item["text"]),
                    bbox=required_bbox(item["bbox"]),
                    confidence=float(item["confidence"]),
                    language=str(item["language"]) if item.get("language") else None,
                    block_type=str(item.get("block_type") or "PARAGRAPH"),
                )
                for item in value.get("text_blocks", [])  # type: ignore[union-attr]
            ),
            tables=tuple(
                OcrTable(
                    row_count=int(table["row_count"]),
                    column_count=int(table["column_count"]),
                    bbox=bbox(table.get("bbox")),
                    confidence=float(table.get("confidence", 0.8)),
                    cells=tuple(
                        OcrCell(
                            row_index=int(cell["row_index"]),
                            column_index=int(cell["column_index"]),
                            text=str(cell.get("text", "")),
                            bbox=bbox(cell.get("bbox")),
                            confidence=(
                                float(cell["confidence"])
                                if cell.get("confidence") is not None
                                else None
                            ),
                            row_span=int(cell.get("row_span", 1)),
                            column_span=int(cell.get("column_span", 1)),
                        )
                        for cell in table.get("cells", [])
                    ),
                )
                for table in value.get("tables", [])  # type: ignore[union-attr]
            ),
        )

    def recognize(self, request: OcrRequest) -> tuple[OcrResponse, bool]:
        key = ocr_cache_key(request, self.provider)
        cache_path = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if cache_path and cache_path.is_file():
            try:
                cached_value = json.loads(cache_path.read_text(encoding="utf-8"))
                if not isinstance(cached_value, dict):
                    raise TypeError
                response = self._load(cached_value)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise OcrContractInvalid("OCR缓存内容不符合响应合同") from exc
            self._validate(response)
            now = self._now()
            self._audit(
                {
                    "id": f"MCALL-{uuid4()}",
                    "job_id": self._job_id,
                    "source_id": request.source_id,
                    "call_kind": "OCR",
                    "task_type": request.task_type,
                    "logical_request_id": request.request_id,
                    "parent_call_id": request.parent_call_id,
                    "page_number": request.page_number,
                    "region_id": request.region_id,
                    "tile_id": request.tile_id,
                    "attempt_number": 0,
                    "provider_id": self.provider.provider_id,
                    "model_version": self.provider.model_version,
                    "status": "CACHED",
                    "started_at": now,
                    "finished_at": now,
                    "elapsed_ms": 0,
                    "input_sha256": key,
                    "input_byte_count": 0,
                    "media_sha256": request.media_sha256
                    or hashlib.sha256(request.image_bytes).hexdigest(),
                    "media_content_type": request.image_content_type,
                    "width": request.width,
                    "height": request.height,
                    "payload_policy_version": request.payload_policy_version,
                    "raw_response_sha256": response.raw_response_sha256,
                    "retryable": False,
                    "provider_request_id": response.provider_request_id,
                    "usage": response.usage,
                    "cache_hit": True,
                }
            )
            return response, True
        for attempt in range(self.max_retries + 1):
            if self._cancel_check is not None:
                self._cancel_check()
            outbound = b""
            try:
                started_iso = self._now()
                started = time.perf_counter()
                call_id = f"MCALL-{uuid4()}"
                try:
                    outbound = self.request_bytes(request)
                except OcrProviderError as exc:
                    self._audit(
                        {
                            "id": call_id,
                            "job_id": self._job_id,
                            "source_id": request.source_id,
                            "call_kind": "OCR",
                            "task_type": request.task_type,
                            "logical_request_id": request.request_id,
                            "parent_call_id": request.parent_call_id,
                            "page_number": request.page_number,
                            "region_id": request.region_id,
                            "tile_id": request.tile_id,
                            "attempt_number": attempt + 1,
                            "provider_id": self.provider.provider_id,
                            "model_version": self.provider.model_version,
                            "status": "SKIPPED",
                            "started_at": started_iso,
                            "finished_at": self._now(),
                            "elapsed_ms": 0,
                            "input_sha256": hashlib.sha256(request.image_bytes).hexdigest(),
                            "input_byte_count": 0,
                            "media_sha256": request.media_sha256
                            or hashlib.sha256(request.image_bytes).hexdigest(),
                            "media_content_type": request.image_content_type,
                            "width": request.width,
                            "height": request.height,
                            "payload_policy_version": request.payload_policy_version,
                            "retryable": exc.retryable,
                            "error_code": exc.code,
                            "sanitized_error_message": sanitized_error_message(exc),
                        }
                    )
                    raise
                self._audit(
                    {
                        "id": call_id,
                        "job_id": self._job_id,
                        "source_id": request.source_id,
                        "call_kind": "OCR",
                        "task_type": request.task_type,
                        "logical_request_id": request.request_id,
                        "parent_call_id": request.parent_call_id,
                        "page_number": request.page_number,
                        "region_id": request.region_id,
                        "tile_id": request.tile_id,
                        "attempt_number": attempt + 1,
                        "provider_id": self.provider.provider_id,
                        "model_version": self.provider.model_version,
                        "status": "STARTED",
                        "started_at": started_iso,
                        "input_sha256": hashlib.sha256(outbound).hexdigest(),
                        "input_byte_count": len(outbound),
                        "media_sha256": request.media_sha256
                        or hashlib.sha256(request.image_bytes).hexdigest(),
                        "media_content_type": request.image_content_type,
                        "width": request.width,
                        "height": request.height,
                        "payload_policy_version": request.payload_policy_version,
                        "retryable": False,
                    }
                )
                response = self.provider.recognize(request)
                self._validate(response)
                self._audit(
                    {
                        "id": call_id,
                        "job_id": self._job_id,
                        "source_id": request.source_id,
                        "call_kind": "OCR",
                        "task_type": request.task_type,
                        "logical_request_id": request.request_id,
                        "parent_call_id": request.parent_call_id,
                        "page_number": request.page_number,
                        "region_id": request.region_id,
                        "tile_id": request.tile_id,
                        "attempt_number": attempt + 1,
                        "provider_id": response.provider_id,
                        "model_version": response.model_version,
                        "status": "COMPLETED",
                        "started_at": started_iso,
                        "finished_at": self._now(),
                        "elapsed_ms": round((time.perf_counter() - started) * 1000),
                        "input_sha256": hashlib.sha256(outbound).hexdigest(),
                        "input_byte_count": len(outbound),
                        "media_sha256": request.media_sha256
                        or hashlib.sha256(request.image_bytes).hexdigest(),
                        "media_content_type": request.image_content_type,
                        "width": request.width,
                        "height": request.height,
                        "payload_policy_version": request.payload_policy_version,
                        "provider_request_id": response.provider_request_id,
                        "raw_response_sha256": response.raw_response_sha256,
                        "retryable": False,
                        "usage": response.usage,
                    }
                )
                break
            except OcrProviderError as exc:
                if outbound:
                    self._audit(
                        {
                            "id": call_id,
                            "job_id": self._job_id,
                            "source_id": request.source_id,
                            "call_kind": "OCR",
                            "task_type": request.task_type,
                            "logical_request_id": request.request_id,
                            "parent_call_id": request.parent_call_id,
                            "page_number": request.page_number,
                            "region_id": request.region_id,
                            "tile_id": request.tile_id,
                            "attempt_number": attempt + 1,
                            "provider_id": self.provider.provider_id,
                            "model_version": self.provider.model_version,
                            "status": (
                                "SKIPPED"
                                if isinstance(exc, OcrRequestTooLarge)
                                else "FAILED"
                            ),
                            "started_at": started_iso,
                            "finished_at": self._now(),
                            "elapsed_ms": round((time.perf_counter() - started) * 1000),
                            "input_sha256": hashlib.sha256(outbound).hexdigest(),
                            "input_byte_count": len(outbound),
                            "media_sha256": request.media_sha256
                            or hashlib.sha256(request.image_bytes).hexdigest(),
                            "media_content_type": request.image_content_type,
                            "width": request.width,
                            "height": request.height,
                            "payload_policy_version": request.payload_policy_version,
                            "retryable": exc.retryable,
                            "error_code": exc.code,
                            "sanitized_error_message": sanitized_error_message(exc),
                        }
                    )
                if not exc.retryable or attempt >= self.max_retries:
                    raise
                self._sleep(self.backoff_seconds * (2**attempt))
        else:  # pragma: no cover - loop is total
            raise RuntimeError("OCR重试状态异常")
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(self._dump(response), ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
        return response, False


__all__ = ["OcrService", "ocr_cache_key"]
