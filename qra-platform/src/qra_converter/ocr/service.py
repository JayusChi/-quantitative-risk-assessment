"""Retry, validation and cache boundary around an OCR provider."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path

from ..parsing.contracts import BoundingBox, canonical_json
from .ports import (
    OcrCell,
    OcrContractInvalid,
    OcrProvider,
    OcrProviderError,
    OcrRequest,
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
    ):
        self.provider = provider
        self.cache_dir = cache_dir
        self.max_retries = max(0, int(max_retries))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self._sleep = sleep
        self._cancel_check = cancel_check

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
            return response, True
        for attempt in range(self.max_retries + 1):
            if self._cancel_check is not None:
                self._cancel_check()
            try:
                response = self.provider.recognize(request)
                self._validate(response)
                break
            except OcrProviderError as exc:
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
