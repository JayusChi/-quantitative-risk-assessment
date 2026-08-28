from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qra_converter.ocr.disabled import DisabledOcrProvider
from qra_converter.ocr.ports import (
    OcrProviderNotConfigured,
    OcrRateLimited,
    OcrRequest,
    OcrResponse,
)
from qra_converter.ocr.service import OcrService


class SequencedProvider:
    provider_id = "sequence"
    model_version = "1"

    def __init__(self):
        self.calls = 0

    def recognize(self, request: OcrRequest) -> OcrResponse:
        self.calls += 1
        if self.calls < 3:
            raise OcrRateLimited("limited")
        return OcrResponse(self.provider_id, self.model_version, raw_response_sha256="c" * 64)


class OcrServiceTests(unittest.TestCase):
    @staticmethod
    def request() -> OcrRequest:
        return OcrRequest(b"image", 10, 10, ("zh-Hans",), True, "REQ-1", 1.0)

    def test_disabled_provider_returns_structured_not_configured_error(self) -> None:
        with self.assertRaises(OcrProviderNotConfigured) as captured:
            OcrService(DisabledOcrProvider()).recognize(self.request())
        self.assertEqual(captured.exception.code, "PARSE.OCR_PROVIDER_NOT_CONFIGURED")

    def test_retry_is_bounded_exponential_and_success_is_cached(self) -> None:
        provider = SequencedProvider()
        sleeps: list[float] = []
        with tempfile.TemporaryDirectory() as temporary:
            service = OcrService(
                provider,
                cache_dir=Path(temporary),
                max_retries=2,
                backoff_seconds=0.5,
                sleep=sleeps.append,
            )
            response, cache_hit = service.recognize(self.request())
            cached_response, cached_hit = service.recognize(self.request())
        self.assertFalse(cache_hit)
        self.assertTrue(cached_hit)
        self.assertEqual(provider.calls, 3)
        self.assertEqual(sleeps, [0.5, 1.0])
        self.assertEqual(response.raw_response_sha256, cached_response.raw_response_sha256)


if __name__ == "__main__":
    unittest.main()
