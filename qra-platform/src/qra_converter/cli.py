from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any

from .mapping import resolve_profile_path
from .service import convert_sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="多来源文件转QRA标准JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)
    convert = subparsers.add_parser("convert", help="转换XLS、XLSX、CSV、DOCX和PDF源资料")
    convert.add_argument("--source-dir", required=True, type=Path)
    convert.add_argument("--profile", required=True)
    convert.add_argument("--output-dir", required=True, type=Path)
    convert.add_argument("--case-id")
    convert.add_argument("--project-name")
    convert.add_argument(
        "--contract-dir",
        type=Path,
        help="第一部分版本化合同目录；平台入口默认使用resources/contracts/part1/v1",
    )
    convert.add_argument(
        "--review-decisions",
        type=Path,
        help="人工复核决定JSON；应用后完整写入review_audit.json",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    contract_validator: Callable[[Any], Any] | None = None,
    capability_planner: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    default_contract_dir: Path | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        profile_path = resolve_profile_path(args.profile)
        summary = convert_sources(
            source_dir=args.source_dir,
            profile_path=profile_path,
            output_dir=args.output_dir,
            case_id=args.case_id,
            project_name=args.project_name,
            contract_validator=contract_validator,
            capability_planner=capability_planner,
            review_decisions_path=args.review_decisions,
            contract_dir=args.contract_dir or default_contract_dir,
        )
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"BLOCK: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["status"] == "READY_FOR_REVIEW" else 2


def platform_main(argv: list[str] | None = None) -> int:
    """Compose the converter with the platform's existing import validator."""
    validation_module = import_module("qra_engine.validation")
    dynamic_module = import_module("qra_engine.dynamic")
    return main(
        argv,
        contract_validator=validation_module.validate_import_contract,
        capability_planner=dynamic_module.plan_dynamic_flow,
        default_contract_dir=(Path.cwd() / "resources" / "contracts" / "part1" / "v1"),
    )


__all__ = ["build_parser", "main", "platform_main"]
