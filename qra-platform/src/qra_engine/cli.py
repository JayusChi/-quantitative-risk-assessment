from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .automation import (
    CALCULATION_CATALOG,
    build_direct_config,
    calculation_catalog_document,
    load_automation_config,
    run_automation,
)
from .engine import QRAEngine
from .errors import InputValidationError
from .model_registry import load_model_registry
from .reporting import DEFAULT_CHART_IDS
from .dynamic import NODE_BY_ID, dynamic_node_catalog, run_dynamic_flow


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="天然气输送管道人员域 QRA 计算引擎")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="只校验输入，不执行计算")
    validate_parser.add_argument("--input", required=True, type=Path)

    subparsers.add_parser("models", help="列出模型注册状态和正式发布阻断项")
    subparsers.add_parser("catalog", help="列出固定配置、动态计算节点和图表")

    run_parser = subparsers.add_parser("run", help="执行人员域 QRA 计算")
    run_parser.add_argument("--input", required=True, type=Path)
    run_parser.add_argument("--output", type=Path)
    run_parser.add_argument(
        "--profile",
        choices=(
            "aqt3046-physical",
            "synthetic-chain",
            "golden-aggregate",
            "gbt34346-annex-c",
        ),
        default="aqt3046-physical",
    )

    automate_parser = subparsers.add_parser(
        "automate", help="按任务配置自动选择计算项并输出JSON、风险矩阵和图表"
    )
    automate_parser.add_argument("--config", type=Path, help="自动化任务JSON配置")
    automate_parser.add_argument("--input", type=Path, help="直接运行时的QRA输入JSON")
    automate_parser.add_argument("--output-dir", type=Path, help="直接运行时的输出目录")
    automate_parser.add_argument(
        "--items",
        nargs="+",
        choices=tuple(CALCULATION_CATALOG),
        default=["human-qra"],
        help="直接运行时选择一个或多个计算项",
    )
    automate_parser.add_argument(
        "--charts",
        default="all",
        help="图表ID逗号分隔；all生成全部，none不生成图表",
    )
    automate_parser.add_argument("--job-id", help="任务编号")

    dynamic_parser = subparsers.add_parser(
        "dynamic", help="根据实际输入数据自动规划并执行所有可运行计算节点"
    )
    dynamic_source = dynamic_parser.add_mutually_exclusive_group(required=True)
    dynamic_source.add_argument("--config", type=Path, help="动态任务JSON配置")
    dynamic_source.add_argument("--input", type=Path, help="直接运行时的输入JSON")
    dynamic_parser.add_argument("--output-dir", type=Path)
    dynamic_parser.add_argument(
        "--targets",
        nargs="+",
        choices=tuple(NODE_BY_ID),
        help="可选：只规划指定目标及其依赖；省略则自动尝试全部节点",
    )
    dynamic_parser.add_argument(
        "--no-charts", action="store_true", help="不生成当前可用节点的SVG图表"
    )
    dynamic_parser.add_argument("--job-id", default="DYNAMIC-QRA")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "models":
        print(
            json.dumps(
                [registration.to_dict() for registration in load_model_registry()],
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "catalog":
        print(
            json.dumps(
                {
                    **calculation_catalog_document(),
                    "dynamic_flow": dynamic_node_catalog(),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if args.command == "dynamic":
        try:
            if args.config:
                config_path = args.config.resolve()
                config = _read_json(config_path)
                if config.get("schema_version") != "1.0.0":
                    raise ValueError("动态任务配置schema_version必须为1.0.0")
                base_dir = config_path.parent
                input_path = Path(str(config["input"]))
                output_dir = Path(str(config["output_directory"]))
                input_path = (
                    input_path.resolve()
                    if input_path.is_absolute()
                    else (base_dir / input_path).resolve()
                )
                output_dir = (
                    output_dir.resolve()
                    if output_dir.is_absolute()
                    else (base_dir / output_dir).resolve()
                )
                targets = config.get("targets")
                generate_charts = bool(config.get("generate_charts", True))
                job_id = str(config.get("job_id", "DYNAMIC-QRA"))
            else:
                if args.output_dir is None:
                    raise ValueError("直接运行dynamic时必须提供--output-dir")
                input_path = args.input
                output_dir = args.output_dir
                targets = args.targets
                generate_charts = not args.no_charts
                job_id = args.job_id
            case = _read_json(input_path)
            manifest = run_dynamic_flow(
                case,
                output_dir,
                targets=targets,
                generate_charts=generate_charts,
                job_id=job_id,
            )
        except (KeyError, ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"BLOCK: {exc}", file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "status": manifest["status"],
                    "job_id": manifest["job_id"],
                    "manifest": manifest["manifest_path"],
                    "output_directory": manifest["output_directory"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if manifest["status"] in {"PASS", "PARTIAL", "PARTIAL_DATA_ONLY"} else 2

    if args.command == "automate":
        if args.config:
            if args.input or args.output_dir:
                print("--config不能与--input/--output-dir同时使用", file=sys.stderr)
                return 2
            config = load_automation_config(args.config)
        else:
            if not args.input or not args.output_dir:
                print("直接运行automate时必须同时提供--input和--output-dir", file=sys.stderr)
                return 2
            if args.charts == "all":
                chart_ids = DEFAULT_CHART_IDS
            elif args.charts == "none":
                chart_ids = ()
            else:
                chart_ids = tuple(
                    item.strip() for item in args.charts.split(",") if item.strip()
                )
            config = build_direct_config(
                input_path=args.input,
                output_dir=args.output_dir,
                calculations=args.items,
                chart_ids=chart_ids,
                job_id=args.job_id,
            )
        try:
            manifest = run_automation(config)
        except (InputValidationError, ValueError, FileNotFoundError) as exc:
            print(f"BLOCK: {exc}", file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "status": manifest["status"],
                    "job_id": manifest["job_id"],
                    "manifest": manifest["manifest_path"],
                    "output_directory": manifest["output_directory"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2 if manifest["status"] == "BLOCK" else 0

    engine = QRAEngine()
    case = _read_json(args.input)

    if args.command == "validate":
        report = engine.validate(case)
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 2 if report.errors else 0

    try:
        result = engine.run(case, profile=args.profile)
    except InputValidationError as exc:
        error_document = {
            "status": "BLOCK",
            "issues": [issue.to_dict() for issue in exc.issues],
        }
        print(json.dumps(error_document, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    if args.output:
        _write_json(args.output, result)
        print(f"PASS: {args.output}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
