from __future__ import annotations

import json
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE5_ROOT = PROJECT_ROOT / "resources" / "synthetic" / "full-chain-v1" / "stage5"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class Stage5AcceptanceRecordTest(unittest.TestCase):
    def test_stage5_user_journey_gate_record_passes(self) -> None:
        profile = read_json(STAGE5_ROOT / "acceptance-profile.json")
        record = read_json(STAGE5_ROOT / "stage5-acceptance.json")
        self.assertEqual(record["gate"], "S5_USER_JOURNEY_ACCEPTED")
        self.assertEqual(record["status"], "PASS")
        self.assertEqual(record["check_count"], profile["expected_check_count"])
        self.assertEqual(record["passed_count"], profile["expected_check_count"])
        self.assertTrue(all(row["status"] == "PASS" for row in record["checks"]))
        self.assertEqual(
            record["completed_node_count"], profile["expected_completed_node_count"]
        )
        self.assertFalse(record["formal_report_allowed"])


    def test_stage5_acceptance_covers_every_required_slice(self) -> None:
        record = read_json(STAGE5_ROOT / "stage5-acceptance.json")
        check_ids = {row["check_id"] for row in record["checks"]}
        self.assertEqual(
            {
                "S5-01_PROJECT_AGGREGATION",
                "S5-02_FULL_SYNTHETIC_DEMO_LOADER",
                "S5-03_SIX_STEP_GUIDED_JOURNEY",
                "S5-04_STATUS_AND_11_NODE_PROGRESS",
                "S5-05_ORDINARY_UI_HIDES_JSON_AND_DATABASE",
                "S5-06_ADVANCED_AUDIT_DISCLOSURE",
                "S5-07_SYNTHETIC_MARKER_AND_FORMAL_GATE",
                "S5-08_RETRY_AND_CONTINUE_STATE",
                "S5-09_DESKTOP_AND_NARROW_RESPONSIVE",
                "S5-10_KEYBOARD_ERRORS_AND_PROJECT_BACKLINK",
                "END_TO_END_REPORT_RETURN_NAVIGATION",
            },
            check_ids,
        )


if __name__ == "__main__":
    unittest.main()
