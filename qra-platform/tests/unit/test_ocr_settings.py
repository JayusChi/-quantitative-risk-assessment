from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from db_qra.ocr_settings import (
    DEFAULT_EXTRACTION_TIMEOUT_SECONDS,
    DEFAULT_OCR_TIMEOUT_SECONDS,
    SUPPORTED_OCR_MODELS,
    OcrSettingsStore,
    load_ocr_settings_into_process,
    ocr_settings_status,
    parse_bailian_config_csv,
    protect_secret,
    unprotect_secret,
)

CSV_TEXT = """workspaceName,测试业务空间
workspaceId,workspace-not-public
apiKey,sk-test-1234567890abcdef
apiHost,llm-test.cn-beijing.maas.aliyuncs.com
openAiCompatible,https://llm-test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
dashScope,https://llm-test.cn-beijing.maas.aliyuncs.com/api/v1
"""

OCR_ENVIRONMENT_KEYS = (
    "QRA_OCR_PROVIDER",
    "QRA_ALIYUN_API_KEY",
    "QRA_ALIYUN_DASHSCOPE_URL",
    "QRA_ALIYUN_OPENAI_BASE_URL",
    "QRA_OCR_MODEL_VERSION",
    "QRA_VISION_MODEL_VERSION",
    "QRA_EXTRACTION_PROVIDER",
    "QRA_EXTRACTION_MODEL_VERSION",
    "QRA_EXTRACTION_TIMEOUT_SECONDS",
    "QRA_EXTRACTION_MAX_RETRIES",
    "QRA_EXTRACTION_MAX_CONCURRENCY",
    "QRA_OCR_TIMEOUT_SECONDS",
    "QRA_OCR_SETTINGS_SOURCE",
)


def _protector(value: str) -> str:
    return "sealed:" + value[::-1]


def _unprotector(value: str) -> str:
    if not value.startswith("sealed:"):
        raise ValueError("invalid test ciphertext")
    return value.removeprefix("sealed:")[::-1]


class OcrSettingsTests(unittest.TestCase):
    def test_parse_store_restore_and_public_status_never_expose_key(self) -> None:
        settings = parse_bailian_config_csv(CSV_TEXT)
        self.assertEqual(settings.ocr_model_version, "qwen3.5-ocr")
        self.assertEqual(settings.ocr_timeout_seconds, 120)
        self.assertEqual(settings.extraction_model_version, "qwen3.8-max")
        self.assertEqual(settings.extraction_timeout_seconds, 120)
        self.assertEqual(DEFAULT_OCR_TIMEOUT_SECONDS, 120)
        self.assertNotIn(settings.api_key, repr(settings))

        with tempfile.TemporaryDirectory() as temporary:
            store = OcrSettingsStore(
                Path(temporary) / "ocr-settings.json",
                protector=_protector,
                unprotector=_unprotector,
            )
            store.save(settings)
            stored_text = store.path.read_text(encoding="utf-8")
            self.assertNotIn(settings.api_key, stored_text)
            self.assertEqual(store.load(), settings)

            with patch.dict(os.environ, {}, clear=False):
                for key in OCR_ENVIRONMENT_KEYS:
                    os.environ.pop(key, None)
                restored = load_ocr_settings_into_process(store)
                self.assertEqual(restored, settings)
                status = ocr_settings_status(store)
                response = json.dumps(status, ensure_ascii=False)
                self.assertTrue(status["configured"])
                self.assertTrue(status["persisted"])
                self.assertEqual(status["source"], "encrypted-store")
                self.assertEqual(status["ocr_timeout_seconds"], 120)
                self.assertTrue(status["extraction_configured"])
                self.assertEqual(status["extraction_model_version"], "qwen3.8-max")
                self.assertEqual(os.environ["QRA_OCR_TIMEOUT_SECONDS"], "120")
                self.assertEqual(
                    os.environ["QRA_EXTRACTION_MODEL_VERSION"], "qwen3.8-max"
                )
                self.assertEqual(
                    tuple(option["id"] for option in status["available_models"]),
                    SUPPORTED_OCR_MODELS,
                )
                self.assertNotIn(settings.api_key, response)
                self.assertNotIn("workspace-not-public", response)

    def test_old_persisted_config_defaults_to_120_seconds(self) -> None:
        settings = parse_bailian_config_csv(CSV_TEXT)
        with tempfile.TemporaryDirectory() as temporary:
            store = OcrSettingsStore(
                Path(temporary) / "ocr-settings.json",
                protector=_protector,
                unprotector=_unprotector,
            )
            store.save(settings)
            payload = json.loads(store.path.read_text(encoding="utf-8"))
            payload.pop("ocr_timeout_seconds")
            store.path.write_text(json.dumps(payload), encoding="utf-8")
            restored = store.load()
            self.assertIsNotNone(restored)
            self.assertEqual(restored.ocr_timeout_seconds, 120)

    def test_unusable_persisted_config_requires_reimport_without_exposing_error(self) -> None:
        settings = parse_bailian_config_csv(CSV_TEXT)
        with tempfile.TemporaryDirectory() as temporary:
            store = OcrSettingsStore(
                Path(temporary) / "ocr-settings.json",
                protector=_protector,
                unprotector=lambda _value: (_ for _ in ()).throw(
                    OSError("sensitive DPAPI diagnostic")
                ),
            )
            store.save(settings)
            with patch.dict(os.environ, {}, clear=False):
                for key in OCR_ENVIRONMENT_KEYS:
                    os.environ.pop(key, None)
                status = ocr_settings_status(store)

        response = json.dumps(status, ensure_ascii=False)
        self.assertFalse(status["configured"])
        self.assertTrue(status["persisted"])
        self.assertFalse(status["persisted_usable"])
        self.assertTrue(status["reimport_required"])
        self.assertEqual(status["status_issue"], "OCR_SETTINGS_REIMPORT_REQUIRED")
        self.assertNotIn("sensitive DPAPI diagnostic", response)
        self.assertNotIn(settings.api_key, response)

    def test_invalid_environment_extraction_timeout_uses_safe_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = OcrSettingsStore(
                Path(temporary) / "missing-settings.json",
                protector=_protector,
                unprotector=_unprotector,
            )
            environment = {
                "QRA_OCR_PROVIDER": "aliyun-bailian",
                "QRA_ALIYUN_API_KEY": "sk-test-1234567890abcdef",
                "QRA_ALIYUN_DASHSCOPE_URL": (
                    "https://llm-test.cn-beijing.maas.aliyuncs.com/api/v1"
                ),
                "QRA_ALIYUN_OPENAI_BASE_URL": (
                    "https://llm-test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
                ),
                "QRA_EXTRACTION_PROVIDER": "aliyun-bailian",
                "QRA_EXTRACTION_MODEL_VERSION": "qwen3.8-max",
                "QRA_EXTRACTION_TIMEOUT_SECONDS": "not-a-number",
            }
            with patch.dict(os.environ, environment, clear=True):
                status = ocr_settings_status(store)

        self.assertTrue(status["extraction_configured"])
        self.assertEqual(
            status["extraction_timeout_seconds"],
            DEFAULT_EXTRACTION_TIMEOUT_SECONDS,
        )

    def test_ocr_model_is_case_insensitive_but_limited_to_workspace_options(self) -> None:
        settings = parse_bailian_config_csv(
            CSV_TEXT,
            ocr_model_version="Qwen3.5-OCR",
        )
        self.assertEqual(settings.ocr_model_version, "qwen3.5-ocr")
        with self.assertRaisesRegex(ValueError, "四个模型"):
            parse_bailian_config_csv(CSV_TEXT, ocr_model_version="unknown-model")

    def test_rejects_non_https_or_unrelated_api_endpoints(self) -> None:
        bad = CSV_TEXT.replace(
            "https://llm-test.cn-beijing.maas.aliyuncs.com/api/v1",
            "https://example.com/api/v1",
        )
        with self.assertRaisesRegex(ValueError, "DashScope"):
            parse_bailian_config_csv(bad)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI is only available on Windows")
    def test_windows_dpapi_current_user_round_trip(self) -> None:
        secret = "sk-dpapi-round-trip-123456789"
        protected = protect_secret(secret)
        self.assertNotEqual(protected, secret)
        self.assertEqual(unprotect_secret(protected), secret)


if __name__ == "__main__":
    unittest.main()
