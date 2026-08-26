from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA = PROJECT_ROOT / "tests" / "fixtures" / "qra_synthetic_case_v1.json"
TOL = 1e-12


def close(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=0.0, abs_tol=TOL)


def main() -> None:
    case = json.loads(DATA.read_text(encoding="utf-8"))
    errors: list[str] = []

    gas_sum = sum(case["pipeline"]["gas_composition_mole_fraction"].values())
    if not close(gas_sum, 1.0):
        errors.append(f"gas composition sum = {gas_sum}")

    weather_sum = sum(row["probability"] for row in case["weather_joint_probability"])
    if not close(weather_sum, 1.0):
        errors.append(f"weather probability sum = {weather_sum}")

    segment_length = sum(row["length_km"] for row in case["segments"])
    if not close(segment_length, case["pipeline"]["total_length_km"]):
        errors.append(f"segment length sum = {segment_length}")
    segment_ids = {row["segment_id"] for row in case["segments"]}
    expected_segment_ids = {f"SEG-{index:03d}" for index in range(1, 21)}
    if segment_ids != expected_segment_ids:
        errors.append(f"segment IDs = {sorted(segment_ids)}")
    if len(case["segments"]) != 20:
        errors.append(f"segment count = {len(case['segments'])}")
    for path, keyed_data in (
        ("segment_correction_factor", case["segment_correction_factor"]),
        ("standard_formula_test_parameters.gbt34346_annex_c.segments", case["standard_formula_test_parameters"]["gbt34346_annex_c"]["segments"]),
        ("standard_formula_test_parameters.aqt3046_physical_chain.segments", case["standard_formula_test_parameters"]["aqt3046_physical_chain"]["segments"]),
    ):
        if set(keyed_data) != segment_ids:
            errors.append(f"{path} segment coverage mismatch")

    for mechanism, fractions in case["frequency_library"]["loc_fraction_by_mechanism"].items():
        total = sum(fractions.values())
        if not close(total, 1.0):
            errors.append(f"LOC fraction sum for {mechanism} = {total}")

    scenarios = case["mock_adapter_output"]["scenario_outcomes"]
    if {row["segment_id"] for row in scenarios} != segment_ids:
        errors.append("mock scenario segment coverage mismatch")
    pll_by_segment: defaultdict[str, float] = defaultdict(float)
    for row in scenarios:
        pll_by_segment[row["segment_id"]] += row["annual_frequency"] * row["expected_fatalities"]

    pipeline_pll = sum(pll_by_segment.values())
    expected = case["expected_aggregation"]
    if not close(pipeline_pll, expected["pipeline_pll_per_year"]):
        errors.append(f"pipeline PLL = {pipeline_pll}")

    human_ranking = sorted(pll_by_segment, key=pll_by_segment.get, reverse=True)
    if human_ranking != expected["human_pll_ranking"]:
        errors.append(f"human PLL ranking = {human_ranking}")

    for point in expected["fn_curve"]:
        threshold = point["fatalities_at_least"]
        actual = sum(
            row["annual_frequency"]
            for row in scenarios
            if row["expected_fatalities"] >= threshold
        )
        if not close(actual, point["cumulative_frequency_per_year"]):
            errors.append(f"F(N>={threshold}) = {actual}")

    ir_by_receptor: defaultdict[str, float] = defaultdict(float)
    for contributions in case["mock_adapter_output"]["ir_contribution_by_segment_and_receptor"].values():
        for receptor, value in contributions.items():
            ir_by_receptor[receptor] += value

    for receptor, expected_ir in expected["ir_by_receptor_per_year"].items():
        if not close(ir_by_receptor[receptor], expected_ir):
            errors.append(f"IR({receptor}) = {ir_by_receptor[receptor]}")

    max_receptor = max(ir_by_receptor, key=ir_by_receptor.get)
    if max_receptor != expected["max_ir_receptor"]:
        errors.append(f"max IR receptor = {max_receptor}")

    def aggregate(field: str) -> tuple[float, dict[str, float], list[str]]:
        by_segment: defaultdict[str, float] = defaultdict(float)
        for row in scenarios:
            by_segment[row["segment_id"]] += row["annual_frequency"] * row[field]
        ranking = sorted(by_segment, key=by_segment.get, reverse=True)
        return sum(by_segment.values()), dict(by_segment), ranking

    asset_total, asset_by_segment, asset_ranking = aggregate("asset_loss_cny")
    if not close(asset_total, expected["asset"]["pipeline_eal_cny_per_year"]):
        errors.append(f"asset EAL = {asset_total}")
    if asset_ranking != expected["asset"]["ranking"]:
        errors.append(f"asset ranking = {asset_ranking}")

    release_total, _, release_ranking = aggregate("released_mass_kg")
    area_total, _, _ = aggregate("environment_area_m2")
    recovery_total, _, _ = aggregate("environment_recovery_days")
    if not close(release_total, expected["environment"]["pipeline_expected_release_kg_per_year"]):
        errors.append(f"environment expected release = {release_total}")
    if not close(area_total, expected["environment"]["pipeline_expected_area_m2_per_year"]):
        errors.append(f"environment expected area = {area_total}")
    if not close(recovery_total, expected["environment"]["pipeline_expected_recovery_days_per_year"]):
        errors.append(f"environment expected recovery = {recovery_total}")
    if release_ranking != expected["environment"]["release_ranking"]:
        errors.append(f"environment release ranking = {release_ranking}")

    unserved_total, _, service_ranking = aggregate("unserved_volume_m3")
    customer_hours_total, _, _ = aggregate("customer_interruption_hours")
    if not close(unserved_total, expected["service"]["pipeline_expected_unserved_volume_m3_per_year"]):
        errors.append(f"service expected unserved volume = {unserved_total}")
    if not close(customer_hours_total, expected["service"]["pipeline_expected_customer_hours_per_year"]):
        errors.append(f"service expected customer hours = {customer_hours_total}")
    if service_ranking != expected["service"]["unserved_volume_ranking"]:
        errors.append(f"service ranking = {service_ranking}")

    environment_hits = sorted({
        row["segment_id"]
        for row in scenarios
        if row.get("environment_sensitive_receptor_hit")
    })
    critical_customer_hits = sorted({
        row["segment_id"]
        for row in scenarios
        if row.get("critical_customer_hit")
    })
    if environment_hits != expected["environment"]["mandatory_sensitive_receptor_segments"]:
        errors.append(f"environment mandatory segments = {environment_hits}")
    if critical_customer_hits != expected["service"]["critical_customer_segments"]:
        errors.append(f"critical customer segments = {critical_customer_hits}")

    if errors:
        print("FAIL")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)

    print("PASS")
    print(f"- gas composition sum: {gas_sum}")
    print(f"- weather probability sum: {weather_sum}")
    print(f"- segment length sum: {segment_length} km")
    print(f"- segment count: {len(case['segments'])}")
    print(f"- pipeline PLL: {pipeline_pll:.9g} /a")
    print(f"- human top three: {', '.join(human_ranking[:3])}")
    print(f"- asset top three: {', '.join(asset_ranking[:3])}")
    print(f"- environment release top three: {', '.join(release_ranking[:3])}")
    print(f"- service top three: {', '.join(service_ranking[:3])}")
    print(f"- max IR: {max_receptor} = {ir_by_receptor[max_receptor]:.9g} /a")


if __name__ == "__main__":
    main()
