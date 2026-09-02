"""Reconcile the stage-1 coverage-gap register against the implemented M1.5 chain."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FULL_CHAIN_ROOT = PROJECT_ROOT / "resources" / "synthetic" / "full-chain-v1"
OUTPUT_PATH = FULL_CHAIN_ROOT / "coverage-gap-closure.json"
SCENARIOS = (
    "S00_BASELINE",
    "S10_CORROSION_DEGRADATION",
    "S20_THIRD_PARTY_SURGE",
    "S30_HIGH_PRESSURE_POPULATION_PEAK",
    "S40_MITIGATION_PACKAGE",
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def _path_exists(value: Any, target_path: str) -> bool:
    current = [value]
    for token in target_path.split("."):
        next_values: list[Any] = []
        for item in current:
            if token == "*" and isinstance(item, list):
                next_values.extend(item)
            elif token == "*" and isinstance(item, dict):
                next_values.extend(item.values())
            elif isinstance(item, dict) and token in item:
                next_values.append(item[token])
        if not next_values:
            return False
        current = next_values
    return True


def _pattern_prefix_covers(container_path: str, target_path: str) -> bool:
    container = container_path.split(".")
    target = target_path.split(".")
    if len(container) > len(target):
        return False
    return all(
        left == "*" or right == "*" or left == right
        for left, right in zip(container, target, strict=False)
    )


def _scenario_snapshots() -> dict[str, dict[str, Any]]:
    snapshots: dict[str, dict[str, Any]] = {}
    for scenario_id in SCENARIOS:
        path = (
            FULL_CHAIN_ROOT
            / "stage2"
            / "generated"
            / f"{scenario_id}_D00_CLEAN"
            / "golden"
            / "expected-snapshot.json"
        )
        snapshots[scenario_id] = _read_json(path)["qra_input"]
    return snapshots


def build_closure() -> dict[str, Any]:
    with (FULL_CHAIN_ROOT / "field-source-node-matrix.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as stream:
        matrix = list(csv.DictReader(stream))
    gaps = _read_json(FULL_CHAIN_ROOT / "coverage-gap-register.json")["fields"]
    mapping = _read_json(FULL_CHAIN_ROOT / "stage3" / "synthetic-mapping.v1.json")
    run_assumptions = _read_json(FULL_CHAIN_ROOT / "stage3" / "run-assumptions.v1.json")
    expected_binding = _read_json(FULL_CHAIN_ROOT / "stage3" / "expected-binding.v1.json")
    structural_assembly = _read_json(
        FULL_CHAIN_ROOT / "stage3" / "structural-assembly.v1.json"
    )
    snapshots = _scenario_snapshots()

    matrix_by_id = {row["field_id"]: row for row in matrix}
    mapped_paths = {row["target_path"] for row in mapping["fields"]}
    run_paths = {row["target_path"] for row in run_assumptions["values"]}
    parameter_roots = set(expected_binding["snapshot_materialized_parameter_paths"])
    structural_paths = {
        row["target_path"]: row["assembly_method"]
        for row in structural_assembly["fields"]
    }

    fields: list[dict[str, Any]] = []
    for gap in gaps:
        target_path = gap["target_path"]
        data_layer = gap["data_layer"]
        scenario_presence = {
            scenario_id: _path_exists(snapshot, target_path)
            for scenario_id, snapshot in snapshots.items()
        }
        evidence_paths = sorted(
            path for path in mapped_paths if _pattern_prefix_covers(path, target_path)
        )
        run_bindings = sorted(
            path for path in run_paths if _pattern_prefix_covers(path, target_path)
        )
        parameter_bindings = sorted(
            path for path in parameter_roots if _pattern_prefix_covers(path, target_path)
        )

        if data_layer == "MODEL_PARAMETER" and parameter_bindings:
            implementation_status = "IMPLEMENTED"
            disposition = "VERIFIED_PARAMETER_PACK_MERGE"
            evidence = parameter_bindings
        elif data_layer == "RUN_ASSUMPTION" and run_bindings:
            implementation_status = "IMPLEMENTED"
            disposition = "VERIFIED_RUN_ASSUMPTION_ASSEMBLY"
            evidence = run_bindings
        elif data_layer == "PROJECT_FACT" and evidence_paths:
            implementation_status = "IMPLEMENTED"
            disposition = "VERIFIED_EVIDENCE_EXTRACTION_OR_AGGREGATE_MAPPING"
            evidence = evidence_paths
        elif data_layer == "PROJECT_FACT" and target_path in structural_paths:
            implementation_status = "IMPLEMENTED"
            disposition = "VERIFIED_STRUCTURAL_ASSEMBLY"
            evidence = [
                f"structural-assembly.v1.json:{structural_paths[target_path]}"
            ]
        elif all(scenario_presence.values()):
            implementation_status = "REFERENCE_ONLY"
            disposition = "REFERENCE_SNAPSHOT_ONLY_NOT_EXTRACTED"
            evidence = []
        else:
            implementation_status = "NOT_EXERCISED"
            disposition = "EXPLICITLY_UNEXERCISED_GENERAL_CONTRACT_FIELD"
            evidence = []

        fields.append(
            {
                **gap,
                "ledger_status": "CLOSED_WITH_DISPOSITION",
                "implementation_status": implementation_status,
                "disposition": disposition,
                "implementation_evidence_paths": evidence,
                "scenario_value_presence": scenario_presence,
                "missing_policy": matrix_by_id[gap["field_id"]]["missing_policy"],
            }
        )

    implementation_counts = Counter(row["implementation_status"] for row in fields)
    disposition_counts = Counter(row["disposition"] for row in fields)
    unresolved_blocking = [
        row
        for row in fields
        if row["criticality"] == "BLOCKING" and row["implementation_status"] != "IMPLEMENTED"
    ]
    all_original_gaps_implemented = len(fields) == len(gaps) and all(
        row["implementation_status"] == "IMPLEMENTED" for row in fields
    )
    result: dict[str, Any] = {
        "schema_version": "1.0.0",
        "status": (
            "IMPLEMENTATION_COMPLETE"
            if all_original_gaps_implemented
            else "DISPOSITION_COMPLETE_IMPLEMENTATION_PARTIAL"
        ),
        "scope": "M1.5_SYNTHETIC_S00_TO_S40",
        "supersedes": "coverage-gap-register.json:status=IDENTIFIED",
        "verified_on": "2026-09-02",
        "contract_field_count": len(matrix),
        "original_gap_field_count": len(gaps),
        "ledger_disposition_count": len(fields),
        "implementation_counts": dict(sorted(implementation_counts.items())),
        "disposition_counts": dict(sorted(disposition_counts.items())),
        "unresolved_blocking_count": len(unresolved_blocking),
        "unresolved_blocking_field_ids": [row["field_id"] for row in unresolved_blocking],
        "claims": {
            "all_original_gaps_have_current_disposition": len(fields) == len(gaps),
            "all_361_contract_fields_are_evidence_extracted": False,
            "all_361_contract_fields_have_verified_implementation": (
                len(matrix) == 361 and all_original_gaps_implemented
            ),
            "full_contract_registration_is_complete": len(matrix) == 361,
            "m1_5_blocking_fields_are_implemented": not unresolved_blocking,
            "project_fact_evidence_or_structural_assembly_complete": (
                all_original_gaps_implemented
            ),
            "synthetic_reference_snapshot_is_not_extraction_evidence": True,
        },
        "fields": fields,
    }
    hash_payload = dict(result)
    result["business_content_sha256"] = hashlib.sha256(_canonical_bytes(hash_payload)).hexdigest()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = build_closure()
    if args.check:
        if not OUTPUT_PATH.is_file() or _read_json(OUTPUT_PATH) != result:
            raise SystemExit("coverage-gap-closure.json is missing or stale")
    else:
        _write_json(OUTPUT_PATH, result)
    print(json.dumps({key: result[key] for key in (
        "status",
        "contract_field_count",
        "original_gap_field_count",
        "implementation_counts",
        "unresolved_blocking_count",
        "business_content_sha256",
    )}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
