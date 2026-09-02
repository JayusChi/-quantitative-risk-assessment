"""Build the reproducible QRA full-synthetic end-to-end demo release bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from db_qra.demo_release import DEMO_RELEASE_NAME, DEMO_RELEASE_VERSION  # noqa: E402

DEFAULT_OUTPUT = PROJECT_ROOT / "workspace" / "releases" / DEMO_RELEASE_NAME
STAGE7_DOCS = PROJECT_ROOT / "docs" / "project" / "m1-5" / "stage7"
STAGE6_OUTPUT = (
    PROJECT_ROOT
    / "workspace"
    / "outputs"
    / "m1-5-stage6-controlled-report-20260901"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )


def build_release(output_root: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    target = output_root.resolve()
    releases_root = (PROJECT_ROOT / "workspace" / "releases").resolve()
    if not target.is_relative_to(releases_root):
        raise ValueError("发布目录必须位于workspace/releases内")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    _copy_tree(PROJECT_ROOT / "src", target / "src")
    _copy_tree(
        PROJECT_ROOT / "resources" / "synthetic" / "full-chain-v1",
        target / "resources" / "synthetic" / "full-chain-v1",
    )
    shutil.copy2(PROJECT_ROOT / "pyproject.toml", target / "pyproject.toml")
    for source_name, target_name in (
        ("install_full_synthetic_demo.ps1", "Install-Demo.ps1"),
        ("start_full_synthetic_demo.ps1", "Start-Demo.ps1"),
        ("backup_full_synthetic_demo.ps1", "Backup-Demo.ps1"),
        ("restore_full_synthetic_demo.ps1", "Restore-Demo.ps1"),
    ):
        shutil.copy2(PROJECT_ROOT / source_name, target / target_name)

    if STAGE7_DOCS.is_dir():
        _copy_tree(STAGE7_DOCS, target / "docs")
    example_root = target / "example-report"
    example_root.mkdir()
    for name in (
        "QRA全合成受控测试报告_v1.html",
        "QRA全合成受控测试报告_v1.pdf",
        "QRA全合成受控测试报告_v1.docx",
        "QRA全合成受控测试报告_v1.zip",
    ):
        source = STAGE6_OUTPUT / name
        if not source.is_file():
            raise FileNotFoundError(f"示例报告不存在，请先完成阶段6：{source}")
        shutil.copy2(source, example_root / name)

    readme = target / "README.md"
    readme.write_text(
        "# QRA全合成端到端演示版_v1\n\n"
        "1. 在 PowerShell 中运行 `./Install-Demo.ps1`。\n"
        "2. 运行 `./Start-Demo.ps1 -OpenBrowser`。\n"
        "3. 项目、11 节点结果和受控测试报告会幂等加载；无需修改数据库或上传JSON。\n\n"
        "本发布包只包含合成数据，仅供软件演示和测试，不得用于真实工程结论。\n",
        encoding="utf-8",
    )

    files = []
    for path in sorted(item for item in target.rglob("*") if item.is_file()):
        relative = path.relative_to(target).as_posix()
        if relative in {"release-manifest.json", "checksums.sha256"}:
            continue
        files.append(
            {
                "path": relative,
                "byte_count": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    manifest = {
        "schema_version": "qra-demo-release-manifest/1.0.0",
        "release_name": DEMO_RELEASE_NAME,
        "release_version": DEMO_RELEASE_VERSION,
        "data_classification": "SYNTHETIC_TEST_ONLY",
        "formal_report_allowed": False,
        "entrypoints": {
            "install": "Install-Demo.ps1",
            "start": "Start-Demo.ps1",
            "backup": "Backup-Demo.ps1",
            "restore": "Restore-Demo.ps1",
        },
        "file_count": len(files),
        "files": files,
    }
    (target / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (target / "checksums.sha256").write_text(
        "".join(f"{row['sha256']}  {row['path']}\n" for row in files),
        encoding="utf-8",
    )

    zip_path = target.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(item for item in target.rglob("*") if item.is_file()):
            archive.write(path, f"{target.name}/{path.relative_to(target).as_posix()}")
    return {
        "status": "PASS",
        "release_name": DEMO_RELEASE_NAME,
        "release_version": DEMO_RELEASE_VERSION,
        "output_root": str(target),
        "zip_path": str(zip_path),
        "zip_sha256": _sha256(zip_path),
        "file_count": len(files),
        "formal_report_allowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build_release(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

