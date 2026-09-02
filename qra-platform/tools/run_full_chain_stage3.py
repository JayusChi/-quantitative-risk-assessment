"""Run stage-3 raw-source-to-immutable-snapshot acceptance scenarios."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from db_qra.ocr_settings import (  # noqa: E402
    OcrSettingsStore,
    environment_extraction_configured,
    load_ocr_settings_into_process,
)
from db_qra.stage3_adapter import persist_confirmed_snapshot  # noqa: E402
from qra_converter.synthetic_stage3 import SyntheticStage3Workflow  # noqa: E402

DEFAULT_LOCAL_SETTINGS = PROJECT_ROOT / "workspace" / "state" / "ocr-settings.json"


def _load_online_settings(settings_path: Path) -> dict[str, object]:
    """Load the DPAPI-protected local settings without exposing credentials."""

    if environment_extraction_configured():
        return {
            "configured": True,
            "source": os.environ.get("QRA_OCR_SETTINGS_SOURCE", "environment"),
            "model_version": os.environ.get("QRA_EXTRACTION_MODEL_VERSION"),
        }
    loaded = load_ocr_settings_into_process(
        OcrSettingsStore(settings_path.resolve()),
        overwrite_environment=True,
    )
    return {
        "configured": loaded is not None and environment_extraction_configured(),
        "source": "encrypted-store" if loaded is not None else "none",
        "model_version": (
            os.environ.get("QRA_EXTRACTION_MODEL_VERSION") if loaded is not None else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--condition",
        default="D00_CLEAN",
        choices=(
            "D00_CLEAN",
            "D10_CONFLICT",
            "D20_MISSING",
            "D30_LOW_QUALITY_SCAN",
            "D40_OVERSIZED_IMAGE",
            "D50_PROMPT_INJECTION",
        ),
    )
    parser.add_argument("--provider", choices=("deterministic", "online"), default="deterministic")
    parser.add_argument(
        "--allow-external-sharing",
        action="store_true",
        help="显式授权把合成解析块发送给已配置的外部信息提取模型",
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument(
        "--settings",
        type=Path,
        default=DEFAULT_LOCAL_SETTINGS,
        help="本机DPAPI加密的百炼设置文件；仅在线模式读取",
    )
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--accept-conflict", choices=("BASE", "OVERLAY"))
    args = parser.parse_args()
    output_dir = args.output_dir or (
        PROJECT_ROOT
        / "workspace"
        / "outputs"
        / "m1-5-stage3-raw-to-snapshot-20260901"
        / args.condition
    )
    decisions = (
        {"pipeline.operating_pressure_mpa": args.accept_conflict} if args.accept_conflict else None
    )
    online_configuration = (
        _load_online_settings(args.settings)
        if args.provider == "online"
        else {"configured": False, "source": "not-requested", "model_version": None}
    )
    result = SyntheticStage3Workflow(
        PROJECT_ROOT,
        snapshot_persister=persist_confirmed_snapshot,
    ).run(
        condition_id=args.condition,
        provider_mode=args.provider,
        decisions=decisions,
        confirm=args.confirm,
        output_dir=output_dir.resolve(),
        database_path=args.database.resolve() if args.database else None,
        allow_external_sharing=args.allow_external_sharing,
    )
    summary = {
        "condition": args.condition,
        "gate": result["gate"],
        "coverage": result["coverage_report"],
        "golden_diff": result["golden_diff"],
        "snapshot_persistence": result["snapshot_persistence"],
        "online_configuration": online_configuration,
        "online_demo": result["online_demo"],
        "output_dir": str(output_dir.resolve()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if args.provider == "online":
        online_accepted = (
            result["online_demo"]["status"] == "COMPLETED_REVIEW_REQUIRED"
            and result["gate"]["blocking_issue_count"] == 0
            and result["gate"]["unresolved_review_count"] == 1
        )
        return 0 if online_accepted else 2
    return 0 if result["gate"]["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
