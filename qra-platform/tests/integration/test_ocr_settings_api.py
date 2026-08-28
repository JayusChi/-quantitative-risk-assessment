from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from db_qra.admin_ui import admin_html
from db_qra.database import QraDatabase
from db_qra.ocr_settings import OcrSettingsStore, load_ocr_settings_into_process
from db_qra.server import QraRequestHandler

API_KEY = "sk-integration-1234567890abcdef"
CSV_TEXT = f"""workspaceName,集成测试空间
workspaceId,hidden-workspace-id
apiKey,{API_KEY}
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
    "QRA_OCR_TIMEOUT_SECONDS",
    "QRA_OCR_SETTINGS_SOURCE",
)


class OcrSettingsApiTests(unittest.TestCase):
    def test_browser_api_imports_encrypted_config_and_restores_on_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {}, clear=False
        ):
            for key in OCR_ENVIRONMENT_KEYS:
                os.environ.pop(key, None)
            root = Path(temporary)
            database = QraDatabase(root / "test.sqlite3")
            database.initialize()
            store = OcrSettingsStore(
                root / "ocr-settings.json",
                protector=lambda value: "sealed:" + value[::-1],
                unprotector=lambda value: value.removeprefix("sealed:")[::-1],
            )
            handler = type(
                "OcrSettingsHandler",
                (QraRequestHandler,),
                {"database": database, "ocr_settings_store": store},
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address
                base = f"http://{host}:{port}"
                payload = json.dumps(
                    {
                        "csv_text": CSV_TEXT,
                        "ocr_model_version": "qwen3.5-ocr",
                        "vision_model_version": "qwen3.7-max",
                        "ocr_timeout_seconds": 120,
                    }
                ).encode("utf-8")
                request = Request(
                    base + "/admin/api/ocr-settings",
                    data=payload,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urlopen(request, timeout=5) as response:
                    status = json.loads(response.read().decode("utf-8"))
                self.assertTrue(status["configured"])
                self.assertTrue(status["persisted"])
                self.assertEqual(status["ocr_model_version"], "qwen3.5-ocr")
                self.assertEqual(status["ocr_timeout_seconds"], 120)
                self.assertEqual(
                    [model["id"] for model in status["available_models"]],
                    [
                        "qwen3.5-ocr",
                        "qwen3.8-max",
                        "qwen3.7-max",
                        "qwen3.7-max-2026-06-08",
                    ],
                )
                self.assertNotIn(API_KEY, json.dumps(status))
                self.assertNotIn("hidden-workspace-id", json.dumps(status))

                switch_payload = json.dumps(
                    {
                        "ocr_model_version": "qwen3.8-max",
                        "ocr_timeout_seconds": 120,
                    }
                ).encode("utf-8")
                switch_request = Request(
                    base + "/admin/api/ocr-settings",
                    data=switch_payload,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urlopen(switch_request, timeout=5) as response:
                    switched = json.loads(response.read().decode("utf-8"))
                self.assertEqual(switched["ocr_model_version"], "qwen3.8-max")
                self.assertEqual(store.load().ocr_model_version, "qwen3.8-max")
                self.assertEqual(os.environ["QRA_OCR_MODEL_VERSION"], "qwen3.8-max")
                self.assertNotIn(API_KEY, json.dumps(switched))

                with urlopen(base + "/admin/api/ocr-settings", timeout=5) as response:
                    fetched = json.loads(response.read().decode("utf-8"))
                self.assertEqual(fetched["source"], "encrypted-store")
                self.assertNotIn(API_KEY, json.dumps(fetched))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

            for key in OCR_ENVIRONMENT_KEYS:
                os.environ.pop(key, None)
            restored = load_ocr_settings_into_process(store)
            self.assertIsNotNone(restored)
            self.assertEqual(os.environ["QRA_ALIYUN_API_KEY"], API_KEY)
            self.assertEqual(os.environ["QRA_OCR_MODEL_VERSION"], "qwen3.8-max")
            self.assertEqual(os.environ["QRA_OCR_TIMEOUT_SECONDS"], "120")

    def test_admin_page_contains_one_time_ocr_configuration_controls(self) -> None:
        html = admin_html().decode("utf-8")
        self.assertIn('data-action="ocr-settings"', html)
        self.assertIn('id="ocrConfigInput"', html)
        self.assertIn('id="ocrModelSelect"', html)
        self.assertIn("qwen3.8-max", html)
        self.assertIn("qwen3.7-max-2026-06-08", html)
        self.assertIn('value="120 秒"', html)
        self.assertIn("Windows 当前用户 DPAPI", html)


if __name__ == "__main__":
    unittest.main()
