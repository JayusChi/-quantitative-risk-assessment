"""Deployable JSON-over-HTTP OCR adapter configured only from deployment settings."""

from __future__ import annotations

import base64
import hashlib
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..parsing.contracts import BoundingBox, canonical_json
from .payload_policy import OcrPayloadPolicy
from .ports import (
    OcrAuthenticationFailed,
    OcrCell,
    OcrContractInvalid,
    OcrRateLimited,
    OcrRequest,
    OcrRequestTooLarge,
    OcrResponse,
    OcrTable,
    OcrTextBlock,
    OcrTimeout,
    OcrUnreadable,
)


class JsonHttpOcrProvider:
    """Small vendor-neutral adapter for endpoints implementing the documented JSON shape."""

    provider_id = "json-http"

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model_version: str,
        payload_policy: OcrPayloadPolicy | None = None,
    ):
        if not endpoint.lower().startswith("https://"):
            raise ValueError("OCR端点必须使用HTTPS")
        self._endpoint = endpoint
        self._api_key = api_key
        self.model_version = model_version
        self.payload_policy = payload_policy or OcrPayloadPolicy.from_environment()

    @staticmethod
    def _bbox(value: object) -> BoundingBox:
        if not isinstance(value, list) or len(value) != 4:
            raise OcrContractInvalid("OCR bbox必须为[x,y,width,height]")
        try:
            bbox = BoundingBox(*(float(item) for item in value))
        except (TypeError, ValueError) as exc:
            raise OcrContractInvalid("OCR bbox包含非法数值") from exc
        if bbox.x < 0 or bbox.y < 0 or bbox.width < 0 or bbox.height < 0:
            raise OcrContractInvalid("OCR bbox不能为负")
        return bbox

    def build_request_bytes(self, request: OcrRequest) -> bytes:
        return canonical_json(
            {
                "image_base64": base64.b64encode(request.image_bytes).decode("ascii"),
                "width": request.width,
                "height": request.height,
                "languages": list(request.languages),
                "detect_tables": request.detect_tables,
                "request_id": request.request_id,
                "model_version": self.model_version,
            }
        ).encode("utf-8")

    def request_byte_count(self, request: OcrRequest) -> int:
        return len(self.build_request_bytes(request))

    def recognize(self, request: OcrRequest) -> OcrResponse:
        body = self.build_request_bytes(request)
        if len(body) > self.payload_policy.max_http_body_bytes:
            raise OcrRequestTooLarge("OCR请求在本地最终序列化校验时超过外发预算")
        outbound = Request(
            self._endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(outbound, timeout=request.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise OcrAuthenticationFailed("OCR提供方认证失败") from exc
            if exc.code == 429:
                raise OcrRateLimited("OCR提供方限流") from exc
            if exc.code in {408, 504}:
                raise OcrTimeout("OCR提供方超时") from exc
            if exc.code == 413:
                raise OcrRequestTooLarge("OCR提供方拒绝过大请求") from exc
            if exc.code == 422:
                raise OcrUnreadable("OCR提供方无法读取图像") from exc
            raise OcrUnreadable(f"OCR提供方返回HTTP {exc.code}") from exc
        except TimeoutError as exc:
            raise OcrTimeout("OCR提供方超时") from exc
        except URLError as exc:
            raise OcrTimeout("OCR提供方连接失败") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise TypeError
            blocks = tuple(
                OcrTextBlock(
                    text=str(item["text"]),
                    bbox=self._bbox(item["bbox"]),
                    confidence=float(item["confidence"]),
                    language=str(item["language"]) if item.get("language") else None,
                    block_type=str(item.get("block_type") or "PARAGRAPH"),
                )
                for item in payload.get("text_blocks", [])
            )
            tables = tuple(
                OcrTable(
                    row_count=int(table["row_count"]),
                    column_count=int(table["column_count"]),
                    bbox=self._bbox(table["bbox"]) if table.get("bbox") else None,
                    confidence=float(table.get("confidence", 0.8)),
                    cells=tuple(
                        OcrCell(
                            row_index=int(cell["row_index"]),
                            column_index=int(cell["column_index"]),
                            text=str(cell.get("text", "")),
                            bbox=self._bbox(cell["bbox"]) if cell.get("bbox") else None,
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
                for table in payload.get("tables", [])
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OcrContractInvalid("OCR提供方响应不符合合同") from exc
        if any(not 0 <= block.confidence <= 1 for block in blocks):
            raise OcrContractInvalid("OCR置信度超出[0,1]")
        return OcrResponse(
            provider_id=self.provider_id,
            model_version=self.model_version,
            text_blocks=blocks,
            tables=tables,
            warnings=tuple(str(item) for item in payload.get("warnings", [])),
            raw_response_sha256=hashlib.sha256(raw).hexdigest(),
            provider_request_id=(
                str(payload["provider_request_id"]) if payload.get("provider_request_id") else None
            ),
        )


__all__ = ["JsonHttpOcrProvider"]
