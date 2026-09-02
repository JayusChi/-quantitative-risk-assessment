from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from .backup import backup_database, restore_database, write_backup_manifest
from .database import QraDatabase
from .demo_release import prepare_full_synthetic_demo
from .engine_adapter import calculate_snapshot, preview_case
from .paths import DEFAULT_DATABASE, DEFAULT_RUNTIME_ROOT
from .server import serve

DEFAULT_SERVER_PORT = 8766


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _read_case(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("输入JSON的顶层必须是对象")
    return value


def _case_name(case: dict[str, Any], input_path: Path) -> str:
    metadata = case.get("metadata", {})
    return str(metadata.get("project_name") or metadata.get("case_id") or input_path.stem)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="独立数据库版天然气管道人员域QRA适配器")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE, help="SQLite数据库路径")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="初始化数据库")

    import_parser = subparsers.add_parser(
        "import-data", help="把结构化JSON作为不可变输入快照写入数据库"
    )
    import_parser.add_argument("--input", required=True, type=Path)
    import_parser.add_argument("--name", help="输入快照名称")

    calculate_parser = subparsers.add_parser("calculate", help="从数据库读取输入并执行动态QRA")
    calculate_parser.add_argument("--snapshot-id", help="输入快照编号；省略时使用最近导入的一份")
    calculate_parser.add_argument("--targets", nargs="+", help="可选目标节点")
    calculate_parser.add_argument("--no-charts", action="store_true")

    run_parser = subparsers.add_parser("run", help="一步完成JSON入库、数据库读取计算和结果回写")
    run_parser.add_argument("--input", required=True, type=Path)
    run_parser.add_argument("--name", help="输入快照名称")
    run_parser.add_argument("--targets", nargs="+", help="可选目标节点")
    run_parser.add_argument("--no-charts", action="store_true")

    subparsers.add_parser("snapshots", help="列出输入快照")
    subparsers.add_parser("runs", help="列出计算任务")

    export_parser = subparsers.add_parser(
        "export", help="把数据库中的某次结果重新导出为原文件目录结构"
    )
    export_parser.add_argument("--run-id", help="任务编号；省略时导出最近任务")
    export_parser.add_argument("--output-dir", required=True, type=Path)

    serve_parser = subparsers.add_parser("serve", help="启动从数据库读取报告资源的本地网页服务")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=DEFAULT_SERVER_PORT)
    serve_parser.add_argument("--tls-cert", type=Path)
    serve_parser.add_argument("--tls-key", type=Path)
    serve_parser.add_argument(
        "--trust-proxy-tls",
        action="store_true",
        default=None,
        help="仅在受信任反向代理终止TLS并覆盖客户端转发头时使用",
    )

    demo_parser = subparsers.add_parser(
        "load-demo", help="幂等加载、计算并生成全合成端到端演示项目"
    )
    demo_parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    demo_parser.add_argument("--no-report", action="store_true")
    demo_parser.add_argument("--actor", default="demo-launcher")

    backup_parser = subparsers.add_parser("backup", help="创建一致性SQLite演示状态备份")
    backup_parser.add_argument("--output", type=Path, required=True)
    backup_parser.add_argument("--replace", action="store_true")
    backup_parser.add_argument("--manifest", type=Path)

    restore_parser = subparsers.add_parser("restore", help="把QRA备份恢复到当前--database路径")
    restore_parser.add_argument("--input", type=Path, required=True)
    restore_parser.add_argument("--replace", action="store_true")
    return parser


def _import(database: QraDatabase, input_path: Path, name: str | None) -> tuple[str, bool]:
    resolved = input_path.resolve()
    case = _read_case(resolved)
    preview_case(case)
    return database.import_case(
        case,
        name=name or _case_name(case, resolved),
        source_path=str(resolved),
    )


def _calculate(
    database: QraDatabase,
    snapshot_id: str | None,
    targets: list[str] | None,
    no_charts: bool,
) -> dict[str, Any]:
    selected_snapshot = snapshot_id or database.latest_snapshot_id()
    return calculate_snapshot(
        database,
        selected_snapshot,
        targets=targets,
        generate_charts=not no_charts,
        runtime_root=DEFAULT_RUNTIME_ROOT,
    )


def _export(database: QraDatabase, run_id: str, output_dir: Path) -> int:
    root = output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    count = 0
    for artifact in database.list_artifacts(run_id):
        relative = PurePosixPath(str(artifact["path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"数据库资源路径不安全：{relative}")
        target = root.joinpath(*relative.parts).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"数据库资源超出导出目录：{relative}")
        stored = database.get_artifact(run_id, relative.as_posix())
        if stored is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(stored[1])
        count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database = QraDatabase(args.database)
    try:
        if args.command == "init":
            database.initialize()
            _print_json({"status": "PASS", "database": str(database.path)})
            return 0
        if args.command == "import-data":
            snapshot_id, created = _import(database, args.input, args.name)
            _print_json(
                {
                    "status": "PASS",
                    "snapshot_id": snapshot_id,
                    "created": created,
                    "metadata": database.snapshot_metadata(snapshot_id),
                }
            )
            return 0
        if args.command == "calculate":
            run = _calculate(database, args.snapshot_id, args.targets, args.no_charts)
            _print_json(
                {
                    "status": "PASS",
                    "run": run,
                    "report_url": (f"http://127.0.0.1:{DEFAULT_SERVER_PORT}/runs/{run['id']}/"),
                }
            )
            return 0
        if args.command == "run":
            snapshot_id, created = _import(database, args.input, args.name)
            run = _calculate(database, snapshot_id, args.targets, args.no_charts)
            _print_json(
                {
                    "status": "PASS",
                    "input_snapshot_created": created,
                    "snapshot_id": snapshot_id,
                    "run": run,
                    "report_url": (f"http://127.0.0.1:{DEFAULT_SERVER_PORT}/runs/{run['id']}/"),
                }
            )
            return 0
        if args.command == "snapshots":
            _print_json(database.list_snapshots())
            return 0
        if args.command == "runs":
            _print_json(database.list_runs())
            return 0
        if args.command == "export":
            run_id = args.run_id or database.latest_run_id()
            count = _export(database, run_id, args.output_dir)
            _print_json(
                {
                    "status": "PASS",
                    "run_id": run_id,
                    "exported_file_count": count,
                    "output_directory": str(args.output_dir.resolve()),
                }
            )
            return 0
        if args.command == "serve":
            serve(
                database,
                args.host,
                args.port,
                tls_cert=args.tls_cert,
                tls_key=args.tls_key,
                trust_proxy_tls=args.trust_proxy_tls,
            )
            return 0
        if args.command == "load-demo":
            result = prepare_full_synthetic_demo(
                database,
                runtime_root=args.runtime_root.resolve(),
                actor=args.actor,
                generate_report=not args.no_report,
            )
            _print_json(result)
            return 0
        if args.command == "backup":
            result = backup_database(database, args.output, replace=args.replace)
            if args.manifest:
                write_backup_manifest(args.manifest, result)
                result["manifest"] = str(args.manifest.resolve())
            _print_json(result)
            return 0
        if args.command == "restore":
            _print_json(
                restore_database(args.input, database.path, replace=args.replace)
            )
            return 0
    except (KeyError, ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"BLOCK: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
