from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .engine import QRAEngine
from .reporting import (
    DEFAULT_CHART_IDS,
    REPORT_STYLE_ID,
    build_risk_matrix,
    render_charts,
    write_dashboard,
    write_risk_matrix_files,
)


AUTOMATION_SCHEMA_VERSION = "1.0.0"

CALCULATION_CATALOG: dict[str, dict[str, Any]] = {
    "validate-input": {
        "label_zh": "输入完整性与一致性校验",
        "kind": "validation",
        "profile": None,
        "supports_report_outputs": False,
        "description": "只校验输入，不执行风险计算。",
    },
    "human-qra": {
        "label_zh": "人员域QRA主链",
        "kind": "engine_profile",
        "profile": "aqt3046-physical",
        "supports_report_outputs": True,
        "description": "失效频率-泄漏-扩散-火灾爆炸-人员伤害-IR/F-N/PLL。",
    },
    "gbt34346-annex-c": {
        "label_zh": "GB/T 34346附录C独立校核",
        "kind": "engine_profile",
        "profile": "gbt34346-annex-c",
        "supports_report_outputs": False,
        "description": "运行附录C定量二级评价公式，不替代人员域QRA主链。",
    },
    "synthetic-chain": {
        "label_zh": "合成后果链回归测试",
        "kind": "engine_profile",
        "profile": "synthetic-chain",
        "supports_report_outputs": True,
        "description": "仅用于开发和回归测试。",
    },
    "golden-aggregate": {
        "label_zh": "预计算结果聚合回归",
        "kind": "engine_profile",
        "profile": "golden-aggregate",
        "supports_report_outputs": False,
        "description": "只验证聚合层黄金结果。",
    },
}


def calculation_catalog_document() -> dict[str, Any]:
    return {
        "schema_version": AUTOMATION_SCHEMA_VERSION,
        "calculations": [
            {"calculation_id": calculation_id, **metadata}
            for calculation_id, metadata in CALCULATION_CATALOG.items()
        ],
        "report_chart_ids": list(DEFAULT_CHART_IDS),
        "report_style_id": REPORT_STYLE_ID,
    }


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON根节点必须是对象：{path}")
    return value


def _write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    return path


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())
    return cleaned.strip("_") or "qra_job"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_automation_config(path: Path) -> dict[str, Any]:
    config_path = path.resolve()
    config = _read_json(config_path)
    config["_config_path"] = str(config_path)
    config["_base_dir"] = str(config_path.parent)
    return config


def build_direct_config(
    *,
    input_path: Path,
    output_dir: Path,
    calculations: Iterable[str],
    chart_ids: Iterable[str] | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": AUTOMATION_SCHEMA_VERSION,
        "job_id": job_id or f"QRA-{input_path.stem}",
        "input": str(input_path.resolve()),
        "calculations": list(calculations),
        "outputs": {
            "directory": str(output_dir.resolve()),
            "risk_matrix": True,
            "charts": list(DEFAULT_CHART_IDS if chart_ids is None else chart_ids),
            "html_dashboard": True,
        },
        "_base_dir": str(Path.cwd()),
    }


def _resolve_path(value: str, base_dir: Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base_dir / path).resolve()


def _validate_config(config: dict[str, Any]) -> tuple[Path, Path, list[str], dict[str, Any]]:
    schema_version = str(config.get("schema_version", ""))
    if schema_version != AUTOMATION_SCHEMA_VERSION:
        raise ValueError(
            f"自动化配置schema_version必须为{AUTOMATION_SCHEMA_VERSION}，当前为{schema_version or '空'}"
        )
    base_dir = Path(config.get("_base_dir") or Path.cwd()).resolve()
    if not config.get("input"):
        raise ValueError("自动化配置缺少input")
    input_path = _resolve_path(str(config["input"]), base_dir)
    if not input_path.is_file():
        raise FileNotFoundError(f"输入JSON不存在：{input_path}")
    calculations = [str(item) for item in config.get("calculations", [])]
    if not calculations:
        raise ValueError("至少选择一个计算项")
    unknown = [item for item in calculations if item not in CALCULATION_CATALOG]
    if unknown:
        raise ValueError(f"未知计算项：{', '.join(unknown)}")
    outputs = config.get("outputs") or {}
    if not outputs.get("directory"):
        raise ValueError("自动化配置缺少outputs.directory")
    output_dir = _resolve_path(str(outputs["directory"]), base_dir)
    chart_ids = outputs.get("charts", [])
    if chart_ids == "all":
        outputs["charts"] = list(DEFAULT_CHART_IDS)
    elif not isinstance(chart_ids, list):
        raise ValueError("outputs.charts必须是数组或字符串all")
    return input_path, output_dir, calculations, outputs


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _select_reporting_result(
    calculations: list[str], results: dict[str, dict[str, Any]], outputs: dict[str, Any]
) -> tuple[str, dict[str, Any]] | None:
    explicit = outputs.get("primary_calculation")
    candidates = [str(explicit)] if explicit else calculations
    for calculation_id in candidates:
        result = results.get(calculation_id)
        if not result:
            continue
        if (
            isinstance(result.get("human_risk"), dict)
            and "segment_risk" in result["human_risk"]
        ):
            return calculation_id, result
    return None


def run_automation(config: dict[str, Any], *, engine: QRAEngine | None = None) -> dict[str, Any]:
    input_path, output_dir, calculations, outputs = _validate_config(config)
    case = _read_json(input_path)
    engine = engine or QRAEngine()
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_paths: list[Path] = []
    results: dict[str, dict[str, Any]] = {}
    item_records: list[dict[str, Any]] = []

    validation = engine.validate(case)
    validation_path = _write_json(
        output_dir / "results" / "validation.json", validation.to_dict()
    )
    generated_paths.append(validation_path)

    if validation.errors:
        status = "BLOCK"
        for calculation_id in calculations:
            item_records.append(
                {
                    "calculation_id": calculation_id,
                    "status": "NOT_RUN_INPUT_BLOCKED",
                    "output": None,
                }
            )
    else:
        status = "PASS_WITH_WARNING" if validation.warnings else "PASS"
        for calculation_id in calculations:
            metadata = CALCULATION_CATALOG[calculation_id]
            if metadata["kind"] == "validation":
                item_records.append(
                    {
                        "calculation_id": calculation_id,
                        "status": "PASS_WITH_WARNING" if validation.warnings else "PASS",
                        "output": _relative(validation_path, output_dir),
                    }
                )
                continue
            profile = str(metadata["profile"])
            result = engine.run(case, profile=profile)
            result_path = _write_json(
                output_dir / "results" / f"{_slug(calculation_id)}.json", result
            )
            results[calculation_id] = result
            generated_paths.append(result_path)
            item_records.append(
                {
                    "calculation_id": calculation_id,
                    "profile": profile,
                    "status": "PASS",
                    "run_id": result.get("run", {}).get("run_id"),
                    "formal_report_allowed": result.get("run", {}).get(
                        "formal_report_allowed"
                    ),
                    "output": _relative(result_path, output_dir),
                }
            )

        wants_reporting = bool(outputs.get("risk_matrix", True)) or bool(
            outputs.get("charts")
        ) or bool(outputs.get("html_dashboard", True))
        selection = _select_reporting_result(calculations, results, outputs)
        if wants_reporting and selection is None:
            raise ValueError(
                "已请求风险矩阵或图表，但所选计算项没有产生管段空间QRA结果；请加入human-qra或synthetic-chain"
            )
        if selection is not None and wants_reporting:
            reporting_calculation, reporting_result = selection
            matrix = build_risk_matrix(
                reporting_result, config.get("risk_matrix_criteria")
            )
            matrix_paths: list[Path] = []
            if outputs.get("risk_matrix", True):
                matrix_paths = write_risk_matrix_files(
                    matrix, output_dir / "derived"
                )
                generated_paths.extend(matrix_paths)
            chart_paths: list[Path] = []
            chart_ids = outputs.get("charts", [])
            if chart_ids:
                chart_paths = render_charts(
                    reporting_result,
                    matrix,
                    output_dir / "charts",
                    chart_ids,
                )
                generated_paths.extend(chart_paths)
            dashboard_path: Path | None = None
            if outputs.get("html_dashboard", True):
                dashboard_path = write_dashboard(
                    reporting_result,
                    matrix,
                    chart_paths,
                    output_dir / "report_dashboard.html",
                )
                generated_paths.append(dashboard_path)
            item_records.append(
                {
                    "calculation_id": "reporting-postprocess",
                    "source_calculation_id": reporting_calculation,
                    "status": "PASS",
                    "risk_matrix_outputs": [
                        _relative(path, output_dir) for path in matrix_paths
                    ],
                    "chart_outputs": [
                        _relative(path, output_dir) for path in chart_paths
                    ],
                    "dashboard_output": (
                        _relative(dashboard_path, output_dir)
                        if dashboard_path is not None
                        else None
                    ),
                }
            )

    file_records = [
        {
            "path": _relative(path, output_dir),
            "sha256": _sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(generated_paths)
    ]
    manifest = {
        "schema_version": AUTOMATION_SCHEMA_VERSION,
        "job_id": str(config.get("job_id") or f"QRA-{input_path.stem}"),
        "status": status,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input": {
            "path": str(input_path),
            "sha256": _sha256_file(input_path),
        },
        "output_directory": str(output_dir),
        "requested_calculations": calculations,
        "items": item_records,
        "report_style_id": REPORT_STYLE_ID,
        "files": file_records,
    }
    manifest_path = _write_json(output_dir / "manifest.json", manifest)
    manifest["manifest_path"] = str(manifest_path)
    return manifest
