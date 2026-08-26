from __future__ import annotations

from typing import Any, Mapping


DERIVED_CATEGORY_DEFINITIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("asset_geometry", "管道与管段几何", ("segments",)),
    ("operation_medium", "运行工况与介质", ("pipeline",)),
    ("failure_frequency", "失效频率与修正模型", ("frequency_library", "frequency_correction_model")),
    ("weather_terrain", "气象与地形", ("weather_joint_probability",)),
    ("population_receptors", "人口与受体", ("population_cells",)),
    ("ignition_congestion", "点火与拥塞", ("ignition_model",)),
    ("consequence_parameters", "后果模型参数", ("standard_formula_test_parameters", "damage_model")),
    ("engineering_indicators", "工程指标观测", ("engineering_indicators",)),
)


def _record_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        records = value.get("records")
        return len(records) if isinstance(records, list) else len(value)
    return 1 if value is not None else 0


def resolve_data_categories(case: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve one authoritative category inventory for preview and reports."""
    manifest = case.get("data_category_manifest")
    if isinstance(manifest, dict) and isinstance(manifest.get("categories"), list) and manifest["categories"]:
        categories = [
            {
                "category_id": str(row["category_id"]),
                "name_zh": str(row.get("name_zh") or row["category_id"]),
                "record_count": int(row.get("record_count", 0)),
            }
            for row in manifest["categories"]
            if isinstance(row, dict) and row.get("category_id")
        ]
        return {
            "definition": "EXPLICIT_DATA_CATEGORY_MANIFEST",
            "category_count": len(categories),
            "categories": categories,
        }

    raw_categories = case.get("raw_data_categories")
    if isinstance(raw_categories, dict) and raw_categories:
        categories = [
            {
                "category_id": str(category_id),
                "name_zh": str(
                    category.get("name_zh") or category_id
                    if isinstance(category, dict)
                    else category_id
                ),
                "record_count": _record_count(category),
            }
            for category_id, category in sorted(raw_categories.items())
        ]
        return {
            "definition": "RAW_DATA_CATEGORY_KEYS",
            "category_count": len(categories),
            "categories": categories,
        }

    categories = []
    for category_id, name_zh, paths in DERIVED_CATEGORY_DEFINITIONS:
        present_paths = [path for path in paths if case.get(path) not in (None, {}, [])]
        if not present_paths:
            continue
        categories.append(
            {
                "category_id": category_id,
                "name_zh": name_zh,
                "record_count": sum(_record_count(case.get(path)) for path in present_paths),
            }
        )
    return {
        "definition": "DERIVED_CANONICAL_INPUT_GROUPS",
        "category_count": len(categories),
        "categories": categories,
    }


__all__ = ["DERIVED_CATEGORY_DEFINITIONS", "resolve_data_categories"]
