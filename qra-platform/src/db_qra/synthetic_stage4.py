"""Stage-4 immutable-snapshot-to-11-node synthetic acceptance workflow."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from qra_engine import ENGINE_VERSION
from qra_engine.audit import sha256_numerical_result
from qra_engine.dynamic import NODE_REGISTRY, plan_dynamic_flow, run_dynamic_flow

from .database import QraDatabase, json_sha256
from .engine_adapter import DB_ADAPTER_VERSION, calculate_snapshot

STAGE4_VERSION = "qra.synthetic-stage4/1.0.0"
GATE_NAME = "S4_FULL_11_OF_11_SYNTHETIC_PASS"
EXPECTED_NODE_COUNT = 11
CONSERVATION_TOLERANCE = 1.0e-12


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _node_documents(directory: Path) -> dict[str, dict[str, Any]]:
    return {
        path.stem: read_json(path)
        for path in sorted((directory / "nodes").glob("*.json"))
    }


def _export_database_artifacts(
    database: QraDatabase, run_id: str, destination: Path
) -> list[str]:
    exported = []
    for artifact in database.list_artifacts(run_id):
        relative = str(artifact["path"])
        stored = database.get_artifact(run_id, relative)
        if stored is None:
            raise RuntimeError(f"数据库计算产物丢失：{run_id}/{relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(stored[1])
        exported.append(relative)
    return exported


def _pack_bindings(
    stage3_snapshot: dict[str, Any], parameter_pack_dir: Path
) -> list[dict[str, Any]]:
    verified = []
    for binding in stage3_snapshot.get("parameter_pack_bindings", []):
        pack_id = str(binding["pack_id"])
        path = parameter_pack_dir / f"{pack_id}.json"
        pack = read_json(path)
        expected_hash = str(binding["business_content_sha256"])
        actual_hash = str(pack.get("business_content_sha256"))
        verified.append(
            {
                "pack_id": pack_id,
                "business_content_sha256": expected_hash,
                "actual_business_content_sha256": actual_hash,
                "file_sha256": file_sha256(path),
                "source_path": path.as_posix(),
                "verified": actual_hash == expected_hash,
            }
        )
    return verified


def _result_metrics(nodes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    geometry = nodes["segment_geometry"]
    frequency = nodes["failure_frequency"]
    human = nodes["human_qra"]
    societal = human["human_risk"]["societal_risk"]
    individual = human["human_risk"]["individual_risk"]
    ranking = societal["segment_ranking"]
    return {
        "segment_count": int(geometry["segment_count"]),
        "annual_failure_frequency_per_year": frequency[
            "total_initiating_frequency_per_year"
        ],
        "at_least_one_failure_probability_over_horizon": frequency[
            "at_least_one_failure_probability_over_horizon"
        ],
        "failure_probability_horizon_years": frequency["failure_probability_model"][
            "exposure_years"
        ],
        "pipeline_pll_per_year": societal["pipeline_pll_per_year"],
        "maximum_individual_risk_per_year": individual["maximum"]["value_per_year"],
        "maximum_individual_risk_receptor_id": individual["maximum"]["cell_id"],
        "fn_curve": societal["fn_curve"],
        "top_risk_segment_id": ranking[0]["segment_id"],
        "top_risk_segment_pll_per_year": ranking[0]["pll_per_year"],
    }


def _max_poisson_error(
    frequencies: dict[str, Any], probabilities: dict[str, Any], horizon: float
) -> float:
    return max(
        (
            abs(float(probabilities[key]) - (1.0 - math.exp(-float(value) * horizon)))
            for key, value in frequencies.items()
        ),
        default=0.0,
    )


def build_conservation_report(
    case: dict[str, Any], nodes: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    frequency = nodes["failure_frequency"]
    human = nodes["human_qra"]
    diagnostics = human["calculation_diagnostics"]
    societal = human["human_risk"]["societal_risk"]
    total = float(frequency["total_initiating_frequency_per_year"])
    horizon = float(frequency["failure_probability_model"]["exposure_years"])
    branch_sum = math.fsum(
        float(value) for value in diagnostics["branch_frequency_per_year"].values()
    )
    fn_curve = societal["fn_curve"]
    errors = {
        "frequency_by_segment_error": math.fsum(
            float(value) for value in frequency["frequency_by_segment_per_year"].values()
        )
        - total,
        "frequency_by_mechanism_error": math.fsum(
            float(value) for value in frequency["frequency_by_mechanism_per_year"].values()
        )
        - total,
        "frequency_by_loc_error": math.fsum(
            float(value) for value in frequency["frequency_by_loc_per_year"].values()
        )
        - total,
        "total_poisson_probability_error": float(
            frequency["at_least_one_failure_probability_over_horizon"]
        )
        - (1.0 - math.exp(-total * horizon)),
        "segment_poisson_probability_max_error": _max_poisson_error(
            frequency["frequency_by_segment_per_year"],
            frequency["failure_probability_by_segment_over_horizon"],
            horizon,
        ),
        "mechanism_poisson_probability_max_error": _max_poisson_error(
            frequency["frequency_by_mechanism_per_year"],
            frequency["failure_probability_by_mechanism_over_horizon"],
            horizon,
        ),
        "loc_poisson_probability_max_error": _max_poisson_error(
            frequency["frequency_by_loc_per_year"],
            frequency["failure_probability_by_loc_over_horizon"],
            horizon,
        ),
        "scenario_branch_frequency_error": branch_sum - total,
        "engine_scenario_branch_frequency_error": float(
            diagnostics["frequency_balance_error"]
        ),
        "weather_probability_sum_error": math.fsum(
            float(row["probability"]) for row in case["weather_joint_probability"]
        )
        - 1.0,
        "gas_composition_sum_error": math.fsum(
            float(value) for value in case["pipeline"]["gas_composition_mole_fraction"].values()
        )
        - 1.0,
        "pll_segment_sum_error": math.fsum(
            float(value) for value in societal["segment_pll_per_year"].values()
        )
        - float(societal["pipeline_pll_per_year"]),
    }
    fn_monotonic = all(
        float(left["cumulative_frequency_per_year"])
        >= float(right["cumulative_frequency_per_year"])
        for left, right in zip(fn_curve, fn_curve[1:], strict=False)
    )
    passed = all(abs(value) <= CONSERVATION_TOLERANCE for value in errors.values())
    passed = passed and fn_monotonic
    return {
        "schema_version": "1.0.0",
        "status": "PASS" if passed else "FAIL",
        "tolerance": CONSERVATION_TOLERANCE,
        "total_initiating_frequency_per_year": total,
        "total_expanded_branch_frequency_per_year": diagnostics[
            "total_expanded_branch_frequency_per_year"
        ],
        "errors": errors,
        "fn_curve_cumulative_frequency_non_increasing": fn_monotonic,
        "probability_rule": "P(N>=1)=1-exp(-lambda*t); probabilities are never summed",
    }


def _model_bindings(value: Any, prefix: str = "") -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, item in sorted(value.items()):
            path = f"{prefix}.{key}" if prefix else str(key)
            if (
                key in {"model_id", "model_version", "model_status", "calculation_profile"}
                or key.endswith("_model_id")
                or key.endswith("_model_version")
                or key.endswith("_model_status")
            ) and isinstance(item, str | int | float | bool):
                bindings.append({"path": path, "value": item})
            bindings.extend(_model_bindings(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            bindings.extend(_model_bindings(item, f"{prefix}[{index}]"))
    return bindings


def build_lineage_report(
    *,
    snapshot_id: str,
    snapshot_sha256: str,
    snapshot_metadata: dict[str, Any],
    job_binding: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    conversion = snapshot_metadata.get("conversion") or {}
    source_conversion = {
        key: conversion.get(key)
        for key in (
            "conversion_job_id",
            "converter_version",
            "mapping_profile_id",
            "mapping_version",
            "mapping_sha256",
            "case_sha256",
            "confirmed_by",
            "confirmed_at",
        )
    }
    node_lineage = []
    for node_id, result in sorted(nodes.items()):
        node_lineage.append(
            {
                "node_id": node_id,
                "result_sha256": json_sha256(result),
                "input_snapshot_id": snapshot_id,
                "input_snapshot_sha256": snapshot_sha256,
                "parameter_pack_ids": [
                    row["pack_id"] for row in job_binding["parameter_pack_bindings"]
                ],
                "run_assumption_binding": job_binding["run_assumption_binding"],
                "engine_version": job_binding["engine_version"],
                "model_bindings": _model_bindings(result),
            }
        )
    complete = all(
        row["input_snapshot_id"]
        and row["input_snapshot_sha256"]
        and row["parameter_pack_ids"]
        and row["run_assumption_binding"]
        and row["engine_version"]
        for row in node_lineage
    )
    return {
        "schema_version": "1.0.0",
        "status": "PASS" if complete else "FAIL",
        "calculation_run_id": job_binding["calculation_run_id"],
        "source_conversion": source_conversion,
        "node_lineage": node_lineage,
    }


class SyntheticStage4Workflow:
    """Run the positive and missing-data calculation paths for stage 4."""

    def __init__(self, project_root: Path | str):
        self.project_root = Path(project_root).resolve()
        self.pack_root = (
            self.project_root
            / "resources"
            / "synthetic"
            / "full-chain-v1"
            / "stage2"
            / "generated"
            / "S00_BASELINE_D00_CLEAN"
        )
        self.stage3_root = (
            self.project_root / "resources" / "synthetic" / "full-chain-v1" / "stage3"
        )
        self.expected_result_path = self.pack_root / "golden" / "expected-result.json"
        self.expected_node_root = self.pack_root / "golden" / "expected-results"

    def run(
        self,
        *,
        stage3_snapshot_path: Path | str,
        database_path: Path | str,
        output_root: Path | str,
        snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        stage3_snapshot_path = Path(stage3_snapshot_path).resolve()
        database_path = Path(database_path).resolve()
        output_root = Path(output_root).resolve()
        if not database_path.is_file():
            raise FileNotFoundError(f"阶段3不可变快照数据库不存在：{database_path}")
        stage3_snapshot = read_json(stage3_snapshot_path)
        case = stage3_snapshot["qra_input"]
        case_sha256 = json_sha256(case)
        if case_sha256 != str(stage3_snapshot["qra_input_sha256"]):
            raise ValueError("阶段3快照封套内的QRA输入哈希不一致")

        database = QraDatabase(database_path)
        database.initialize()
        snapshot_id = snapshot_id or database.latest_snapshot_id()
        snapshot_metadata = database.snapshot_metadata(snapshot_id)
        database_case = database.load_snapshot(snapshot_id)
        if json_sha256(database_case) != case_sha256:
            raise ValueError("数据库不可变快照与阶段3确认快照不一致")

        plan = plan_dynamic_flow(database_case)
        runnable = plan["runnable_node_ids"]
        expected_node_ids = [node.node_id for node in NODE_REGISTRY]
        packs = _pack_bindings(stage3_snapshot, self.pack_root / "parameter-packs")
        assumptions = read_json(self.stage3_root / "run-assumptions.v1.json")
        assumption_verified = (
            assumptions["binding_id"] == stage3_snapshot["run_assumption_binding"]
        )

        database_run = calculate_snapshot(
            database,
            snapshot_id,
            generate_charts=False,
            runtime_root=output_root / "runtime",
        )
        database_run_id = str(database_run["id"])
        database_output = output_root / "source-chain-db-run"
        exported = _export_database_artifacts(database, database_run_id, database_output)
        database_nodes = {
            node_id: database.get_result_document(database_run_id, node_id)
            for node_id in expected_node_ids
        }
        database_manifest = read_json(database_output / "dynamic_manifest.json")

        direct_output = output_root / "direct-json-run"
        direct_manifest = run_dynamic_flow(
            case,
            direct_output,
            generate_charts=False,
            job_id="STAGE4-DIRECT-JSON-RERUN",
        )
        direct_nodes = _node_documents(direct_output)

        expected_result = read_json(self.expected_result_path)
        expected_nodes = _node_documents(self.expected_node_root)
        diff_rows = []
        for node_id in expected_node_ids:
            expected_hash = str(expected_result["node_result_sha256"][node_id])
            source_hash = json_sha256(database_nodes[node_id])
            direct_hash = json_sha256(direct_nodes[node_id])
            expected_numerical_hash = sha256_numerical_result(expected_nodes[node_id])
            source_numerical_hash = sha256_numerical_result(database_nodes[node_id])
            direct_numerical_hash = sha256_numerical_result(direct_nodes[node_id])
            diff_rows.append(
                {
                    "node_id": node_id,
                    "expected_raw_sha256": expected_hash,
                    "source_chain_raw_sha256": source_hash,
                    "direct_json_raw_sha256": direct_hash,
                    "expected_numerical_sha256": expected_numerical_hash,
                    "source_chain_numerical_sha256": source_numerical_hash,
                    "direct_json_numerical_sha256": direct_numerical_hash,
                    "source_chain_equals_direct_json": database_nodes[node_id]
                    == direct_nodes[node_id],
                    "source_chain_raw_equals_golden": database_nodes[node_id]
                    == expected_nodes[node_id],
                    "direct_json_raw_equals_golden": direct_nodes[node_id]
                    == expected_nodes[node_id],
                    "source_chain_numerically_equals_golden": source_numerical_hash
                    == expected_numerical_hash,
                    "direct_json_numerically_equals_golden": direct_numerical_hash
                    == expected_numerical_hash,
                }
            )
        diff_pass = all(
            row["source_chain_equals_direct_json"]
            and row["source_chain_numerically_equals_golden"]
            and row["direct_json_numerically_equals_golden"]
            for row in diff_rows
        )
        metrics = _result_metrics(direct_nodes)
        expected_metrics = _result_metrics(expected_nodes)
        metrics_equal = sha256_numerical_result(metrics) == sha256_numerical_result(
            expected_metrics
        )
        diff_report = {
            "schema_version": "1.0.0",
            "status": "PASS" if diff_pass and metrics_equal else "FAIL",
            "comparison_scope": "raw-source-confirmed DB snapshot vs direct JSON vs stage-2 golden",
            "comparison_policy": (
                "source-chain and direct-JSON results must be raw-identical; golden comparison "
                "uses the engine numerical contract (12 significant digits and canonical ordering)"
            ),
            "node_diffs": diff_rows,
            "actual_metrics": metrics,
            "expected_metrics": expected_metrics,
            "metrics_equal": metrics_equal,
            "mismatch_count": sum(
                not (
                    row["source_chain_equals_direct_json"]
                    and row["source_chain_numerically_equals_golden"]
                    and row["direct_json_numerically_equals_golden"]
                )
                for row in diff_rows
            ),
            "raw_representation_mismatch_count": sum(
                not (
                    row["source_chain_raw_equals_golden"]
                    and row["direct_json_raw_equals_golden"]
                )
                for row in diff_rows
            ),
        }
        write_json(output_root / "result-diff-report.json", diff_report)

        replay_report = {
            "schema_version": "1.0.0",
            "status": (
                "PASS"
                if database_manifest["numerical_result_sha256"]
                == direct_manifest["numerical_result_sha256"]
                == expected_result["numerical_result_sha256"]
                else "FAIL"
            ),
            "snapshot_sha256": case_sha256,
            "source_chain_numerical_result_sha256": database_manifest[
                "numerical_result_sha256"
            ],
            "direct_json_rerun_numerical_result_sha256": direct_manifest[
                "numerical_result_sha256"
            ],
            "expected_numerical_result_sha256": expected_result[
                "numerical_result_sha256"
            ],
            "hash_scope": direct_manifest["numerical_result_hash_scope"],
        }
        write_json(output_root / "deterministic-rerun-record.json", replay_report)

        conservation = build_conservation_report(case, direct_nodes)
        write_json(output_root / "conservation-report.json", conservation)

        job_binding = {
            "schema_version": "1.0.0",
            "stage4_workflow_version": STAGE4_VERSION,
            "calculation_run_id": database_run_id,
            "calculation_status": database_run["status"],
            "input_snapshot_id": snapshot_id,
            "input_snapshot_sha256": case_sha256,
            "input_snapshot_business_sha256": stage3_snapshot["business_content_sha256"],
            "parameter_pack_bindings": packs,
            "all_parameter_packs_verified": all(row["verified"] for row in packs),
            "run_assumption_binding": stage3_snapshot["run_assumption_binding"],
            "run_assumption_business_sha256": assumptions["business_content_sha256"],
            "run_assumption_verified": assumption_verified,
            "engine_version": f"qra-engine/{ENGINE_VERSION}; db-adapter/{DB_ADAPTER_VERSION}",
            "target_node_ids": expected_node_ids,
            "formal_report_allowed": bool(stage3_snapshot["formal_report_allowed"]),
        }
        write_json(output_root / "calculation-job-binding.json", job_binding)

        lineage = build_lineage_report(
            snapshot_id=snapshot_id,
            snapshot_sha256=case_sha256,
            snapshot_metadata=snapshot_metadata,
            job_binding=job_binding,
            nodes=database_nodes,
        )
        write_json(output_root / "reverse-provenance-record.json", lineage)

        standard_path = {
            "schema_version": "1.0.0",
            "nodes": [
                {
                    "node_id": row["node_id"],
                    "standard": row["standard"],
                    "status": row["status"],
                }
                for row in plan["plan"]
            ],
            "failure_probability_model": direct_nodes["failure_frequency"][
                "failure_probability_model"
            ],
            "human_qra_model_trace": direct_nodes["human_qra"]["model_trace"],
            "gbt34346_formula_trace": direct_nodes["gbt34346_annex_c"]["formula_trace"],
            "adaptive_formula_trace": direct_nodes["adaptive_evidence_qra"]["formula_trace"],
        }
        write_json(output_root / "standard-formula-path.json", standard_path)

        missing_case = copy.deepcopy(case)
        missing_case.pop("population_cells", None)
        missing_plan = plan_dynamic_flow(missing_case)
        missing_output = output_root / "D20_MISSING"
        missing_manifest = run_dynamic_flow(
            missing_case,
            missing_output,
            generate_charts=False,
            job_id="STAGE4-D20-MISSING",
        )
        missing_capability = read_json(missing_output / "capability_report.json")
        fill_data_list = [
            {
                "path": row["path"],
                "label_zh": row["label_zh"],
                "value": None,
                "state": "MISSING_NOT_ZERO",
            }
            for row in missing_capability["missing_inputs"]
        ]
        blocked = missing_plan["skipped_node_ids"]
        blocked_not_completed = all(
            row["status"] != "COMPLETED"
            for row in missing_manifest["nodes"]
            if row["node_id"] in blocked
        )
        d20_report = {
            "schema_version": "1.0.0",
            "condition_id": "D20_MISSING",
            "status": (
                "PASS"
                if blocked
                and blocked_not_completed
                and "population_cells" not in missing_case
                and all(row["value"] is None for row in fill_data_list)
                else "FAIL"
            ),
            "calculation_status": missing_manifest["status"],
            "blocked_node_ids": blocked,
            "blocked_nodes_not_completed": blocked_not_completed,
            "completed_node_ids": missing_capability["completed_node_ids"],
            "fill_data_list": fill_data_list,
            "missing_values_coerced_to_zero": False,
            "persisted_as_snapshot": False,
            "formal_report_allowed": missing_capability["risk_result"][
                "formal_acceptance_judgement_allowed"
            ],
        }
        write_json(output_root / "D20-missing-data-report.json", d20_report)

        full_node_pass = (
            runnable == expected_node_ids
            and len(database.list_nodes(database_run_id)) == EXPECTED_NODE_COUNT
            and all(
                row["status"] == "COMPLETED"
                for row in database.list_nodes(database_run_id)
            )
        )
        formal_report_allowed = any(
            (
                bool(job_binding["formal_report_allowed"]),
                bool(database_run["summary"]["formal_acceptance_judgement_allowed"]),
                bool(direct_nodes["human_qra"]["run"]["formal_report_allowed"]),
                bool(d20_report["formal_report_allowed"]),
            )
        )
        summary = {
            "schema_version": "1.0.0",
            "stage": 4,
            "gate": GATE_NAME,
            "status": (
                "PASS"
                if all(
                    (
                        full_node_pass,
                        database_run["status"] == "COMPLETED",
                        job_binding["all_parameter_packs_verified"],
                        job_binding["run_assumption_verified"],
                        diff_report["status"] == "PASS",
                        replay_report["status"] == "PASS",
                        conservation["status"] == "PASS",
                        lineage["status"] == "PASS",
                        d20_report["status"] == "PASS",
                        not formal_report_allowed,
                    )
                )
                else "FAIL"
            ),
            "snapshot_id": snapshot_id,
            "snapshot_sha256": case_sha256,
            "calculation_run_id": database_run_id,
            "database_run_status": database_run["status"],
            "completed_node_count": database_run["summary"]["completed_node_count"],
            "failed_node_count": database_run["summary"]["failed_node_count"],
            "skipped_node_count": database_run["summary"]["skipped_node_count"],
            "numerical_result_sha256": direct_manifest["numerical_result_sha256"],
            "metrics": metrics,
            "formal_report_allowed": formal_report_allowed,
            "exported_database_artifact_count": len(exported),
        }
        write_json(output_root / "stage4-run-summary.json", summary)
        return {
            "summary": summary,
            "job_binding": job_binding,
            "capability_plan": plan,
            "diff_report": diff_report,
            "replay_report": replay_report,
            "conservation_report": conservation,
            "lineage_report": lineage,
            "d20_report": d20_report,
            "database_run": database_run,
            "direct_manifest": direct_manifest,
        }


__all__ = [
    "CONSERVATION_TOLERANCE",
    "EXPECTED_NODE_COUNT",
    "GATE_NAME",
    "STAGE4_VERSION",
    "SyntheticStage4Workflow",
    "build_conservation_report",
    "build_lineage_report",
    "file_sha256",
    "read_json",
    "write_json",
]
