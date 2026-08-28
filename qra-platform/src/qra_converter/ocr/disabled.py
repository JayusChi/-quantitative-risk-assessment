from __future__ import annotations

from .ports import OcrProviderNotConfigured, OcrRequest, OcrResponse


class DisabledOcrProvider:
    provider_id = "disabled"
    model_version = "none"

    def recognize(self, request: OcrRequest) -> OcrResponse:
        raise OcrProviderNotConfigured("部署环境未配置OCR提供方")


__all__ = ["DisabledOcrProvider"]
