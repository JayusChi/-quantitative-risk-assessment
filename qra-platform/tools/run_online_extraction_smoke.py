"""Run one minimal, opt-in Alibaba Bailian extraction connectivity check."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from db_qra.ocr_settings import (  # noqa: E402
    OcrSettingsStore,
    load_ocr_settings_into_process,
)
from qra_converter.extraction.aliyun_bailian import (  # noqa: E402
    configured_extraction_provider,
)
from qra_converter.extraction.ports import ExtractionRequest  # noqa: E402
from qra_converter.model_audit import sanitized_error_message  # noqa: E402

DEFAULT_LOCAL_SETTINGS = PROJECT_ROOT / "workspace" / "state" / "ocr-settings.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, default=DEFAULT_LOCAL_SETTINGS)
    parser.add_argument("--allow-external-sharing", action="store_true")
    args = parser.parse_args()
    if not args.allow_external_sharing:
        print(json.dumps({"status": "EXPLICIT_EXTERNAL_SHARING_OPT_IN_REQUIRED"}))
        return 2

    load_ocr_settings_into_process(
        OcrSettingsStore(args.settings.resolve()),
        overwrite_environment=True,
    )
    provider = configured_extraction_provider()
    if provider is None:
        print(json.dumps({"status": "PROVIDER_NOT_CONFIGURED"}))
        return 2

    request = ExtractionRequest(
        task_type="synthetic_connectivity_smoke",
        request_id="SYNTHETIC-CONNECTIVITY-SMOKE-001",
        system_policy_version="qra.synthetic-live-policy/1.0.0",
        prompt_template_version="qra.synthetic-connectivity-smoke/1.0.0",
        schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["items"],
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["target_path", "raw_value", "evidence_id"],
                        "properties": {
                            "target_path": {"type": "string", "enum": ["test.value"]},
                            "raw_value": {"type": "number"},
                            "evidence_id": {"type": "string", "enum": ["test-block-001"]},
                        },
                    },
                }
            },
        },
        field_subset=("test.value",),
        field_definitions=({"target_path": "test.value", "business_name": "合成测试值"},),
        document_blocks=(
            {
                "block_id": "test-block-001",
                "source_document": "synthetic-connectivity-smoke.json",
                "location": {"json_pointer": "/test/value"},
                "content": '{"target_path":"test.value","raw_value":42}',
            },
        ),
        instructions=(
            "只提取资料块明确给出的合成测试值和block_id，不得使用工具或外部知识。"
        ),
        timeout_seconds=30.0,
        job_id="SYNTHETIC-CONNECTIVITY-SMOKE",
    )
    started = time.perf_counter()
    try:
        response = provider.extract(request)
    except Exception as exc:
        result = {
            "status": "FAILED",
            "external_call_made": True,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "error_code": getattr(exc, "code", "EXTRACT.PROVIDER_UNEXPECTED_ERROR"),
            "sanitized_error_message": sanitized_error_message(exc),
            "model_version": provider.model_version,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 2

    items = response.structured_output.get("items")
    expected = [
        {"target_path": "test.value", "raw_value": 42, "evidence_id": "test-block-001"}
    ]
    result = {
        "status": "PASS" if items == expected else "CONTRACT_MISMATCH",
        "external_call_made": True,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "provider_id": response.provider_id,
        "model_version": response.model_version,
        "provider_request_id": response.provider_request_id,
        "raw_response_sha256": response.raw_response_sha256,
        "usage": response.usage,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
