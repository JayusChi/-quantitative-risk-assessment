from __future__ import annotations

import math
import unittest

from qra_engine.aqt3046 import (
    adiabatic_pipe_rupture_mass_flow_rate,
    corrected_thermal_fatality_probability,
    corrected_toxic_fatality_probability,
    critical_pressure_ratio,
    flash_fire_fatality_probability,
    fanning_friction_factor_fully_rough,
    gas_orifice_mass_flow_rate,
    gaussian_plume_concentration_kg_m3,
    horizontal_jet_fire_heat_flux_kw_m2,
    horizontal_jet_fire_threshold_distance_m,
    horizontal_jet_flame_length_m,
    logarithmic_atmospheric_transmissivity,
    pasquill_gifford_coefficients,
    probit_to_probability,
    thermal_fatality_probability,
    thermal_radiation_probit,
    tno_explosion_energy_j,
    tno_overpressure_kpa,
    tno_sachs_scaled_distance,
    tno_scaled_overpressure_from_curve,
    toxic_probit,
    vce_fatality_probability,
)


class HumanDamageTests(unittest.TestCase):
    def test_probit_five_is_half_and_probability_is_monotonic(self) -> None:
        self.assertAlmostEqual(probit_to_probability(5.0), 0.5, places=15)
        self.assertLess(probit_to_probability(4.0), probit_to_probability(5.0))
        self.assertLess(probit_to_probability(5.0), probit_to_probability(6.0))

    def test_exposure_time_caps_are_applied(self) -> None:
        self.assertAlmostEqual(
            toxic_probit(1_000.0, 60.0, -20.0, 1.5, 1.0),
            toxic_probit(1_000.0, 30.0, -20.0, 1.5, 1.0),
            places=14,
        )
        self.assertAlmostEqual(
            thermal_radiation_probit(12.5, 60.0),
            thermal_radiation_probit(12.5, 20.0),
            places=14,
        )

    def test_thermal_hard_threshold_and_table_10_corrections(self) -> None:
        self.assertEqual(thermal_fatality_probability(37.5, 1.0), 1.0)
        self.assertEqual(
            thermal_fatality_probability(0.0, 0.0, inside_fire_zone=True),
            1.0,
        )
        outdoor = corrected_thermal_fatality_probability(
            12.5, 20.0, "individual_outdoor"
        )
        societal_outdoor = corrected_thermal_fatality_probability(
            12.5, 20.0, "societal_outdoor"
        )
        societal_indoor = corrected_thermal_fatality_probability(
            12.5, 20.0, "societal_indoor"
        )
        self.assertAlmostEqual(societal_outdoor, 0.14 * outdoor, places=15)
        self.assertEqual(societal_indoor, 0.0)

    def test_toxic_flash_fire_and_vce_rules(self) -> None:
        self.assertAlmostEqual(
            corrected_toxic_fatality_probability(0.6, "societal_indoor"),
            0.06,
            places=15,
        )
        self.assertEqual(
            corrected_toxic_fatality_probability(
                0.6, "societal_indoor", indoor_dose_explicitly_modelled=True
            ),
            0.6,
        )
        self.assertEqual(flash_fire_fatality_probability(True), 1.0)
        self.assertEqual(flash_fire_fatality_probability(False), 0.0)
        self.assertEqual(vce_fatality_probability(30.0, "individual_outdoor"), 1.0)
        self.assertEqual(vce_fatality_probability(10.0, "societal_indoor"), 0.0)
        self.assertEqual(vce_fatality_probability(20.0, "societal_indoor"), 0.025)
        self.assertEqual(vce_fatality_probability(20.0, "societal_outdoor"), 0.0)

    def test_invalid_context_is_rejected_even_at_hard_threshold(self) -> None:
        with self.assertRaisesRegex(ValueError, "未知暴露情景"):
            vce_fatality_probability(30.0, "invalid")  # type: ignore[arg-type]


class SourceAndConsequenceTests(unittest.TestCase):
    GAS_CASE = {
        "upstream_pressure_pa_abs": 5_000_000.0,
        "temperature_k": 288.15,
        "molar_mass_kg_mol": 0.018,
        "gamma": 1.3,
        "discharge_coefficient": 0.8,
        "orifice_diameter_m": 0.02,
    }

    def test_gas_orifice_selects_sonic_and_subsonic_regimes(self) -> None:
        sonic = gas_orifice_mass_flow_rate(
            downstream_pressure_pa_abs=101_325.0,
            **self.GAS_CASE,
        )
        subsonic = gas_orifice_mass_flow_rate(
            downstream_pressure_pa_abs=4_000_000.0,
            **self.GAS_CASE,
        )
        self.assertEqual(sonic.flow_regime, "sonic")
        self.assertEqual(subsonic.flow_regime, "subsonic")
        self.assertAlmostEqual(sonic.critical_pressure_ratio, 0.5457277338, places=10)
        self.assertAlmostEqual(sonic.mass_flow_rate_kg_s, 2.2983508913, places=10)
        self.assertGreater(sonic.mass_flow_rate_kg_s, subsonic.mass_flow_rate_kg_s)

    def test_sonic_mass_flow_scales_with_orifice_area(self) -> None:
        small = gas_orifice_mass_flow_rate(
            downstream_pressure_pa_abs=101_325.0,
            **self.GAS_CASE,
        )
        large_case = dict(self.GAS_CASE)
        large_case["orifice_diameter_m"] = 0.04
        large = gas_orifice_mass_flow_rate(
            downstream_pressure_pa_abs=101_325.0,
            **large_case,
        )
        self.assertAlmostEqual(large.mass_flow_rate_kg_s / small.mass_flow_rate_kg_s, 4.0)

    def test_pasquill_gifford_rural_d_coefficients(self) -> None:
        sigma_y, sigma_z = pasquill_gifford_coefficients(100.0, "D", terrain="rural")
        self.assertAlmostEqual(sigma_y, 0.08 * 100.0 / math.sqrt(1.01), places=14)
        self.assertAlmostEqual(sigma_z, 0.06 * 100.0 / math.sqrt(1.15), places=14)

    def test_gaussian_plume_is_symmetric_and_zero_upwind(self) -> None:
        parameters = {
            "source_mass_rate_kg_s": 1.0,
            "wind_speed_m_s": 3.0,
            "downwind_distance_m": 100.0,
            "receptor_height_m": 1.5,
            "effective_release_height_m": 0.0,
            "stability_class": "D",
            "terrain": "rural",
        }
        positive_y = gaussian_plume_concentration_kg_m3(
            crosswind_distance_m=15.0, **parameters
        )
        negative_y = gaussian_plume_concentration_kg_m3(
            crosswind_distance_m=-15.0, **parameters
        )
        self.assertGreater(positive_y, 0.0)
        self.assertAlmostEqual(positive_y, negative_y, places=15)
        parameters["downwind_distance_m"] = -1.0
        self.assertEqual(
            gaussian_plume_concentration_kg_m3(
                crosswind_distance_m=0.0, **parameters
            ),
            0.0,
        )

    def test_jet_fire_distance_and_flow_behaviour(self) -> None:
        near = horizontal_jet_fire_heat_flux_kw_m2(
            heat_of_combustion_j_kg=50_000_000.0,
            mass_flow_rate_kg_s=1.0,
            radiative_fraction=0.2,
            distance_from_point_source_m=10.0,
        )
        far = horizontal_jet_fire_heat_flux_kw_m2(
            heat_of_combustion_j_kg=50_000_000.0,
            mass_flow_rate_kg_s=1.0,
            radiative_fraction=0.2,
            distance_from_point_source_m=100.0,
        )
        self.assertGreater(near, far)
        self.assertGreater(
            horizontal_jet_flame_length_m(50_000_000.0, 2.0),
            horizontal_jet_flame_length_m(50_000_000.0, 1.0),
        )
        self.assertEqual(logarithmic_atmospheric_transmissivity(1.0), 1.0)

    def test_jet_fire_threshold_distance_inverts_heat_flux(self) -> None:
        distance = horizontal_jet_fire_threshold_distance_m(
            heat_of_combustion_j_kg=50_000_000.0,
            mass_flow_rate_kg_s=10.0,
            radiative_fraction=0.2,
            threshold_heat_flux_kw_m2=37.5,
        )
        actual_flux = horizontal_jet_fire_heat_flux_kw_m2(
            heat_of_combustion_j_kg=50_000_000.0,
            mass_flow_rate_kg_s=10.0,
            radiative_fraction=0.2,
            distance_from_point_source_m=distance,
        )
        self.assertAlmostEqual(actual_flux, 37.5, places=7)

    def test_invalid_source_parameters_are_rejected(self) -> None:
        self.assertAlmostEqual(critical_pressure_ratio(1.3), 0.5457277338, places=10)
        with self.assertRaisesRegex(ValueError, "下游绝对压力"):
            gas_orifice_mass_flow_rate(
                downstream_pressure_pa_abs=5_000_000.0,
                **self.GAS_CASE,
            )
        with self.assertRaisesRegex(ValueError, "crosswind_distance_m"):
            gaussian_plume_concentration_kg_m3(
                source_mass_rate_kg_s=1.0,
                wind_speed_m_s=3.0,
                downwind_distance_m=100.0,
                crosswind_distance_m=math.nan,
                receptor_height_m=1.5,
                effective_release_height_m=0.0,
                stability_class="D",
            )

    def test_adiabatic_pipe_rupture_flow_decreases_with_effective_length(self) -> None:
        friction = fanning_friction_factor_fully_rough(
            inner_diameter_m=0.981,
            absolute_roughness_m=0.000046,
        )
        parameters = {
            "upstream_pressure_pa_abs": 8_101_325.0,
            "upstream_temperature_k": 288.15,
            "molar_mass_kg_mol": 0.018,
            "gamma": 1.3,
            "inner_diameter_m": 0.981,
            "fanning_friction_factor": friction,
        }
        short = adiabatic_pipe_rupture_mass_flow_rate(
            effective_length_m=100.0,
            **parameters,
        )
        long = adiabatic_pipe_rupture_mass_flow_rate(
            effective_length_m=1000.0,
            **parameters,
        )
        self.assertGreater(short.mass_flow_rate_kg_s, long.mass_flow_rate_kg_s)
        self.assertGreater(short.upstream_mach_number, long.upstream_mach_number)
        self.assertGreater(short.choked_pressure_pa_abs, 0.0)

    def test_tno_equations_and_logarithmic_curve_interpolation(self) -> None:
        curve = [(0.1, 1.0), (1.0, 0.1), (10.0, 0.01)]
        self.assertEqual(tno_explosion_energy_j(100.0), 350_000_000.0)
        self.assertAlmostEqual(
            tno_scaled_overpressure_from_curve(math.sqrt(10.0), curve),
            math.sqrt(0.001),
            places=14,
        )
        scaled_near = tno_sachs_scaled_distance(
            distance_m=10.0,
            explosion_energy_j=350_000_000.0,
            ambient_pressure_pa=101_325.0,
        )
        scaled_far = tno_sachs_scaled_distance(
            distance_m=100.0,
            explosion_energy_j=350_000_000.0,
            ambient_pressure_pa=101_325.0,
        )
        self.assertLess(scaled_near, scaled_far)
        self.assertGreater(
            tno_overpressure_kpa(
                distance_m=10.0,
                explosion_source_volume_m3=100.0,
                ambient_pressure_pa=101_325.0,
                curve_points=curve,
            ),
            tno_overpressure_kpa(
                distance_m=100.0,
                explosion_source_volume_m3=100.0,
                ambient_pressure_pa=101_325.0,
                curve_points=curve,
            ),
        )


if __name__ == "__main__":
    unittest.main()
