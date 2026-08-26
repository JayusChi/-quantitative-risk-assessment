from __future__ import annotations

import math
import copy
import unittest

from qra_engine.event_tree import calculate_event_tree

from tests.unit.engine.helpers import load_case


class EventTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = load_case()["ignition_model"]

    def test_branch_probabilities_sum_to_one(self) -> None:
        branches = calculate_event_tree(self.model, "medium", 20.0)
        self.assertTrue(
            math.isclose(sum(branch.conditional_probability for branch in branches), 1.0, abs_tol=1e-15)
        )
        self.assertEqual({branch.branch_id for branch in branches}, {"jet_fire", "vce", "flash_fire", "safe_dispersion"})

    def test_release_rate_boundary_uses_upper_interval(self) -> None:
        branches = calculate_event_tree(self.model, "low", 10.0)
        jet = next(branch for branch in branches if branch.branch_id == "jet_fire")
        self.assertEqual(jet.conditional_probability, 0.04)

    def test_low_activity_has_no_vce(self) -> None:
        branches = calculate_event_tree(self.model, "low", 2.0)
        vce = next(branch for branch in branches if branch.branch_id == "vce")
        self.assertEqual(vce.conditional_probability, 0.0)

    def test_delayed_ignition_uses_aqt3046_g1_source_formula(self) -> None:
        model = copy.deepcopy(self.model)
        model["material_reactivity_class"] = "low"
        model["delayed_ignition_sources_by_activity"] = {
            "low": [
                {
                    "presence_probability": 0.5,
                    "ignition_efficiency_per_s": 0.01,
                    "cloud_exposure_time_s": 60.0,
                }
            ]
        }
        model["vce_given_delayed_probability"] = {"low": 0.25}
        branches = calculate_event_tree(model, "low", 2.0)
        immediate = 0.02
        delayed = 0.5 * (1.0 - math.exp(-0.01 * 60.0))
        vce = next(branch for branch in branches if branch.branch_id == "vce")
        flash = next(branch for branch in branches if branch.branch_id == "flash_fire")
        self.assertAlmostEqual(
            vce.conditional_probability + flash.conditional_probability,
            (1.0 - immediate) * delayed,
            places=15,
        )


if __name__ == "__main__":
    unittest.main()
