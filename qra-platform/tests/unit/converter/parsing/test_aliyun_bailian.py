from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from qra_converter.ocr.aliyun_bailian import AliyunBailianOcrProvider
from qra_converter.ocr.disabled import DisabledOcrProvider
from qra_converter.ocr.ports import OcrConnectionFailed, OcrRequest, OcrTimeout
from qra_converter.parsing.pipeline import configured_ocr_provider, real_ocr_configured


class FakeResponse:
    def __init__(self, payload: dict[str, object]):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class AliyunBailianOcrProviderTests(unittest.TestCase):
    @staticmethod
    def request() -> OcrRequest:
        return OcrRequest(b"png", 1200, 800, ("zh-Hans", "en"), True, "REQ-1", 9.0)

    def test_structured_high_precision_response_maps_text_and_location(self) -> None:
        captured = {}

        def opener(outbound, *, timeout):
            captured["url"] = outbound.full_url
            captured["authorization"] = outbound.get_header("Authorization")
            captured["body"] = json.loads(outbound.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse(
                {
                    "status_code": 200,
                    "request_id": "provider-request-1",
                    "output": {
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {
                                    "content": [
                                        {
                                            "ocr_result": {
                                                "words_info": [
                                                    {
                                                        "text": "管道完整性评估",
                                                        "location": [
                                                            10,
                                                            20,
                                                            210,
                                                            20,
                                                            210,
                                                            60,
                                                            10,
                                                            60,
                                                        ],
                                                        "confidence": 0.96,
                                                    }
                                                ]
                                            },
                                            "text": "管道完整性评估",
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                }
            )

        provider = AliyunBailianOcrProvider(
            dashscope_url="https://workspace.cn-beijing.maas.aliyuncs.com/api/v1",
            api_key="secret",
            opener=opener,
        )
        response = provider.recognize(self.request())

        self.assertEqual(
            captured["url"],
            "https://workspace.cn-beijing.maas.aliyuncs.com/"
            "api/v1/services/aigc/multimodal-generation/generation",
        )
        self.assertEqual(captured["authorization"], "Bearer secret")
        self.assertEqual(captured["timeout"], 9.0)
        self.assertEqual(
            captured["body"]["parameters"]["ocr_options"],
            {"task": "advanced_recognition"},
        )
        self.assertTrue(
            captured["body"]["input"]["messages"][0]["content"][0]["image"].startswith(
                "data:image/png;base64,"
            )
        )
        self.assertEqual(response.provider_request_id, "provider-request-1")
        self.assertEqual(response.text_blocks[0].text, "管道完整性评估")
        self.assertEqual(response.text_blocks[0].bbox.x, 10)
        self.assertEqual(response.text_blocks[0].bbox.width, 200)
        self.assertEqual(response.text_blocks[0].confidence, 0.96)
        self.assertEqual(response.warnings, ())

    def test_missing_provider_location_preserves_only_conservative_page_evidence(self) -> None:
        provider = AliyunBailianOcrProvider(
            dashscope_url="https://workspace.cn-beijing.maas.aliyuncs.com/api/v1",
            api_key="secret",
            opener=lambda *_args, **_kwargs: FakeResponse(
                {
                    "status_code": 200,
                    "request_id": "provider-request-2",
                    "output": {
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": [{"text": "only plain text"}]},
                            }
                        ]
                    },
                }
            ),
        )
        response = provider.recognize(self.request())
        self.assertEqual(len(response.text_blocks), 1)
        self.assertEqual(response.text_blocks[0].block_type, "UNLOCATED_PAGE_TEXT")
        self.assertEqual(response.text_blocks[0].confidence, 0.5)
        self.assertEqual(response.text_blocks[0].bbox.width, 1200)
        self.assertTrue(response.warnings)

    def test_max_model_uses_multimodal_transcription_prompt_without_ocr_options(self) -> None:
        captured = {}

        def opener(outbound, *, timeout):
            captured["body"] = json.loads(outbound.data.decode("utf-8"))
            return FakeResponse(
                {
                    "status_code": 200,
                    "request_id": "provider-request-max",
                    "output": {
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": "管道检测结果\n| 项目 | 数值 |"},
                            }
                        ]
                    },
                }
            )

        provider = AliyunBailianOcrProvider(
            dashscope_url="https://workspace.cn-beijing.maas.aliyuncs.com/api/v1",
            api_key="secret",
            model_version="qwen3.8-max",
            opener=opener,
        )
        response = provider.recognize(self.request())

        self.assertEqual(captured["body"]["model"], "qwen3.8-max")
        self.assertNotIn("ocr_options", captured["body"]["parameters"])
        message_content = captured["body"]["input"]["messages"][0]["content"]
        self.assertIn("image", message_content[0])
        self.assertIn("高准确度OCR", message_content[1]["text"])
        self.assertEqual(response.model_version, "qwen3.8-max")
        self.assertEqual(response.text_blocks[0].block_type, "UNLOCATED_PAGE_TEXT")
        self.assertIn("管道检测结果", response.text_blocks[0].text)

    def test_environment_selects_bailian_without_exposing_key_in_model_metadata(self) -> None:
        values = {
            "QRA_OCR_PROVIDER": "aliyun-bailian",
            "QRA_ALIYUN_DASHSCOPE_URL": (
                "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1"
            ),
            "QRA_ALIYUN_API_KEY": "secret",
            "QRA_OCR_MODEL_VERSION": "qwen3.5-ocr",
        }
        with patch.dict(os.environ, values, clear=True):
            provider = configured_ocr_provider()
            self.assertTrue(real_ocr_configured())
        self.assertIsInstance(provider, AliyunBailianOcrProvider)
        self.assertEqual(provider.model_version, "qwen3.5-ocr")
        self.assertNotIn("secret", repr(provider))

    def test_incomplete_bailian_environment_keeps_provider_disabled(self) -> None:
        with patch.dict(os.environ, {"QRA_OCR_PROVIDER": "aliyun-bailian"}, clear=True):
            self.assertIsInstance(configured_ocr_provider(), DisabledOcrProvider)
            self.assertFalse(real_ocr_configured())

    def test_network_denial_is_retryable_provider_error_not_timeout(self) -> None:
        provider = AliyunBailianOcrProvider(
            dashscope_url="https://workspace.cn-beijing.maas.aliyuncs.com/api/v1",
            api_key="secret",
            opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                URLError(PermissionError("socket access denied"))
            ),
        )
        with self.assertRaises(OcrConnectionFailed) as captured:
            provider.recognize(self.request())
        self.assertEqual(captured.exception.code, "PARSE.OCR_PROVIDER_ERROR")
        self.assertTrue(captured.exception.retryable)

    def test_url_timeout_remains_timeout(self) -> None:
        provider = AliyunBailianOcrProvider(
            dashscope_url="https://workspace.cn-beijing.maas.aliyuncs.com/api/v1",
            api_key="secret",
            opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                URLError(TimeoutError("timed out"))
            ),
        )
        with self.assertRaises(OcrTimeout):
            provider.recognize(self.request())

    def test_server_failure_is_retryable_provider_error(self) -> None:
        provider = AliyunBailianOcrProvider(
            dashscope_url="https://workspace.cn-beijing.maas.aliyuncs.com/api/v1",
            api_key="secret",
            opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                HTTPError("https://example.invalid", 503, "unavailable", {}, None)
            ),
        )
        with self.assertRaises(OcrConnectionFailed):
            provider.recognize(self.request())


if __name__ == "__main__":
    unittest.main()
