"""Run the synthetic stage-4 immutable-snapshot-to-calculation workflow."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from db_qra.synthetic_stage4 import SyntheticStage4Workflow  # noqa: E402

DEFAULT_STAGE3_OUTPUT = (
    PROJECT_ROOT / "workspace" / "outputs" / "m1-5-stage3-raw-to-snapshot-20260901"
)
DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT / "workspace" / "outputs" / "m1-5-stage4-full-calculation-20260901"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage3-snapshot",
        type=Path,
        default=DEFAULT_STAGE3_OUTPUT / "D00_CLEAN" / "snapshot.json",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_STAGE3_OUTPUT / "stage3-snapshots.sqlite3",
    )
    parser.add_argument("--snapshot-id")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    result = SyntheticStage4Workflow(PROJECT_ROOT).run(
        stage3_snapshot_path=args.stage3_snapshot,
        database_path=args.database,
        output_root=args.output_root,
        snapshot_id=args.snapshot_id,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["summary"]["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
