from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from qra_converter.service import convert_sources
from qra_engine.validation import validate_import_contract


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "converter_mvp"
PROFILE_PATH = (
    PROJECT_ROOT
    / "resources"
    / "mappings"
    / "generic"
    / "generic.structured-mvp.v1.json"
)


class ConverterMvpIntegrationTests(unittest.TestCase):
    def test_structured_sources_convert_and_pass_import_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            summary = convert_sources(
                source_dir=SOURCE_DIR,
                profile_path=PROFILE_PATH,
                output_dir=Path(temporary_directory),
                case_id="CONVERTER-MVP-001",
                project_name="结构化转换黄金案例",
                contract_validator=validate_import_contract,
            )
            case = json.loads(Path(summary["paths"]["case"]).read_text(encoding="utf-8"))
            report = json.loads(
                Path(summary["paths"]["conversion_report"]).read_text(encoding="utf-8")
            )
            source_manifest = json.loads(
                Path(summary["paths"]["source_manifest"]).read_text(encoding="utf-8")
            )

        self.assertEqual(summary["status"], "READY_FOR_REVIEW")
        self.assertEqual(report["contract_status"], "PASS")
        self.assertEqual(len(case["segments"]), 2)
        self.assertEqual(case["pipeline"]["operating_temperature_k"], 288.65)
        self.assertEqual(
            case["raw_data_categories"]["cips_dense_interval_potential"]["records"][0][
                "out_of_range_fraction"
            ],
            0.08,
        )
        self.assertEqual(
            [row["segment_id"] for row in case["population_cells"]],
            ["SEG-001", "SEG-002"],
        )
        self.assertEqual(source_manifest["source_count"], 5)
        self.assertGreater(report["summary"]["lineage_count"], 20)
        self.assertFalse(validate_import_contract(case).errors)

    def test_repeated_conversion_has_identical_case_hash(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_summary = convert_sources(
                source_dir=SOURCE_DIR,
                profile_path=PROFILE_PATH,
                output_dir=Path(first),
                case_id="CONVERTER-MVP-001",
                contract_validator=validate_import_contract,
            )
            second_summary = convert_sources(
                source_dir=SOURCE_DIR,
                profile_path=PROFILE_PATH,
                output_dir=Path(second),
                case_id="CONVERTER-MVP-001",
                contract_validator=validate_import_contract,
            )
        self.assertEqual(first_summary["case_sha256"], second_summary["case_sha256"])


if __name__ == "__main__":
    unittest.main()
