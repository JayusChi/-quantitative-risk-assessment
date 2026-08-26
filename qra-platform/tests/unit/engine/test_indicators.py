from __future__ import annotations

import copy
import math
import unittest

from qra_engine.frequency import calculate_loc_frequencies
from qra_engine.frequency_correction import resolve_segment_correction_factors
from qra_engine.indicators import (
    build_indicator_coverage,
    load_indicator_catalog,
    validate_indicator_set,
)

from tests.unit.engine.helpers import load_case


class EngineeringIndicatorTests(unittest.TestCase):
    def test_catalog_is_unique_and_contains_full_registered_set(self) -> None:
        catalog = load_indicator_catalog()
        ids = [definition.indicator_id for definition in catalog.definitions]
        self.assertEqual(len(ids), 246)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(
            sum(definition.requirement == "REQUIRED" for definition in catalog.definitions),
            62,
        )

    def test_twenty_segment_case_covers_all_required_observation_slots(self) -> None:
        coverage = build_indicator_coverage(load_case())
        self.assertEqual(coverage["required_coverage_fraction"], 1.0)
        self.assertEqual(coverage["missing_required_indicator_ids"], [])
        self.assertEqual(coverage["indicator_definition_count"], 246)

    def test_unknown_explicit_indicator_is_rejected(self) -> None:
        case = copy.deepcopy(load_case())
        case["engineering_indicators"]["observations_global"]["unknown.field"] = {
            "value": 1.0,
            "quality": "C",
            "as_of": "2026-08-05",
            "source_ref": "synthetic://unknown",
        }
        issues, _ = validate_indicator_set(case)
        self.assertIn("INDICATOR_ID_UNKNOWN", {issue.code for issue in issues})


class FrequencyCorrectionTests(unittest.TestCase):
    def test_provided_factors_preserve_existing_frequency_result(self) -> None:
        case = load_case()
        resolution = resolve_segment_correction_factors(case)
        self.assertEqual(
            resolution.factors_by_segment,
            case["segment_correction_factor"],
        )
        rows = calculate_loc_frequencies(case, resolution)
        self.assertEqual(len(rows), 20 * 4)

    def test_log_linear_model_consumes_segment_indicator_values(self) -> None:
        case = copy.deepcopy(load_case())
        mechanism_models = {}
        for mechanism in case["frequency_library"]["base_frequency_by_mechanism"]:
            mechanism_models[mechanism] = {
                "intercept": 0.0,
                "minimum_factor": 0.01,
                "maximum_factor": 100.0,
                "terms": [
                    {
                        "indicator_id": "third_party.excavation_events_per_km_year",
                        "coefficient": 0.1,
                        "reference": 0.0,
                        "scale": 1.0,
                    },
                    {
                        "indicator_id": "geometry_material.wall_thickness_mm",
                        "coefficient": 0.0,
                        "reference": 0.0,
                        "scale": 1.0,
                    }
                ],
            }
        case["frequency_correction_model"] = {
            "model_id": "frequency.correction.test_log_linear.v1",
            "version": "1.0.0",
            "status": "SYNTHETIC_TEST_ONLY",
            "model_type": "log_linear_calibrated",
            "mechanisms": mechanism_models,
        }
        resolution = resolve_segment_correction_factors(case)
        rural = resolution.factors_by_segment["SEG-001"]["third_party_damage"]
        dense = resolution.factors_by_segment["SEG-011"]["third_party_damage"]
        self.assertTrue(math.isclose(rural, math.exp(0.02), rel_tol=1e-12))
        self.assertTrue(math.isclose(dense, math.exp(0.3), rel_tol=1e-12))
        self.assertGreater(dense, rural)
        trace = resolution.diagnostics["term_trace"]["SEG-011"][
            "third_party_damage"
        ]
        self.assertEqual(trace["terms"][0]["value"], 3.0)
        self.assertEqual(
            trace["terms"][0]["indicator_id"],
            "third_party.excavation_events_per_km_year",
        )
        self.assertEqual(trace["terms"][1]["value"], 17.5)


if __name__ == "__main__":
    unittest.main()
