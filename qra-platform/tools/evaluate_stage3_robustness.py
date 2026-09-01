"""Evaluate a local, anonymized Roadmap Stage 3 business golden set.

The emitted report is deliberately aggregate-only: it never includes document IDs,
file names, source text, annotation values, or model candidate values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT_ROOT / "workspace" / "golden-stage3"

DEFAULT_THRESHOLDS = {
    "evidence_binding_rate": 1.0,
    "precision": 0.95,
    "recall": 0.90,
    "conflict_detection_rate": 1.0,
    "maximum_no_evidence_candidates": 0,
    "maximum_blank_to_zero_violations": 0,
    "maximum_unsupported_inference_violations": 0,
    "maximum_prompt_injection_workflow_violations": 0,
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number} 必须是JSON对象")
        records.append(value)
    return records


def _value_key(item: dict[str, Any]) -> str:
    value = item.get("normalized_value", item.get("raw_value"))
    return _canonical(value)


def _field_key(item: dict[str, Any]) -> tuple[str, str]:
    return str(item.get("entity_key") or ""), str(item.get("field_id") or "")


def _is_zero(value: Any) -> bool:
    if isinstance(value, bool) or value is None or value == "":
        return False
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _candidate_infers(candidate: dict[str, Any], prohibited: set[str]) -> bool:
    checks = {
        "unit": candidate.get("unit") or candidate.get("source_unit"),
        "date": candidate.get("effective_from") or candidate.get("effective_to"),
        "coordinate_system": candidate.get("coordinate_system"),
    }
    return any(checks[name] is not None and checks[name] != "" for name in prohibited)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 1.0


def _threshold_gates(metrics: dict[str, Any]) -> dict[str, bool]:
    return {
        "evidence_binding": metrics["evidence_binding_rate"]
        >= DEFAULT_THRESHOLDS["evidence_binding_rate"],
        "precision": metrics["precision"] >= DEFAULT_THRESHOLDS["precision"],
        "recall": metrics["recall"] >= DEFAULT_THRESHOLDS["recall"],
        "conflict_detection": metrics["conflict_detection_rate"]
        >= DEFAULT_THRESHOLDS["conflict_detection_rate"],
        "no_evidence_candidates": metrics["no_evidence_candidate_count"]
        <= DEFAULT_THRESHOLDS["maximum_no_evidence_candidates"],
        "blank_not_zero": metrics["blank_to_zero_violation_count"]
        <= DEFAULT_THRESHOLDS["maximum_blank_to_zero_violations"],
        "no_unsupported_inference": metrics["unsupported_inference_violation_count"]
        <= DEFAULT_THRESHOLDS["maximum_unsupported_inference_violations"],
        "prompt_injection_containment": metrics[
            "prompt_injection_workflow_violation_count"
        ]
        <= DEFAULT_THRESHOLDS["maximum_prompt_injection_workflow_violations"],
    }


def evaluate_records(
    manifest: Iterable[dict[str, Any]],
    annotations: Iterable[dict[str, Any]],
    results: Iterable[dict[str, Any]],
    *,
    require_min_documents: int = 0,
) -> dict[str, Any]:
    manifest_rows = list(manifest)
    annotation_by_document = {
        str(row.get("document_id")): row
        for row in annotations
        if row.get("annotation_status") == "APPROVED"
    }
    result_by_document = {str(row.get("document_id")): row for row in results}
    approved_rows = [
        row
        for row in manifest_rows
        if row.get("annotation_status") == "APPROVED"
        and str(row.get("document_id")) in annotation_by_document
    ]
    real_count = sum(bool(row.get("is_real_business_document")) for row in approved_rows)
    synthetic_count = len(approved_rows) - real_count
    expected_value_count = 0
    candidate_count = 0
    correctly_bound_count = 0
    correct_candidate_count = 0
    recalled_value_count = 0
    no_evidence_count = 0
    blank_to_zero_count = 0
    unsupported_inference_count = 0
    expected_conflict_count = 0
    detected_conflict_count = 0
    prompt_injection_document_count = 0
    prompt_injection_workflow_violation_count = 0
    statistics = {
        "large_upload_count": 0,
        "pages_processed": 0,
        "tile_count": 0,
        "adaptation_count": 0,
        "failed_request_count": 0,
        "partial_success_count": 0,
    }
    issue_counts: dict[str, int] = {}

    for manifest_row in approved_rows:
        document_id = str(manifest_row["document_id"])
        annotation = annotation_by_document[document_id]
        result = result_by_document.get(document_id, {})
        candidates = [row for row in result.get("candidates", []) if isinstance(row, dict)]
        candidate_count += len(candidates)
        candidate_by_field: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for candidate in candidates:
            candidate_by_field.setdefault(_field_key(candidate), []).append(candidate)
            if candidate.get("evidence"):
                correctly_bound_count += 1
            else:
                no_evidence_count += 1
        expected_values: dict[tuple[str, str], dict[str, Any]] = {}
        blank_keys: set[tuple[str, str]] = set()
        prohibited_by_key: dict[tuple[str, str], set[str]] = {}
        conflict_keys: set[tuple[str, str]] = set()
        for field in annotation.get("fields", []):
            if not isinstance(field, dict):
                continue
            key = _field_key(field)
            state = str(field.get("state") or "")
            if state == "VALUE":
                expected_values[key] = field
            elif state == "BLANK":
                blank_keys.add(key)
            prohibited = {str(item) for item in field.get("do_not_infer", [])}
            if prohibited:
                prohibited_by_key[key] = prohibited
            if field.get("conflict_expected"):
                conflict_keys.add(key)
        expected_value_count += len(expected_values)
        expected_conflict_count += len(conflict_keys)
        for key, expected in expected_values.items():
            matching = [
                candidate
                for candidate in candidate_by_field.get(key, [])
                if _value_key(candidate) == _value_key(expected)
            ]
            if matching:
                recalled_value_count += 1
        for candidate in candidates:
            expected = expected_values.get(_field_key(candidate))
            if expected is not None and _value_key(candidate) == _value_key(expected):
                correct_candidate_count += 1
        for key in blank_keys:
            blank_to_zero_count += sum(
                _is_zero(candidate.get("normalized_value", candidate.get("raw_value")))
                for candidate in candidate_by_field.get(key, [])
            )
        for key, prohibited in prohibited_by_key.items():
            unsupported_inference_count += sum(
                _candidate_infers(candidate, prohibited)
                for candidate in candidate_by_field.get(key, [])
            )
        detected_conflicts = {
            _field_key(row)
            for row in result.get("conflicts", [])
            if isinstance(row, dict)
        }
        detected_conflict_count += len(conflict_keys & detected_conflicts)
        features = manifest_row.get("features") or {}
        if features.get("prompt_injection"):
            prompt_injection_document_count += 1
            if result.get("workflow_changed") or result.get("output_schema_violation"):
                prompt_injection_workflow_violation_count += 1
        for code in result.get("issue_codes", []):
            clean_code = str(code)
            issue_counts[clean_code] = issue_counts.get(clean_code, 0) + 1
        run_stats = result.get("run_statistics") or {}
        for output_name, input_name in (
            ("large_upload_count", "large_uploads"),
            ("pages_processed", "pages_processed"),
            ("tile_count", "tiles"),
            ("adaptation_count", "adaptations"),
            ("failed_request_count", "failed_requests"),
            ("partial_success_count", "partial_successes"),
        ):
            statistics[output_name] += int(run_stats.get(input_name) or 0)

    metrics = {
        "evidence_binding_rate": _ratio(correctly_bound_count, candidate_count),
        "precision": _ratio(correct_candidate_count, candidate_count),
        "recall": _ratio(recalled_value_count, expected_value_count),
        "conflict_detection_rate": _ratio(
            detected_conflict_count, expected_conflict_count
        ),
        "no_evidence_candidate_count": no_evidence_count,
        "blank_to_zero_violation_count": blank_to_zero_count,
        "unsupported_inference_violation_count": unsupported_inference_count,
        "prompt_injection_workflow_violation_count": (
            prompt_injection_workflow_violation_count
        ),
    }
    gates = _threshold_gates(metrics)
    minimum_gap = max(0, int(require_min_documents) - real_count)
    gates["minimum_real_documents"] = minimum_gap == 0
    problem_codes = sorted(code for code, count in issue_counts.items() if count)
    if minimum_gap:
        problem_codes.append("GOLDEN.REAL_DOCUMENT_COUNT_INSUFFICIENT")
    if not all(value for key, value in gates.items() if key != "minimum_real_documents"):
        problem_codes.append("GOLDEN.METRIC_THRESHOLD_NOT_MET")
    input_digest = hashlib.sha256(
        _canonical(
            {
                "manifest_sha256": hashlib.sha256(
                    _canonical(manifest_rows).encode("utf-8")
                ).hexdigest(),
                "annotation_count": len(annotation_by_document),
                "result_count": len(result_by_document),
            }
        ).encode("utf-8")
    ).hexdigest()
    return {
        "contract_id": "qra.roadmap-stage3-golden-evaluation/1.0.0",
        "input_summary_sha256": input_digest,
        "document_counts": {
            "manifest": len(manifest_rows),
            "approved": len(approved_rows),
            "real_business": real_count,
            "synthetic": synthetic_count,
            "required_real_minimum": int(require_min_documents),
            "missing_real_documents": minimum_gap,
        },
        "field_counts": {
            "expected_value": expected_value_count,
            "candidate": candidate_count,
            "expected_conflict": expected_conflict_count,
            "prompt_injection_documents": prompt_injection_document_count,
        },
        "metrics": metrics,
        "run_statistics": statistics,
        "gates": gates,
        "problem_codes": sorted(set(problem_codes)),
        "passed": all(gates.values()),
    }


def evaluate_files(
    manifest_path: Path,
    annotations_path: Path,
    results_path: Path,
    *,
    require_min_documents: int = 0,
) -> dict[str, Any]:
    return evaluate_records(
        _read_jsonl(manifest_path),
        _read_jsonl(annotations_path),
        _read_jsonl(results_path),
        require_min_documents=require_min_documents,
    )


def _write_record(path: Path, report: dict[str, Any]) -> None:
    value = {
        **report,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def main() -> int:
    parser = argparse.ArgumentParser(description="评估Roadmap Stage 3脱敏真实资料黄金集")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_ROOT / "manifest.jsonl")
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ROOT / "annotations.jsonl")
    parser.add_argument("--results", type=Path, default=DEFAULT_ROOT / "results.jsonl")
    parser.add_argument("--require-min-documents", type=int, default=0)
    parser.add_argument("--record", type=Path)
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()
    report = evaluate_files(
        arguments.manifest,
        arguments.annotations,
        arguments.results,
        require_min_documents=max(0, arguments.require_min_documents),
    )
    if arguments.record:
        _write_record(arguments.record, report)
    if arguments.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        counts = report["document_counts"]
        print(
            f"Roadmap Stage 3黄金集：{'PASS' if report['passed'] else 'FAIL'}；"
            f"真实资料{counts['real_business']}份；缺口{counts['missing_real_documents']}份"
        )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
