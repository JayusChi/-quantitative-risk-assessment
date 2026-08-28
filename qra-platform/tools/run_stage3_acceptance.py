"""Run deterministic and opt-in real stage-three OCR acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
DEFAULT_OCR_SETTINGS = PROJECT_ROOT / "workspace" / "state" / "ocr-settings.json"
DEFAULT_REAL_IMAGE = PROJECT_ROOT.parent / ".tmp_jiujiang_review" / "images" / "001.jpg"
DEFAULT_REAL_SCAN_PDF = PROJECT_ROOT.parent / "tmp" / "pdfs" / "jiujiang-real-scan-acceptance.pdf"
OCR_ENVIRONMENT_KEYS = (
    "QRA_OCR_PROVIDER",
    "QRA_ALIYUN_API_KEY",
    "QRA_ALIYUN_DASHSCOPE_URL",
    "QRA_ALIYUN_OPENAI_BASE_URL",
    "QRA_OCR_API_KEY",
    "QRA_OCR_ENDPOINT",
    "QRA_OCR_MODEL_VERSION",
    "QRA_VISION_MODEL_VERSION",
    "QRA_OCR_TIMEOUT_SECONDS",
    "QRA_OCR_SETTINGS_SOURCE",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _deterministic_test_environment() -> Iterator[None]:
    """Keep the opt-in network smoke test skipped during deterministic discovery."""

    saved = {key: os.environ.pop(key) for key in OCR_ENVIRONMENT_KEYS if key in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def _deterministic_suite() -> unittest.TestResult:
    loader = unittest.TestLoader()
    with _deterministic_test_environment():
        suite = unittest.TestSuite(
            (
                loader.discover(
                    str(PROJECT_ROOT / "tests" / "unit" / "converter" / "parsing"),
                    pattern="test_*.py",
                    top_level_dir=str(PROJECT_ROOT),
                ),
                loader.loadTestsFromName("tests.integration.test_document_parsing_pipeline"),
            )
        )
        return unittest.TextTestRunner(verbosity=2, stream=sys.stderr).run(suite)


def _load_local_ocr_settings(settings_path: Path) -> dict[str, object]:
    from db_qra.ocr_settings import (
        OcrSettingsStore,
        environment_ocr_configured,
        load_ocr_settings_into_process,
    )

    if environment_ocr_configured():
        return {
            "configured": True,
            "source": os.environ.get("QRA_OCR_SETTINGS_SOURCE", "environment"),
            "protection": (
                "WINDOWS_DPAPI_CURRENT_USER"
                if os.environ.get("QRA_OCR_SETTINGS_SOURCE") == "encrypted-store"
                else "PROCESS_ENVIRONMENT_ONLY"
            ),
        }
    settings = load_ocr_settings_into_process(OcrSettingsStore(settings_path))
    return {
        "configured": settings is not None and environment_ocr_configured(),
        "source": "encrypted-store" if settings is not None else "none",
        "protection": "WINDOWS_DPAPI_CURRENT_USER" if settings is not None else None,
    }


def _ocr_calls(document: Any) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []
    image_call = document.metadata.get("ocr")
    if isinstance(image_call, dict) and image_call.get("raw_response_sha256"):
        calls.append(image_call)
    pdf_calls = document.metadata.get("ocr_calls")
    if isinstance(pdf_calls, list):
        calls.extend(item for item in pdf_calls if isinstance(item, dict))
    allowed = (
        "page_number",
        "image_id",
        "provider_id",
        "model_version",
        "raw_response_sha256",
        "provider_request_id",
    )
    return [{key: call.get(key) for key in allowed if key in call} for call in calls]


def _audit_execution(label: str, path: Path, execution: Any) -> dict[str, object]:
    document = execution.document
    page_blocks = [block for page in document.pages for block in page.text_blocks]
    ocr_blocks = [block for block in page_blocks if block.extraction_method.startswith("OCR")]
    located_blocks = [
        block
        for block in ocr_blocks
        if block.page_number is not None
        and block.bbox is not None
        and block.coordinate_space is not None
    ]
    ocr_cells = [
        cell
        for table in document.tables
        for cell in table.cells
        if cell.extraction_method.startswith("OCR")
    ]
    calls = _ocr_calls(document)
    complete_calls = [
        call
        for call in calls
        if call.get("provider_id") not in {None, "", "fixture", "disabled"}
        and bool(call.get("model_version"))
        and len(str(call.get("raw_response_sha256") or "")) == 64
        and bool(call.get("provider_request_id"))
    ]
    success = (
        execution.succeeded
        and bool(calls)
        and len(complete_calls) == len(calls)
        and bool(ocr_blocks or ocr_cells)
        and len(located_blocks) == len(ocr_blocks)
    )
    return {
        "label": label,
        "file_name": path.name,
        "input_sha256": _sha256(path),
        "media_type": document.media_type,
        "page_count": document.page_count,
        "page_classifications": [page.classification for page in document.pages],
        "ocr_block_count": len(ocr_blocks),
        "located_ocr_block_count": len(located_blocks),
        "ocr_cell_count": len(ocr_cells),
        "table_count": len(document.tables),
        "parse_sha256": document.parse_sha256,
        "parsing_provenance": document.metadata.get("parsing_provenance", {}),
        "ocr_calls": calls,
        "issue_codes": sorted({issue.code for issue in document.issues}),
        "succeeded": success,
    }


def _real_ocr_audit(image_path: Path, scan_pdf_path: Path) -> dict[str, object]:
    from qra_converter.parsing.pipeline import ParsingPipeline, configured_ocr_provider

    missing = [str(path) for path in (image_path, scan_pdf_path) if not path.is_file()]
    if missing:
        return {
            "status": "INPUT_MISSING",
            "missing_input_names": [Path(path).name for path in missing],
            "targets": [],
        }
    with tempfile.TemporaryDirectory(prefix="qra-stage3-real-ocr-") as temporary:
        root = Path(temporary)
        provider = configured_ocr_provider()
        targets = []
        for label, path in (("JIUJIANG_IMAGE", image_path), ("JIUJIANG_SCAN_PDF", scan_pdf_path)):
            execution = ParsingPipeline(
                output_root=root / label.casefold(),
                cache_root=root / "cache",
                ocr_provider=provider,
            ).parse_path(path)
            targets.append(_audit_execution(label, path, execution))
    return {
        "status": "PASS" if all(target["succeeded"] for target in targets) else "FAIL",
        "targets": targets,
    }


def _write_record(path: Path, record: dict[str, object]) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def main() -> int:
    parser = argparse.ArgumentParser(description="执行第三阶段文档解析与真实OCR验收")
    parser.add_argument(
        "--require-real-ocr",
        action="store_true",
        help="加载本机加密配置并对九江真实图片和扫描PDF执行正式OCR",
    )
    parser.add_argument(
        "--ocr-settings",
        type=Path,
        default=DEFAULT_OCR_SETTINGS,
        help="本机DPAPI加密OCR设置文件",
    )
    parser.add_argument("--real-image", type=Path, default=DEFAULT_REAL_IMAGE)
    parser.add_argument("--real-scan-pdf", type=Path, default=DEFAULT_REAL_SCAN_PDF)
    parser.add_argument("--record", type=Path, help="写入不含密钥和识别文本的验收JSON")
    parser.add_argument("--json", action="store_true", help="输出机器可读验收摘要")
    arguments = parser.parse_args()

    source_text = str(SOURCE_ROOT)
    if source_text not in sys.path:
        sys.path.insert(0, source_text)

    result = _deterministic_suite()
    configuration: dict[str, object] = {"configured": False, "source": "not-loaded"}
    real_audit: dict[str, object] = {"status": "NOT_RUN", "targets": []}
    configuration_issue: str | None = None
    if arguments.require_real_ocr:
        try:
            configuration = _load_local_ocr_settings(arguments.ocr_settings)
        except (OSError, RuntimeError, ValueError) as exc:
            configuration_issue = type(exc).__name__
        if configuration.get("configured"):
            real_audit = _real_ocr_audit(
                arguments.real_image.resolve(),
                arguments.real_scan_pdf.resolve(),
            )
        else:
            real_audit = {"status": "NOT_CONFIGURED", "targets": []}

    real_status = str(real_audit["status"])
    record: dict[str, object] = {
        "stage": 3,
        "acceptance_id": "qra.stage3-parsing/1.0.0",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "deterministic_suite": "PASS" if result.wasSuccessful() else "FAIL",
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "ocr_configuration": configuration,
        "real_ocr_status": real_status,
        "real_ocr_audit": real_audit,
        "formal_scan_acceptance": result.wasSuccessful() and real_status == "PASS",
    }
    if configuration_issue:
        record["configuration_issue"] = configuration_issue
    if arguments.record:
        _write_record(arguments.record, record)
    if arguments.json:
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            "第三阶段确定性验收："
            f"{record['deterministic_suite']}；真实OCR：{record['real_ocr_status']}"
        )
    if not result.wasSuccessful():
        return 1
    if arguments.require_real_ocr and not configuration.get("configured"):
        return 2
    if arguments.require_real_ocr and real_status != "PASS":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
