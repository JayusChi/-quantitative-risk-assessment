"""Run a sanitized Bailian OCR connectivity and parsing smoke test."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from db_qra.conversion_adapter import run_conversion_job, submit_conversion  # noqa: E402
from db_qra.database import QraDatabase  # noqa: E402
from qra_converter.parsing.pipeline import real_ocr_configured  # noqa: E402


def _font(size: int):
    candidates = (
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibri.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def _synthetic_image() -> bytes:
    image = Image.new("RGB", (1400, 900), "white")
    draw = ImageDraw.Draw(image)
    title_font = _font(54)
    body_font = _font(38)
    draw.text((80, 70), "QRA OCR CONNECTION TEST", fill="black", font=title_font)
    draw.text((80, 170), "Pipeline: GZ-001", fill="black", font=body_font)
    x_positions = (80, 600, 980, 1320)
    y_positions = (280, 390, 500, 610)
    for x in x_positions:
        draw.line((x, y_positions[0], x, y_positions[-1]), fill="black", width=3)
    for y in y_positions:
        draw.line((x_positions[0], y, x_positions[-1], y), fill="black", width=3)
    rows = (
        ("Segment", "Length km", "Pressure MPa"),
        ("SEG-001", "12.5", "6.3"),
        ("SEG-002", "8.8", "5.9"),
    )
    for row_index, row in enumerate(rows):
        y = y_positions[row_index] + 28
        for column_index, text in enumerate(row):
            draw.text((x_positions[column_index] + 24, y), text, fill="black", font=body_font)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description="测试阿里云百炼OCR连接和平台解析链路")
    parser.add_argument("--image", type=Path, help="可选；改用指定的PNG/JPG图片")
    parser.add_argument(
        "--show-text",
        action="store_true",
        help="显式输出最多500字识别文本；默认只输出哈希和计数",
    )
    arguments = parser.parse_args()
    if not real_ocr_configured():
        print(
            json.dumps(
                {
                    "status": "NOT_CONFIGURED",
                    "message": "当前进程未配置阿里云百炼OCR环境变量",
                },
                ensure_ascii=False,
            )
        )
        return 2

    with tempfile.TemporaryDirectory(prefix="qra-bailian-smoke-") as temporary:
        root = Path(temporary)
        file_name = arguments.image.name if arguments.image else "synthetic-ocr.png"
        content = arguments.image.resolve().read_bytes() if arguments.image else _synthetic_image()
        database = QraDatabase(root / "qra.sqlite3")
        job_id, _ = submit_conversion(
            database,
            profile="generic.structured-mvp.v1",
            files=[{"file_name": file_name, "content": content}],
            project_name="百炼OCR连通性测试",
        )
        completed = run_conversion_job(database, job_id, runtime_root=root / "runtime")
        source = database.list_conversion_sources(job_id)[0]
        stored = database.get_conversion_parse_artifact(
            job_id, str(source["id"]), "parsed_document.json"
        )
        if stored is None:
            print(json.dumps({"status": "FAIL", "message": "未生成解析产物"}, ensure_ascii=False))
            return 1
        document = json.loads(stored[1].decode("utf-8"))

    page_blocks = [block for page in document.get("pages", []) for block in page["text_blocks"]]
    text = "\n".join(str(block["text"]) for block in page_blocks)
    ocr = document.get("metadata", {}).get("ocr") or {}
    record = {
        "status": "PASS" if source["security_status"] == "PARSED" and page_blocks else "FAIL",
        "platform_job_status": completed["status"],
        "source_parse_status": source["security_status"],
        "provider_id": ocr.get("provider_id"),
        "model_version": ocr.get("model_version"),
        "provider_request_id": ocr.get("provider_request_id"),
        "raw_response_sha256": ocr.get("raw_response_sha256"),
        "text_block_count": len(page_blocks),
        "table_count": len(document.get("tables", [])),
        "recognized_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "recognized_expected_marker": (
            True if arguments.image else "QRA" in text.upper() and "GZ-001" in text.upper()
        ),
        "warnings": ocr.get("warnings") or [],
        "issue_codes": sorted(
            {str(issue["code"]) for issue in document.get("issues", [])}
        ),
    }
    if arguments.show_text:
        record["sample_text"] = text[:500]
    if not arguments.image and not record["recognized_expected_marker"]:
        record["status"] = "FAIL"
    print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
