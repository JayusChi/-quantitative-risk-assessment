from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any


from qra_engine import QRAEngine
from qra_engine.audit import sha256_json


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}顶层必须是JSON对象")
    return value


def _require_approved_independent_reference(reference: dict[str, Any]) -> None:
    approval = reference.get("approval", {})
    source = reference.get("source", {})
    if approval.get("status") != "APPROVED":
        raise ValueError("外部基准approval.status必须为APPROVED")
    if not str(approval.get("approved_by") or "").strip():
        raise ValueError("外部基准必须记录approved_by")
    if source.get("independent_of_qra_engine") is not True:
        raise ValueError("外部基准必须明确独立于本QRA引擎")
    source_hash = str(source.get("result_file_sha256") or "").lower()
    if not SHA256_PATTERN.fullmatch(source_hash):
        raise ValueError("外部结果文件必须记录有效的SHA-256")
    if not str(source.get("organization") or "").strip():
        raise ValueError("外部基准必须记录结果出具机构")


def _compare(
    checks: list[dict[str, Any]],
    name: str,
    actual: float,
    expected: float,
    relative_tolerance: float,
    absolute_tolerance: float,
) -> None:
    error = abs(actual - expected)
    allowed = max(absolute_tolerance, abs(expected) * relative_tolerance)
    checks.append(
        {
            "metric": name,
            "actual": actual,
            "expected": expected,
            "absolute_error": error,
            "allowed_error": allowed,
            "passed": error <= allowed,
        }
    )


def compare(
    case: dict[str, Any],
    reference: dict[str, Any],
    *,
    source_result_content: bytes,
) -> dict[str, Any]:
    _require_approved_independent_reference(reference)
    actual_source_hash = hashlib.sha256(source_result_content).hexdigest()
    declared_source_hash = str(reference["source"]["result_file_sha256"]).lower()
    if actual_source_hash != declared_source_hash:
        raise ValueError(
            "外部原始结果文件SHA-256与reference.source.result_file_sha256不一致"
        )
    expected = reference.get("expected")
    if not isinstance(expected, dict):
        raise ValueError("外部基准缺少expected结果")
    tolerances = reference.get("tolerances", {})
    relative = float(tolerances.get("relative", 0.05))
    absolute = float(tolerances.get("absolute", 1.0e-12))
    if relative < 0.0 or absolute < 0.0:
        raise ValueError("容差不得为负数")

    result = QRAEngine().run(case, profile=str(reference.get("profile") or "aqt3046-physical"))
    checks: list[dict[str, Any]] = []
    release_summary = result["calculation_diagnostics"]["physical_consequence_model"][
        "release_rate_summary"
    ]
    for loc_id, values in sorted(expected.get("source_term_release_rate_kg_s", {}).items()):
        actual_values = release_summary[loc_id]
        for bound in ("minimum_kg_s", "maximum_kg_s"):
            _compare(
                checks,
                f"source_term.{loc_id}.{bound}",
                float(actual_values[bound]),
                float(values[bound]),
                relative,
                absolute,
            )

    ranking = {
        str(row["segment_id"]): row
        for row in result["human_risk"]["segment_risk"]["ranking"]
    }
    for row in expected.get("fatal_heat_flux_distance_m", []):
        segment_id = str(row["segment_id"])
        actual_distance = ranking[segment_id]["dominant_risk_scenario"].get(
            "fatal_heat_flux_distance_m"
        )
        if actual_distance is None:
            raise ValueError(f"{segment_id}主导场景没有致死热辐射距离")
        _compare(
            checks,
            f"consequence.{segment_id}.fatal_heat_flux_distance_m",
            float(actual_distance),
            float(row["value"]),
            relative,
            absolute,
        )

    human = result["human_risk"]
    _compare(
        checks,
        "risk.maximum_ir_per_year",
        float(human["individual_risk"]["maximum"]["value_per_year"]),
        float(expected["maximum_ir_per_year"]),
        relative,
        absolute,
    )
    _compare(
        checks,
        "risk.pipeline_pll_per_year",
        float(human["societal_risk"]["pipeline_pll_per_year"]),
        float(expected["pipeline_pll_per_year"]),
        relative,
        absolute,
    )
    actual_fn = {
        float(row["fatalities_at_least"]): float(row["cumulative_frequency_per_year"])
        for row in human["societal_risk"]["fn_curve"]
    }
    for row in expected.get("fn_curve", []):
        threshold = float(row["fatalities_at_least"])
        _compare(
            checks,
            f"risk.fn.F(N>={threshold:g})",
            actual_fn[threshold],
            float(row["cumulative_frequency_per_year"]),
            relative,
            absolute,
        )

    return {
        "schema_version": "external-benchmark-comparison-v1",
        "benchmark_id": reference.get("benchmark_id"),
        "input_sha256": sha256_json(case),
        "external_result_file_sha256": reference["source"]["result_file_sha256"],
        "passed": bool(checks) and all(row["passed"] for row in checks),
        "check_count": len(checks),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="比较经批准的独立完整QRA基准")
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--source-result-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    comparison = compare(
        _load(arguments.case),
        _load(arguments.reference),
        source_result_content=arguments.source_result_file.read_bytes(),
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("PASS" if comparison["passed"] else "FAIL")
    return 0 if comparison["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
