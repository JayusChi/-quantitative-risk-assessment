"""Alibaba Cloud Bailian / DashScope OCR adapter.

The adapter deliberately uses the provider's structured high-precision OCR task so
that recognized text remains tied to source-image coordinates.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from ..parsing.contracts import BoundingBox, canonical_json
from .ports import (
    OcrAuthenticationFailed,
    OcrConnectionFailed,
    OcrContractInvalid,
    OcrRateLimited,
    OcrRequest,
    OcrResponse,
    OcrTextBlock,
    OcrTimeout,
    OcrUnreadable,
)

_GENERATION_PATH = "/api/v1/services/aigc/multimodal-generation/generation"
_VISION_OCR_PROMPT = (
    "请对这张图片进行高准确度OCR。原样抄录所有可见文字、数字、公式、表格和图注，"
    "保持自然阅读顺序；表格使用Markdown表格表示，公式尽量保留原格式；"
    "看不清的单个字符使用英文问号?，不得总结、解释或猜测。仅输出识别结果。"
)


class AliyunBailianOcrProvider:
    """Call a selected OCR-capable Qwen model through a workspace endpoint."""

    provider_id = "aliyun-bailian-dashscope"

    def __init__(
        self,
        *,
        dashscope_url: str,
        api_key: str,
        model_version: str = "qwen3.5-ocr",
        opener: Callable[..., object] = urlopen,
    ):
        self._endpoint = self._generation_endpoint(dashscope_url)
        if not api_key.strip():
            raise ValueError("阿里云百炼API Key不能为空")
        if not model_version.strip():
            raise ValueError("OCR模型名称不能为空")
        self._api_key = api_key.strip()
        self.model_version = model_version.strip()
        self._opener = opener

    @staticmethod
    def _generation_endpoint(value: str) -> str:
        parsed = urlsplit(value.strip().rstrip("/"))
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("阿里云百炼DashScope地址必须是无凭据、无查询参数的HTTPS地址")
        path = parsed.path.rstrip("/")
        if path.endswith(_GENERATION_PATH):
            endpoint_path = path
        elif path.endswith("/api/v1"):
            endpoint_path = f"{path}/services/aigc/multimodal-generation/generation"
        else:
            raise ValueError("阿里云百炼DashScope地址必须以/api/v1结尾")
        return urlunsplit((parsed.scheme, parsed.netloc, endpoint_path, "", ""))

    @staticmethod
    def _bbox(item: dict[str, object], request: OcrRequest) -> BoundingBox | None:
        location = item.get("location")
        if isinstance(location, list) and len(location) == 8:
            try:
                values = [float(value) for value in location]
            except (TypeError, ValueError) as exc:
                raise OcrContractInvalid("百炼OCR location包含非法数值") from exc
            xs = values[0::2]
            ys = values[1::2]
            x1, x2 = min(xs), max(xs)
            y1, y2 = min(ys), max(ys)
            if x1 < 0 or y1 < 0 or x2 < x1 or y2 < y1:
                raise OcrContractInvalid("百炼OCR location坐标非法")
            return BoundingBox(x1, y1, x2 - x1, y2 - y1)

        rotate_rect = item.get("rotate_rect")
        if isinstance(rotate_rect, list) and len(rotate_rect) >= 4:
            try:
                center_x, center_y, width, height = (
                    float(rotate_rect[index]) for index in range(4)
                )
            except (TypeError, ValueError) as exc:
                raise OcrContractInvalid("百炼OCR rotate_rect包含非法数值") from exc
            if width < 0 or height < 0:
                raise OcrContractInvalid("百炼OCR rotate_rect尺寸非法")
            return BoundingBox(center_x - width / 2, center_y - height / 2, width, height)

        return None

    @staticmethod
    def _confidence(item: dict[str, object]) -> tuple[float, bool]:
        raw = item.get("confidence", item.get("probability"))
        if raw is None:
            return 0.8, True
        try:
            confidence = float(raw)
        except (TypeError, ValueError) as exc:
            raise OcrContractInvalid("百炼OCR置信度包含非法数值") from exc
        if 1 < confidence <= 100:
            confidence /= 100
        if not 0 <= confidence <= 1:
            raise OcrContractInvalid("百炼OCR置信度超出[0,1]")
        return confidence, False

    @staticmethod
    def _content(payload: dict[str, object]) -> tuple[list[dict[str, object]], str | None]:
        try:
            output = payload["output"]
            choices = output["choices"]  # type: ignore[index]
            choice = choices[0]  # type: ignore[index]
            message = choice["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OcrContractInvalid("百炼OCR响应缺少output.choices.message.content") from exc
        finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
        if isinstance(content, str):
            return ([{"text": content}], str(finish_reason) if finish_reason else None)
        if not isinstance(content, list) or any(not isinstance(item, dict) for item in content):
            raise OcrContractInvalid("百炼OCR响应content不是对象数组")
        return (content, str(finish_reason) if finish_reason else None)

    def _request_bytes(self, request: OcrRequest) -> bytes:
        data_url = "data:image/png;base64," + base64.b64encode(request.image_bytes).decode(
            "ascii"
        )
        content: list[dict[str, object]] = [
            {
                "image": data_url,
                "min_pixels": 3072,
                "max_pixels": 8388608,
                "enable_rotate": False,
            }
        ]
        parameters: dict[str, object] = {"max_tokens": 16384, "seed": 1}
        if self.model_version.casefold() == "qwen3.5-ocr":
            parameters["ocr_options"] = {"task": "advanced_recognition"}
        else:
            content.append({"text": _VISION_OCR_PROMPT})
        return canonical_json(
            {
                "model": self.model_version,
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": content,
                        }
                    ]
                },
                "parameters": parameters,
            }
        ).encode("utf-8")

    @staticmethod
    def _http_error_detail(exc: HTTPError) -> str:
        try:
            body = exc.read()
            payload = json.loads(body.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            return f"HTTP {exc.code}"
        if not isinstance(payload, dict):
            return f"HTTP {exc.code}"
        error = payload.get("error")
        error_object = error if isinstance(error, dict) else {}
        code = str(payload.get("code") or error_object.get("code") or "").strip()
        message = str(
            payload.get("message")
            or error_object.get("message")
            or (error if isinstance(error, str) else "")
        ).strip()
        detail = ": ".join(part for part in (code, message) if part) or f"HTTP {exc.code}"
        detail = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED_API_KEY]", detail)
        return detail[:300]

    def recognize(self, request: OcrRequest) -> OcrResponse:
        outbound = Request(
            self._endpoint,
            data=self._request_bytes(request),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "qra-platform/aliyun-bailian-ocr",
            },
        )
        try:
            with self._opener(outbound, timeout=request.timeout_seconds) as response:  # type: ignore[attr-defined]
                raw = response.read()
        except HTTPError as exc:
            detail = self._http_error_detail(exc)
            if exc.code in {401, 403}:
                raise OcrAuthenticationFailed(f"阿里云百炼OCR认证失败（{detail}）") from exc
            if exc.code == 429:
                raise OcrRateLimited(f"阿里云百炼OCR触发限流（{detail}）") from exc
            if exc.code in {408, 504}:
                raise OcrTimeout(f"阿里云百炼OCR调用超时（{detail}）") from exc
            if exc.code in {400, 413, 415, 422}:
                raise OcrUnreadable(f"阿里云百炼OCR拒绝图像（{detail}）") from exc
            if 500 <= exc.code < 600:
                raise OcrConnectionFailed(f"阿里云百炼OCR服务异常（{detail}）") from exc
            raise OcrUnreadable(f"阿里云百炼OCR返回错误（{detail}）") from exc
        except TimeoutError as exc:
            raise OcrTimeout("阿里云百炼OCR调用超时") from exc
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise OcrTimeout("阿里云百炼OCR调用超时") from exc
            raise OcrConnectionFailed("阿里云百炼OCR连接失败") from exc

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OcrContractInvalid("阿里云百炼OCR未返回有效JSON") from exc
        if not isinstance(payload, dict):
            raise OcrContractInvalid("阿里云百炼OCR响应顶层不是对象")
        status_code = payload.get("status_code")
        if status_code not in (None, 200, "200") or payload.get("code"):
            raise OcrUnreadable(
                f"阿里云百炼OCR调用失败（{payload.get('code') or status_code}）"
            )

        content, finish_reason = self._content(payload)
        words: list[dict[str, object]] = []
        fallback_texts: list[str] = []
        for content_item in content:
            ocr_result = content_item.get("ocr_result")
            if isinstance(ocr_result, dict):
                raw_words = ocr_result.get("words_info")
                if isinstance(raw_words, list):
                    words.extend(item for item in raw_words if isinstance(item, dict))
            text = content_item.get("processed_text", content_item.get("text"))
            if isinstance(text, str) and text.strip():
                fallback_texts.append(text.strip())

        blocks: list[OcrTextBlock] = []
        used_default_confidence = False
        missing_locations = 0
        for item in words:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            bbox = self._bbox(item, request)
            if bbox is None:
                missing_locations += 1
                continue
            confidence, defaulted = self._confidence(item)
            used_default_confidence = used_default_confidence or defaulted
            blocks.append(
                OcrTextBlock(
                    text=text,
                    bbox=bbox,
                    confidence=confidence,
                    block_type="TEXT_LINE",
                )
            )

        warnings: list[str] = []
        if used_default_confidence:
            warnings.append("百炼高精识别未提供逐行置信度，平台按0.8记录并要求复核")
        if missing_locations:
            warnings.append(f"百炼响应中有{missing_locations}行缺少坐标，已从证据块中排除")
        if finish_reason == "length":
            warnings.append("百炼OCR输出达到长度上限，识别结果可能被截断")
        if not blocks and fallback_texts:
            warnings.append("百炼未返回逐行坐标，整页文本按低置信度证据保留")
            blocks.append(
                OcrTextBlock(
                    text="\n".join(fallback_texts),
                    bbox=BoundingBox(0, 0, float(request.width), float(request.height)),
                    confidence=0.5,
                    block_type="UNLOCATED_PAGE_TEXT",
                )
            )

        return OcrResponse(
            provider_id=self.provider_id,
            model_version=self.model_version,
            text_blocks=tuple(blocks),
            warnings=tuple(warnings),
            raw_response_sha256=hashlib.sha256(raw).hexdigest(),
            provider_request_id=(
                str(payload["request_id"]) if payload.get("request_id") else None
            ),
        )


__all__ = ["AliyunBailianOcrProvider"]
