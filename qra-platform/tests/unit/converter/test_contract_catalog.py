from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from qra_converter.contract_catalog import load_contract_catalog


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = PROJECT_ROOT / "resources" / "contracts" / "part1" / "v1"
INDICATOR_CATALOG = (
    PROJECT_ROOT / "src" / "qra_engine" / "model_specs" / "qra_indicator_catalog_v1.json"
)


class ContractCatalogTests(unittest.TestCase):
    def test_manifest_hashes_version_and_dictionary_are_consistent(self) -> None:
        catalog = load_contract_catalog(
            CONTRACT_ROOT,
            expected_contract_id="qra.part1-input",
            expected_version="1.0.0",
        )
        self.assertEqual(catalog.status, "TEST_EDITION")
        self.assertEqual(len(catalog.manifest_sha256), 64)
        self.assertEqual(
            set(catalog.schemas),
            {
                "qra-input",
                "candidate-field",
                "evidence",
                "quality-issue",
                "review-decision",
                "snapshot-manifest",
            },
        )

    def test_field_and_target_ids_are_unique(self) -> None:
        fields = load_contract_catalog(CONTRACT_ROOT).field_dictionary["fields"]
        field_ids = [row["field_id"] for row in fields]
        target_paths = [row["target_path"] for row in fields]
        self.assertEqual(len(field_ids), len(set(field_ids)))
        self.assertEqual(len(target_paths), len(set(target_paths)))

    def test_all_indicator_catalog_rows_are_registered_without_hardcoded_count(self) -> None:
        source = json.loads(INDICATOR_CATALOG.read_text(encoding="utf-8"))
        source_ids = {
            f"{group['group_id']}.{row[0]}"
            for group in source["groups"]
            for row in group["fields"]
        }
        fields = load_contract_catalog(CONTRACT_ROOT).field_dictionary["fields"]
        registered = {row["indicator_id"] for row in fields if "indicator_id" in row}
        self.assertEqual(len(source["groups"]), 17)
        self.assertEqual(registered, source_ids)
        self.assertEqual(len(registered), sum(len(group["fields"]) for group in source["groups"]))

    def test_tampered_file_is_rejected_without_version_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copy_root = Path(temporary) / "v1"
            shutil.copytree(CONTRACT_ROOT, copy_root)
            path = copy_root / "term_aliases.json"
            path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "哈希不匹配"):
                load_contract_catalog(copy_root)

    def test_manifest_path_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copy_root = Path(temporary) / "v1"
            shutil.copytree(CONTRACT_ROOT, copy_root)
            manifest_path = copy_root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files_sha256"]["../outside.json"] = "0" * 64
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "路径越界"):
                load_contract_catalog(copy_root)


if __name__ == "__main__":
    unittest.main()
