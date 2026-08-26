from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class EventBranch:
    branch_id: str
    conditional_probability: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _immediate_probability(ignition_model: dict[str, Any], activity: str, release_rate_kg_s: float) -> float:
    activity_key = ignition_model.get("material_reactivity_class")
    if activity_key is None:
        activity_key = "low" if activity == "low" else "medium_high"
    rows = ignition_model["immediate_ignition_probability"][activity_key]
    for row in rows:
        lower = float(row["release_rate_min_kg_s"])
        upper = row.get("release_rate_max_kg_s")
        if release_rate_kg_s >= lower and (upper is None or release_rate_kg_s < float(upper)):
            return float(row["probability"])
    raise ValueError(f"no immediate ignition probability for release rate {release_rate_kg_s}")


def _delayed_probability(ignition_model: dict[str, Any], activity: str) -> float:
    sources_by_activity = ignition_model.get("delayed_ignition_sources_by_activity")
    if sources_by_activity is None:
        return float(ignition_model["delayed_ignition_test_probability"][activity])

    no_ignition_probability = 1.0
    for source in sources_by_activity[activity]:
        presence = float(source["presence_probability"])
        efficiency = float(source["ignition_efficiency_per_s"])
        exposure_time = float(source["cloud_exposure_time_s"])
        if not 0.0 <= presence <= 1.0:
            raise ValueError("presence_probability必须位于[0,1]")
        if efficiency < 0.0 or exposure_time < 0.0:
            raise ValueError("点火效率和云团暴露时间不得为负")
        source_probability = presence * (1.0 - math.exp(-efficiency * exposure_time))
        no_ignition_probability *= 1.0 - source_probability
    return 1.0 - no_ignition_probability


def _vce_given_delayed_probability(ignition_model: dict[str, Any], activity: str) -> float:
    if "vce_given_delayed_probability" in ignition_model:
        return float(ignition_model["vce_given_delayed_probability"][activity])
    return float(ignition_model["vce_given_delayed_test_probability"][activity])


def calculate_event_tree(
    ignition_model: dict[str, Any],
    activity: str,
    release_rate_kg_s: float,
) -> list[EventBranch]:
    immediate = _immediate_probability(ignition_model, activity, release_rate_kg_s)
    delayed = _delayed_probability(ignition_model, activity)
    vce_given_delayed = _vce_given_delayed_probability(ignition_model, activity)
    for name, probability in (
        ("immediate", immediate),
        ("delayed", delayed),
        ("vce_given_delayed", vce_given_delayed),
    ):
        if not 0.0 <= probability <= 1.0:
            raise ValueError(f"{name} probability must be in [0,1]")

    branches = [
        EventBranch("jet_fire", immediate),
        EventBranch("vce", (1.0 - immediate) * delayed * vce_given_delayed),
        EventBranch("flash_fire", (1.0 - immediate) * delayed * (1.0 - vce_given_delayed)),
        EventBranch("safe_dispersion", (1.0 - immediate) * (1.0 - delayed)),
    ]
    total = sum(branch.conditional_probability for branch in branches)
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ArithmeticError(f"event tree probability sum is {total}")
    return branches
