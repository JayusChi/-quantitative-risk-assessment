from __future__ import annotations

from pathlib import Path

import pytest

from qra_converter.contract_catalog import load_contract_catalog
from qra_converter.orchestration.workflow import (
    DEFAULT_AUTO_FIELD_LIMIT,
    Stage4Workflow,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = PROJECT_ROOT / "resources" / "contracts" / "part1" / "v1"


def _all_field_names(workflow: Stage4Workflow) -> str:
    return "\n".join(
        str(definition["name_zh"])
        for definition in workflow.fields.values()
        if definition.get("name_zh")
    )


def test_automatic_online_field_routing_supports_256_fields() -> None:
    workflow = Stage4Workflow(catalog=load_contract_catalog(CONTRACT_ROOT))
    selected = workflow._relevant_field_subset(
        ({"text": _all_field_names(workflow)},), [], None
    )
    assert DEFAULT_AUTO_FIELD_LIMIT == 256
    assert len(selected) == 256
    assert selected == tuple(sorted(selected))
    assert set(selected) <= set(workflow.fields)


def test_automatic_online_field_limit_is_configurable_and_validated() -> None:
    catalog = load_contract_catalog(CONTRACT_ROOT)
    workflow = Stage4Workflow(catalog=catalog, auto_field_limit=17)
    selected = workflow._relevant_field_subset(
        ({"text": _all_field_names(workflow)},), [], None
    )
    assert len(selected) == 17

    with pytest.raises(ValueError, match="字段字典总数"):
        Stage4Workflow(catalog=catalog, auto_field_limit=281)
