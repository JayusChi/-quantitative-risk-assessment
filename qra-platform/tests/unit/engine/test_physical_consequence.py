from __future__ import annotations

import copy
import unittest

from qra_engine import QRAEngine
from qra_engine.consequence import ScenarioContext
from qra_engine.physical_consequence import AQT3046PipelineConsequenceAdapter

from tests.unit.engine.helpers import load_case


class PhysicalConsequenceAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.case = load_case()
        cls.adapter = AQT3046PipelineConsequenceAdapter(cls.case)

    def test_standard_release_models_replace_fixed_test_rates(self) -> None:
        small = self.adapter.release_rate_kg_s(
            "small_5mm", segment_id="SEG-010", leak_chainage_km=4.0
        )
        medium = self.adapter.release_rate_kg_s(
            "medium_25mm", segment_id="SEG-010", leak_chainage_km=4.0
        )
        large = self.adapter.release_rate_kg_s(
            "large_100mm", segment_id="SEG-010", leak_chainage_km=4.0
        )
        rupture_centre = self.adapter.release_rate_kg_s(
            "rupture", segment_id="SEG-010", leak_chainage_km=3.95
        )
        rupture_near_valve = self.adapter.release_rate_kg_s(
            "rupture", segment_id="SEG-010", leak_chainage_km=3.81
        )
        self.assertAlmostEqual(medium / small, 25.0, places=12)
        self.assertAlmostEqual(large / medium, 16.0, places=12)
        self.assertGreater(rupture_centre, large)
        self.assertNotAlmostEqual(rupture_centre, rupture_near_valve, places=6)

    def test_gas_composition_drives_authoritative_properties_and_source_term(self) -> None:
        methane_case = copy.deepcopy(self.case)
        methane_case["pipeline"]["gas_composition_mole_fraction"] = {
            "methane": 1.0
        }
        methane_adapter = AQT3046PipelineConsequenceAdapter(methane_case)
        self.assertNotEqual(
            self.adapter.molar_mass_kg_mol, methane_adapter.molar_mass_kg_mol
        )
        self.assertNotEqual(
            self.adapter.heat_of_combustion_j_kg,
            methane_adapter.heat_of_combustion_j_kg,
        )
        baseline_release = self.adapter.release_rate_kg_s(
            "large_100mm", segment_id="SEG-010", leak_chainage_km=4.0
        )
        methane_release = methane_adapter.release_rate_kg_s(
            "large_100mm", segment_id="SEG-010", leak_chainage_km=4.0
        )
        self.assertNotAlmostEqual(baseline_release, methane_release, places=6)
        diagnostics = self.adapter.model_diagnostics()["gas_mixture_properties"]
        self.assertEqual(
            diagnostics["property_model_id"], "natural_gas.ideal_mixture.lhv.v1"
        )

    def test_fire_dispersion_explosion_and_damage_branches_return_physical_metrics(self) -> None:
        scenarios = {
            "jet_fire": ScenarioContext(
                segment_id="SEG-014",
                loc_id="large_100mm",
                branch_id="jet_fire",
                leak_x_m=5400.0,
                leak_y_m=0.0,
                leak_chainage_km=5.4,
                release_rate_kg_s=116.3730470915159,
                stability_class="D",
                wind_speed_m_s=3.0,
                wind_direction_from="W",
            ),
            "flash_fire": ScenarioContext(
                segment_id="SEG-010",
                loc_id="large_100mm",
                branch_id="flash_fire",
                leak_x_m=4000.0,
                leak_y_m=0.0,
                leak_chainage_km=4.0,
                release_rate_kg_s=116.3730470915159,
                stability_class="D",
                wind_speed_m_s=3.0,
                wind_direction_from="W",
            ),
            "vce": ScenarioContext(
                segment_id="SEG-014",
                loc_id="large_100mm",
                branch_id="vce",
                leak_x_m=5400.0,
                leak_y_m=0.0,
                leak_chainage_km=5.4,
                release_rate_kg_s=116.3730470915159,
                stability_class="D",
                wind_speed_m_s=3.0,
                wind_direction_from="W",
            ),
        }
        jet = self.adapter.evaluate(
            scenarios["jet_fire"],
            [5500.0, 0.0],
            exposure_context="individual_outdoor",
        )
        flash = self.adapter.evaluate(
            scenarios["flash_fire"],
            [4200.0, 0.0],
            exposure_context="individual_outdoor",
        )
        vce = self.adapter.evaluate(
            scenarios["vce"],
            [5450.0, 0.0],
            exposure_context="individual_outdoor",
        )
        self.assertEqual(jet.effect_metric, "thermal_radiation")
        self.assertEqual(flash.effect_metric, "flammable_gas_concentration")
        self.assertEqual(vce.effect_metric, "peak_overpressure")
        self.assertIn("AQ/T 3046", jet.details["model"])
        self.assertIn("AQ/T 3046", flash.details["model"])
        self.assertIn("AQ/T 3046", vce.details["model"])
        for effect in (jet, flash, vce):
            self.assertGreaterEqual(effect.fatality_probability, 0.0)
            self.assertLessEqual(effect.fatality_probability, 1.0)


class PhysicalChainIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = QRAEngine().run(load_case())

    def test_default_profile_runs_registered_standard_formula_chain(self) -> None:
        self.assertEqual(
            self.result["run"]["calculation_profile"],
            "aqt3046-physical",
        )
        self.assertEqual(
            self.result["model_trace"]["human_consequence_model_id"],
            "human.aqt3046.pipeline.v1",
        )
        self.assertNotEqual(
            self.result["human_risk"]["consequence_model"]["status"],
            "SYNTHETIC_TEST_ONLY",
        )
        diagnostics = self.result["calculation_diagnostics"]
        self.assertLess(abs(diagnostics["frequency_balance_error"]), 1.0e-12)
        self.assertIn("physical_consequence_model", diagnostics)
        self.assertIn(
            "rupture",
            diagnostics["physical_consequence_model"]["release_rate_summary"],
        )
        ranking = self.result["human_risk"]["segment_risk"]["ranking"]
        self.assertEqual(len(ranking), 20)
        self.assertEqual([row["segment_id"] for row in ranking[:3]], [
            "SEG-012",
            "SEG-011",
            "SEG-013",
        ])


if __name__ == "__main__":
    unittest.main()
