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

RUN_STATISTIC_FIELDS = {
    "large_upload_count": "large_uploads",
    "pages_processed": "pages_processed",
    "tile_count": "tiles",
    "adaptation_count": "adaptations",
    "failed_request_count": "failed_requests",
    "partial_success_count": "partial_successes",
    "elapsed_ms": "elapsed_ms",
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "total_tokens": "total_tokens",
    "manual_review_ms": "manual_review_ms",
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


def _optional_ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _field_group(item: dict[str, Any]) -> str:
    field_id = str(item.get("field_id") or "")
    return field_id.split(".", 1)[0] if field_id else "UNSPECIFIED"


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _empty_score() -> dict[str, Any]:
    return {
        "expected_value_count": 0,
        "candidate_count": 0,
        "correctly_bound_count": 0,
        "correct_candidate_count": 0,
        "recalled_value_count": 0,
        "no_evidence_count": 0,
        "blank_to_zero_count": 0,
        "unsupported_inference_count": 0,
        "expected_conflict_count": 0,
        "detected_conflict_count": 0,
        "prompt_injection_document_count": 0,
        "prompt_injection_workflow_violation_count": 0,
        "run_statistics": {name: 0 for name in RUN_STATISTIC_FIELDS},
        "telemetry_completeness": {
            "document_count": 0,
            "elapsed_recorded_count": 0,
            "token_usage_recorded_count": 0,
            "manual_review_time_recorded_count": 0,
        },
    }


def _add_score(target: dict[str, Any], source: dict[str, Any]) -> None:
    for name in (
        "expected_value_count",
        "candidate_count",
        "correctly_bound_count",
        "correct_candidate_count",
        "recalled_value_count",
        "no_evidence_count",
        "blank_to_zero_count",
        "unsupported_inference_count",
        "expected_conflict_count",
        "detected_conflict_count",
        "prompt_injection_document_count",
        "prompt_injection_workflow_violation_count",
    ):
        target[name] += source[name]
    for name in RUN_STATISTIC_FIELDS:
        target["run_statistics"][name] += source["run_statistics"][name]
    for name in target["telemetry_completeness"]:
        target["telemetry_completeness"][name] += source[
            "telemetry_completeness"
        ][name]


def _score_fields(
    fields: Iterable[dict[str, Any]],
    candidates: Iterable[dict[str, Any]],
    conflicts: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    score = _empty_score()
    candidate_rows = list(candidates)
    score["candidate_count"] = len(candidate_rows)
    candidate_by_field: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for candidate in candidate_rows:
        candidate_by_field.setdefault(_field_key(candidate), []).append(candidate)
        if candidate.get("evidence"):
            score["correctly_bound_count"] += 1
        else:
            score["no_evidence_count"] += 1

    expected_values: dict[tuple[str, str], dict[str, Any]] = {}
    blank_keys: set[tuple[str, str]] = set()
    prohibited_by_key: dict[tuple[str, str], set[str]] = {}
    conflict_keys: set[tuple[str, str]] = set()
    for field in fields:
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

    score["expected_value_count"] = len(expected_values)
    score["expected_conflict_count"] = len(conflict_keys)
    for key, expected in expected_values.items():
        matching = [
            candidate
            for candidate in candidate_by_field.get(key, [])
            if _value_key(candidate) == _value_key(expected)
        ]
        if matching:
            score["recalled_value_count"] += 1
    for candidate in candidate_rows:
        expected = expected_values.get(_field_key(candidate))
        if expected is not None and _value_key(candidate) == _value_key(expected):
            score["correct_candidate_count"] += 1
    for key in blank_keys:
        score["blank_to_zero_count"] += sum(
            _is_zero(candidate.get("normalized_value", candidate.get("raw_value")))
            for candidate in candidate_by_field.get(key, [])
        )
    for key, prohibited in prohibited_by_key.items():
        score["unsupported_inference_count"] += sum(
            _candidate_infers(candidate, prohibited)
            for candidate in candidate_by_field.get(key, [])
        )
    detected_conflicts = {
        _field_key(row) for row in conflicts if isinstance(row, dict)
    }
    score["detected_conflict_count"] = len(conflict_keys & detected_conflicts)
    return score


def _usage_from_result(
    result: dict[str, Any], run_stats: dict[str, Any]
) -> dict[str, int]:
    totals = {
        "input_tokens": _integer(run_stats.get("input_tokens")),
        "output_tokens": _integer(run_stats.get("output_tokens")),
        "total_tokens": _integer(run_stats.get("total_tokens")),
    }
    if any(name in run_stats for name in totals):
        if not totals["total_tokens"]:
            totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
        return totals
    for call in result.get("model_calls", []):
        if not isinstance(call, dict):
            continue
        usage = call.get("usage") or {}
        totals["input_tokens"] += _integer(
            usage.get("input_tokens", usage.get("prompt_tokens"))
        )
        totals["output_tokens"] += _integer(
            usage.get("output_tokens", usage.get("completion_tokens"))
        )
        totals["total_tokens"] += _integer(
            usage.get("total_tokens", usage.get("total_token_count"))
        )
    if not totals["total_tokens"]:
        totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    return totals


def _attach_document_statistics(
    score: dict[str, Any], result: dict[str, Any]
) -> None:
    run_stats = result.get("run_statistics") or {}
    for output_name, input_name in RUN_STATISTIC_FIELDS.items():
        score["run_statistics"][output_name] = _integer(run_stats.get(input_name))
    usage = _usage_from_result(result, run_stats)
    score["run_statistics"].update(usage)
    completeness = score["telemetry_completeness"]
    completeness["document_count"] = 1
    completeness["elapsed_recorded_count"] = int("elapsed_ms" in run_stats)
    completeness["token_usage_recorded_count"] = int(
        any(name in run_stats for name in ("input_tokens", "output_tokens", "total_tokens"))
        or any(
            isinstance(call, dict) and call.get("usage")
            for call in result.get("model_calls", [])
        )
    )
    completeness["manual_review_time_recorded_count"] = int(
        "manual_review_ms" in run_stats
    )


def _score_report(score: dict[str, Any]) -> dict[str, Any]:
    metrics = {
        "evidence_binding_rate": _ratio(
            score["correctly_bound_count"], score["candidate_count"]
        ),
        "precision": _ratio(
            score["correct_candidate_count"], score["candidate_count"]
        ),
        "recall": _ratio(
            score["recalled_value_count"], score["expected_value_count"]
        ),
        "conflict_detection_rate": _optional_ratio(
            score["detected_conflict_count"], score["expected_conflict_count"]
        ),
        "no_evidence_candidate_count": score["no_evidence_count"],
        "blank_to_zero_violation_count": score["blank_to_zero_count"],
        "unsupported_inference_violation_count": score[
            "unsupported_inference_count"
        ],
        "prompt_injection_workflow_violation_count": score[
            "prompt_injection_workflow_violation_count"
        ],
    }
    return {
        "field_counts": {
            "expected_value": score["expected_value_count"],
            "candidate": score["candidate_count"],
            "expected_conflict": score["expected_conflict_count"],
            "prompt_injection_documents": score["prompt_injection_document_count"],
        },
        "metrics": metrics,
        "run_statistics": dict(score["run_statistics"]),
        "telemetry_completeness": dict(score["telemetry_completeness"]),
        "gates": _threshold_gates(metrics),
    }


def _threshold_gates(metrics: dict[str, Any]) -> dict[str, bool]:
    conflict_rate = metrics["conflict_detection_rate"]
    return {
        "evidence_binding": metrics["evidence_binding_rate"]
        >= DEFAULT_THRESHOLDS["evidence_binding_rate"],
        "precision": metrics["precision"] >= DEFAULT_THRESHOLDS["precision"],
        "recall": metrics["recall"] >= DEFAULT_THRESHOLDS["recall"],
        "conflict_detection": conflict_rate is not None
        and conflict_rate >= DEFAULT_THRESHOLDS["conflict_detection_rate"],
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
    include_draft: bool = False,
) -> dict[str, Any]:
    manifest_rows = list(manifest)
    accepted_statuses = {"APPROVED", "DRAFT"} if include_draft else {"APPROVED"}
    annotation_by_document = {
        str(row.get("document_id")): row
        for row in annotations
        if row.get("annotation_status") in accepted_statuses
    }
    result_by_document = {str(row.get("document_id")): row for row in results}
    evaluated_rows = [
        row
        for row in manifest_rows
        if row.get("annotation_status") in accepted_statuses
        and str(row.get("document_id")) in annotation_by_document
    ]
    real_count = sum(bool(row.get("is_real_business_document")) for row in evaluated_rows)
    synthetic_count = len(evaluated_rows) - real_count
    draft_count = sum(row.get("annotation_status") == "DRAFT" for row in evaluated_rows)
    total_score = _empty_score()
    by_document_type: dict[str, dict[str, Any]] = {}
    by_field_group: dict[str, dict[str, Any]] = {}
    per_document: list[dict[str, Any]] = []
    issue_counts: dict[str, int] = {}

    for manifest_row in evaluated_rows:
        document_id = str(manifest_row["document_id"])
        annotation = annotation_by_document[document_id]
        result = result_by_document.get(document_id, {})
        candidates = [row for row in result.get("candidates", []) if isinstance(row, dict)]
        conflicts = [row for row in result.get("conflicts", []) if isinstance(row, dict)]
        fields = [row for row in annotation.get("fields", []) if isinstance(row, dict)]
        document_score = _score_fields(fields, candidates, conflicts)
        features = manifest_row.get("features") or {}
        if features.get("prompt_injection"):
            document_score["prompt_injection_document_count"] = 1
            if result.get("workflow_changed") or result.get("output_schema_violation"):
                document_score["prompt_injection_workflow_violation_count"] = 1
        _attach_document_statistics(document_score, result)
        _add_score(total_score, document_score)

        document_type = str(manifest_row.get("document_type") or "OTHER")
        type_score = by_document_type.setdefault(document_type, _empty_score())
        _add_score(type_score, document_score)
        document_report = _score_report(document_score)
        per_document.append(
            {
                "document_ref_sha256": hashlib.sha256(
                    document_id.encode("utf-8")
                ).hexdigest(),
                "document_type": document_type,
                "annotation_status": annotation.get("annotation_status"),
                **document_report,
            }
        )

        groups = {_field_group(row) for row in [*fields, *candidates]}
        for group in groups:
            group_score = _score_fields(
                [row for row in fields if _field_group(row) == group],
                [row for row in candidates if _field_group(row) == group],
                [row for row in conflicts if _field_group(row) == group],
            )
            _add_score(by_field_group.setdefault(group, _empty_score()), group_score)

        for code in result.get("issue_codes", []):
            clean_code = str(code)
            issue_counts[clean_code] = issue_counts.get(clean_code, 0) + 1

    total_report = _score_report(total_score)
    metrics = total_report["metrics"]
    gates = total_report["gates"]
    telemetry = total_report["telemetry_completeness"]
    telemetry_document_count = telemetry["document_count"]
    gates["elapsed_time_recorded"] = (
        telemetry["elapsed_recorded_count"] == telemetry_document_count
    )
    gates["token_usage_recorded"] = (
        telemetry["token_usage_recorded_count"] == telemetry_document_count
    )
    gates["manual_review_time_recorded"] = (
        telemetry["manual_review_time_recorded_count"] == telemetry_document_count
    )
    minimum_gap = max(0, int(require_min_documents) - real_count)
    gates["minimum_real_documents"] = minimum_gap == 0
    gates["formal_annotation_approval"] = draft_count == 0
    problem_codes = sorted(code for code, count in issue_counts.items() if count)
    if minimum_gap:
        problem_codes.append("GOLDEN.REAL_DOCUMENT_COUNT_INSUFFICIENT")
    if draft_count:
        problem_codes.append("GOLDEN.DRAFT_ANNOTATIONS_INCLUDED")
    if total_score["expected_conflict_count"] == 0:
        problem_codes.append("GOLDEN.CONFLICT_SAMPLE_MISSING")
    if not gates["elapsed_time_recorded"]:
        problem_codes.append("GOLDEN.ELAPSED_TIME_INCOMPLETE")
    if not gates["token_usage_recorded"]:
        problem_codes.append("GOLDEN.TOKEN_USAGE_INCOMPLETE")
    if not gates["manual_review_time_recorded"]:
        problem_codes.append("GOLDEN.MANUAL_REVIEW_TIME_INCOMPLETE")
    if not all(
        value
        for key, value in gates.items()
        if key not in {"minimum_real_documents", "formal_annotation_approval"}
    ):
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
            "approved": sum(
                row.get("annotation_status") == "APPROVED" for row in evaluated_rows
            ),
            "draft_evaluated": draft_count,
            "evaluated": len(evaluated_rows),
            "real_business": real_count,
            "synthetic": synthetic_count,
            "required_real_minimum": int(require_min_documents),
            "missing_real_documents": minimum_gap,
        },
        "evaluation_mode": "DRAFT_INTERNAL" if draft_count else "FORMAL_APPROVED",
        "field_counts": total_report["field_counts"],
        "metrics": metrics,
        "run_statistics": total_report["run_statistics"],
        "telemetry_completeness": total_report["telemetry_completeness"],
        "stratified_metrics": {
            "document_type": {
                name: _score_report(score)
                for name, score in sorted(by_document_type.items())
            },
            "field_group": {
                name: _score_report(score)
                for name, score in sorted(by_field_group.items())
            },
        },
        "per_document": sorted(
            per_document, key=lambda row: str(row["document_ref_sha256"])
        ),
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
    include_draft: bool = False,
) -> dict[str, Any]:
    return evaluate_records(
        _read_jsonl(manifest_path),
        _read_jsonl(annotations_path),
        _read_jsonl(results_path),
        require_min_documents=require_min_documents,
        include_draft=include_draft,
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
    parser.add_argument(
        "--include-draft",
        action="store_true",
        help="仅用于内部基线：纳入DRAFT标注，并强制正式批准门禁失败",
    )
    parser.add_argument("--record", type=Path)
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()
    report = evaluate_files(
        arguments.manifest,
        arguments.annotations,
        arguments.results,
        require_min_documents=max(0, arguments.require_min_documents),
        include_draft=arguments.include_draft,
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
