from __future__ import annotations

import copy
import unittest

from qra_engine import QRAEngine
from qra_engine.errors import InputValidationError
from qra_engine.validation import validate_case

from tests.unit.engine.helpers import load_case


class ValidationTests(unittest.TestCase):
    def test_baseline_is_calculable_but_not_formal(self) -> None:
        report = validate_case(load_case())
        self.assertFalse(report.errors)
        self.assertFalse(report.formal_report_allowed)
        warning_paths = {issue.path for issue in report.warnings}
        self.assertNotIn("assessment.criteria_set_by_domain.human", warning_paths)
        self.assertIn("assessment.criteria_set_by_domain.asset", warning_paths)

    def test_weather_probability_must_be_normalized(self) -> None:
        case = load_case()
        case["weather_joint_probability"][0]["probability"] = 0.2
        report = validate_case(case)
        self.assertIn("WEATHER_PROBABILITY_NOT_NORMALIZED", {issue.code for issue in report.errors})

    def test_loc_fraction_must_be_normalized(self) -> None:
        case = load_case()
        case["frequency_library"]["loc_fraction_by_mechanism"]["external_corrosion"]["rupture"] = 0.15
        report = validate_case(case)
        self.assertIn("LOC_FRACTION_NOT_NORMALIZED", {issue.code for issue in report.errors})

    def test_gas_composition_must_be_normalized(self) -> None:
        case = load_case()
        case["pipeline"]["gas_composition_mole_fraction"]["methane"] = 0.8
        report = validate_case(case)
        self.assertIn("GAS_COMPOSITION_NOT_NORMALIZED", {issue.code for issue in report.errors})

    def test_nonflammable_composition_is_rejected_before_calculation(self) -> None:
        case = load_case()
        case["pipeline"]["gas_composition_mole_fraction"] = {"nitrogen": 1.0}
        report = validate_case(case)
        self.assertIn(
            "GAS_COMPOSITION_UNSUPPORTED", {issue.code for issue in report.errors}
        )
        with self.assertRaises(InputValidationError):
            QRAEngine().run(case)

    def test_synthetic_model_is_blocked_in_strict_run(self) -> None:
        case = copy.deepcopy(load_case())
        case["metadata"]["run_profile"] = "strict_standard"
        report = validate_case(case)
        self.assertIn("SYNTHETIC_MODEL_IN_STRICT_RUN", {issue.code for issue in report.errors})


if __name__ == "__main__":
    unittest.main()
