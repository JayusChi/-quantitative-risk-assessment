"""Deterministic OCR provider for golden fixtures."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from .ports import OcrRequest, OcrResponse, OcrUnreadable


class FixtureOcrProvider:
    provider_id = "fixture"
    model_version = "fixture-v1"

    def __init__(self, responses: Mapping[str, OcrResponse]):
        self._responses = dict(responses)

    def recognize(self, request: OcrRequest) -> OcrResponse:
        content_hash = hashlib.sha256(request.image_bytes).hexdigest()
        response = self._responses.get(request.request_id) or self._responses.get(content_hash)
        if response is None:
            raise OcrUnreadable(f"OCR夹具没有请求{request.request_id}的标注结果")
        return response


__all__ = ["FixtureOcrProvider"]
