from __future__ import annotations

import unittest

from qra_converter.fusion.conflicts import build_fusion_groups, decision_is_stale
from qra_converter.fusion.identities import identity_key


def candidate(candidate_id: str, value: float, raw: str) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "field_id": "pipeline.operating_pressure_mpa",
        "entity": {"entity_type": "PIPELINE", "entity_key": "ID:PIPELINE:P-1"},
        "raw_value": raw,
        "normalized_value": value,
        "confidence": 0.9,
        "model_or_rule_versions": {"source_rank": 70},
        "evidence_ids": [f"EVD-{candidate_id}"],
    }


class Stage4FusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fields = {
            "pipeline.operating_pressure_mpa": {
                "conflict_policy": {"blocking": True, "numeric_tolerance": 0.01}
            }
        }

    def test_near_duplicate_and_conflict_are_distinct(self) -> None:
        near_groups, near_issues = build_fusion_groups(
            [candidate("CAND-1", 5.0, "5 MPa"), candidate("CAND-2", 5.005, "5005 kPa")],
            fields=self.fields,
        )
        self.assertEqual(near_groups[0]["group_type"], "NEAR_DUPLICATE")
        self.assertEqual(near_issues, [])

        conflict_groups, conflict_issues = build_fusion_groups(
            [candidate("CAND-1", 5.0, "5 MPa"), candidate("CAND-3", 5.2, "5.2 MPa")],
            fields=self.fields,
        )
        self.assertEqual(conflict_groups[0]["group_type"], "CONFLICT")
        self.assertEqual(set(conflict_groups[0]["candidate_ids"]), {"CAND-1", "CAND-3"})
        self.assertEqual(conflict_issues[0]["code"], "FUSION.VALUE_CONFLICT")
        self.assertTrue(decision_is_stale(conflict_groups[0], {"candidate_set_sha256": "0" * 64}))

    def test_same_chainage_on_different_pipelines_never_matches(self) -> None:
        first, _ = identity_key(
            {
                "entity_type": "SEGMENT",
                "pipeline_id": "PIPE-A",
                "chainage_range": {"start_km": 12.3, "end_km": 13.0},
            }
        )
        second, _ = identity_key(
            {
                "entity_type": "SEGMENT",
                "pipeline_id": "PIPE-B",
                "chainage_range": {"start_km": 12.3, "end_km": 13.0},
            }
        )
        self.assertNotEqual(first, second)

    def test_design_and_operating_pressure_are_different_concepts(self) -> None:
        fields = {
            "pipeline.design_pressure_mpa": {
                "conflict_policy": {"blocking": True, "numeric_tolerance": 0}
            },
            **self.fields,
        }
        design = candidate("CAND-DESIGN", 8.0, "8 MPa")
        design["field_id"] = "pipeline.design_pressure_mpa"
        groups, issues = build_fusion_groups(
            [design, candidate("CAND-OPERATING", 5.0, "5 MPa")],
            fields=fields,
        )
        self.assertNotIn("CONFLICT", {group["group_type"] for group in groups})
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
