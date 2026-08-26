from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SYNTHETIC_CASE = PROJECT_ROOT / "tests" / "fixtures" / "qra_synthetic_case_v1.json"


def load_case() -> dict:
    return json.loads(SYNTHETIC_CASE.read_text(encoding="utf-8"))
