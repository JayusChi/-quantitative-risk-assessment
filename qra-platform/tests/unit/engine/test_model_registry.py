from __future__ import annotations

import copy
import unittest

from qra_engine.engine import QRAEngine
from qra_engine.errors import ModelNotReadyError
from qra_engine.model_registry import (
    find_model_registration,
    load_model_registry,
    require_released_model,
)

from tests.unit.engine.helpers import load_case


class ModelRegistryTests(unittest.TestCase):
    def test_registry_ids_are_unique_and_specs_exist(self) -> None:
        registrations = load_model_registry()
        self.assertEqual(
            len(registrations),
            len({registration.model_id for registration in registrations}),
        )
        self.assertIsNotNone(find_model_registration("human.aqt3046.pipeline.v1"))

    def test_partial_standard_model_cannot_be_released_silently(self) -> None:
        with self.assertRaisesRegex(
            ModelNotReadyError,
            "IMPLEMENTED_STANDARD_FORMULAS_PROVISIONAL_VALIDATION",
        ):
            require_released_model("human.aqt3046.pipeline.v1")

    def test_unknown_model_cannot_be_used_for_formal_report(self) -> None:
        with self.assertRaisesRegex(ModelNotReadyError, "未登记"):
            require_released_model("human.unknown.v1")

    def test_real_input_flags_do_not_make_test_profile_formal(self) -> None:
        case = copy.deepcopy(load_case())
        case["metadata"]["data_classification"] = "PROJECT_DATA"
        case["frequency_library"]["data_classification"] = "PROJECT_DATA"
        case["damage_model"]["status"] = "PROJECT_PARAMETER_SET"
        case["ignition_model"]["model_status"] = "PROJECT_PARAMETER_SET"
        case["engineering_indicators"]["data_classification"] = "PROJECT_DATA"
        case["frequency_correction_model"]["status"] = "APPROVED_PROJECT_FACTORS"
        result = QRAEngine().run(case, profile="synthetic-chain")
        self.assertTrue(result["validation"]["formal_report_allowed"])
        self.assertFalse(result["run"]["formal_report_allowed"])
        self.assertTrue(
            any("SYNTHETIC_TEST_ONLY" in blocker for blocker in result["run"]["formal_report_blockers"])
        )


if __name__ == "__main__":
    unittest.main()
