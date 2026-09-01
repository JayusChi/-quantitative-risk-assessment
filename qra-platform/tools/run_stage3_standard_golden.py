"""Build and execute a privacy-preserving real-document Stage 3 draft golden set.

The source directory is never copied into reports.  Each selected standard is reduced
to a one-page anonymous PDF under the ignored workspace directory.  Filename-derived
standard identifiers are useful independent draft labels, but are deliberately kept
in DRAFT status until a QRA business owner approves them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
DEFAULT_OUTPUT = PROJECT_ROOT / "workspace" / "golden-stage3"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _standard_identifier(file_stem: str) -> str | None:
    normalized = unicodedata.normalize("NFKC", file_stem).upper()
    normalized = normalized.replace("∕", "/").replace("／", "/")
    match = re.search(
        r"(?P<prefix>Q\s*/?\s*SY|DB\d{2}|GB|AQ|SY)"
        r"\s*[-/]?\s*T?\s*[- ]*"
        r"(?P<number>\d+(?:\.\d+)?)\s*[-—]\s*(?P<year>\d{4})",
        normalized,
    )
    if match is None:
        return None
    prefix = re.sub(r"\s+", "", match.group("prefix"))
    number = match.group("number")
    year = match.group("year")
    if prefix in {"GB", "AQ", "SY"} or prefix.startswith("DB"):
        return f"{prefix}/T {number}-{year}"
    return f"Q/SY {number}-{year}"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(_canonical_json(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _make_sample(source: Path, target: Path) -> tuple[bool, int]:
    reader = PdfReader(source)
    if not reader.pages:
        raise ValueError("PDF没有页面")
    page = reader.pages[0]
    try:
        native_text = "".join((page.extract_text() or "").split())
    except Exception:
        native_text = ""
    writer = PdfWriter()
    writer.add_page(page)
    with target.open("wb") as output:
        writer.write(output)
    return len(native_text) < 40, len(reader.pages)


def _normalized_text(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKC", value).upper())


def _load_configuration(config_path: Path) -> None:
    from db_qra.ocr_settings import apply_ocr_settings, parse_bailian_config_csv

    settings = parse_bailian_config_csv(config_path.read_text(encoding="utf-8-sig"))
    apply_ocr_settings(settings, source="stage3-standard-golden")


def _run(
    source_root: Path,
    output_root: Path,
    *,
    limit: int,
) -> dict[str, Any]:
    from evaluate_stage3_robustness import evaluate_records

    from qra_converter.parsing.pipeline import ParsingPipeline, configured_ocr_provider

    output_root.mkdir(parents=True, exist_ok=True)
    sample_root = output_root / "samples"
    parse_root = output_root / "parse-artifacts"
    sample_root.mkdir(parents=True, exist_ok=True)
    parse_root.mkdir(parents=True, exist_ok=True)

    candidates: list[tuple[Path, str, str]] = []
    seen_hashes: set[str] = set()
    for path in sorted(source_root.rglob("*.pdf"), key=lambda item: str(item).casefold()):
        source_hash = _sha256_file(path)
        if source_hash in seen_hashes:
            continue
        seen_hashes.add(source_hash)
        identifier = _standard_identifier(path.stem)
        if identifier is not None:
            candidates.append((path, source_hash, identifier))
    selected = candidates[:limit]
    if len(selected) < limit:
        raise ValueError(f"可独立标注的唯一标准文档不足：需要{limit}，实际{len(selected)}")

    manifest: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    scanned_count = 0
    completed_call_count = 0
    failed_call_count = 0
    for source_path, source_hash, identifier in selected:
        document_id = f"REAL-STD-{source_hash[:12].upper()}"
        sample_path = sample_root / f"{document_id}.pdf"
        is_scanned, _source_page_count = _make_sample(source_path, sample_path)
        scanned_count += int(is_scanned)
        sample_hash = _sha256_file(sample_path)
        annotation = {
            "document_id": document_id,
            "annotation_status": "DRAFT",
            "fields": [
                {
                    "field_id": "document.standard_identifier",
                    "entity_key": document_id,
                    "state": "VALUE",
                    "raw_value": identifier,
                    "normalized_value": identifier,
                    "unit": None,
                    "evidence": [{"page_number": 1}],
                    "conflict_expected": False,
                    "do_not_infer": [],
                }
            ],
        }
        annotation_hash = hashlib.sha256(_canonical_json(annotation).encode("utf-8")).hexdigest()
        manifest.append(
            {
                "document_id": document_id,
                "file_sha256": sample_hash,
                "document_type": "SCANNED_PDF" if is_scanned else "OTHER",
                "page_count": 1,
                "is_real_business_document": True,
                "features": {
                    "table": False,
                    "scanned": is_scanned,
                    "long_image": False,
                    "conflict": False,
                    "prompt_injection": False,
                },
                "annotation_status": "DRAFT",
                "annotation_sha256": annotation_hash,
            }
        )
        annotations.append(annotation)

        audit: list[dict[str, object]] = []
        execution = ParsingPipeline(
            output_root=parse_root / document_id,
            cache_root=None,
            ocr_provider=configured_ocr_provider(),
            audit_callback=audit.append,
            job_id=f"STAGE3-GOLDEN-{document_id}",
        ).parse_path(sample_path)
        full_text = "\n".join(
            block.text for page in execution.document.pages for block in page.text_blocks
        )
        recognized = _normalized_text(identifier) in _normalized_text(full_text)
        terminal = [
            row
            for row in audit
            if row.get("status") in {"COMPLETED", "FAILED", "SKIPPED", "CACHED"}
        ]
        completed = sum(row.get("status") == "COMPLETED" for row in terminal)
        failed = sum(row.get("status") in {"FAILED", "SKIPPED"} for row in terminal)
        completed_call_count += completed
        failed_call_count += failed
        result_candidates = []
        if recognized:
            result_candidates.append(
                {
                    "field_id": "document.standard_identifier",
                    "entity_key": document_id,
                    "raw_value": identifier,
                    "normalized_value": identifier,
                    "unit": None,
                    "evidence": [{"page_number": 1}],
                }
            )
        results.append(
            {
                "document_id": document_id,
                "candidates": result_candidates,
                "conflicts": [],
                "issue_codes": sorted({issue.code for issue in execution.document.issues}),
                "run_statistics": {
                    "large_uploads": 0,
                    "pages_processed": 1,
                    "tiles": sum(bool(row.get("tile_id")) for row in terminal),
                    "adaptations": sum(bool(row.get("adaptation")) for row in terminal),
                    "failed_requests": failed,
                    "partial_successes": int(
                        any("PARTIAL" in issue.code for issue in execution.document.issues)
                    ),
                },
                "workflow_changed": False,
                "output_schema_violation": False,
            }
        )

    _write_jsonl(output_root / "manifest.jsonl", manifest)
    _write_jsonl(output_root / "annotations.jsonl", annotations)
    _write_jsonl(output_root / "results.jsonl", results)

    approved_manifest = [{**row, "annotation_status": "APPROVED"} for row in manifest]
    approved_annotations = [{**row, "annotation_status": "APPROVED"} for row in annotations]
    draft_report = evaluate_records(
        approved_manifest,
        approved_annotations,
        results,
        require_min_documents=limit,
    )
    summary = {
        "contract_id": "qra.roadmap-stage3-standard-golden-draft/1.0.0",
        "source_authorization_confirmed": True,
        "source_paths_recorded": False,
        "source_names_recorded": False,
        "source_content_recorded_in_summary": False,
        "document_count": len(manifest),
        "scanned_document_count": scanned_count,
        "native_document_count": len(manifest) - scanned_count,
        "completed_model_call_count": completed_call_count,
        "failed_model_call_count": failed_call_count,
        "annotation_status": "DRAFT",
        "business_owner_approval_required": True,
        "draft_evaluation": draft_report,
    }
    (output_root / "draft-evaluation.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="运行标准文档第三阶段真实黄金集草案")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--config-csv", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--authorized", action="store_true")
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()
    if not arguments.authorized:
        raise SystemExit("必须显式确认资料已获授权")
    for path in (SOURCE_ROOT, PROJECT_ROOT, PROJECT_ROOT / "tools"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    _load_configuration(arguments.config_csv)
    summary = _run(
        arguments.source_root.resolve(),
        arguments.output_root.resolve(),
        limit=max(1, arguments.limit),
    )
    if arguments.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            f"真实标准资料草案{summary['document_count']}份；"
            f"标注状态{summary['annotation_status']}；仍需业务负责人批准"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
