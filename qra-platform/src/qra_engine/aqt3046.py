from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Literal, Sequence


R_UNIVERSAL_J_MOL_K = 8.31446261815324
ExposureContext = Literal["individual_outdoor", "societal_outdoor", "societal_indoor"]
TerrainType = Literal["rural", "urban"]


def _finite_positive(name: str, value: float, *, allow_zero: bool = False) -> float:
    lower_ok = value >= 0.0 if allow_zero else value > 0.0
    if not math.isfinite(value) or not lower_ok:
        comparator = "非负" if allow_zero else "大于0"
        raise ValueError(f"{name}必须为{comparator}的有限数")
    return float(value)


def _probability(value: float) -> float:
    if math.isnan(value):
        raise ValueError("概率不能为NaN")
    return min(1.0, max(0.0, float(value)))


def _exposure_context(value: ExposureContext) -> ExposureContext:
    if value not in ("individual_outdoor", "societal_outdoor", "societal_indoor"):
        raise ValueError(f"未知暴露情景：{value}")
    return value


def probit_to_probability(probit: float) -> float:
    """AQ/T 3046—2013 式(3)(4)：概率值Pr转换为死亡概率Pd。"""
    if math.isnan(probit):
        raise ValueError("probit不能为NaN")
    return _probability(0.5 * (1.0 + math.erf((probit - 5.0) / math.sqrt(2.0))))


def toxic_probit(
    concentration_mg_m3: float,
    exposure_time_min: float,
    constant_a: float,
    constant_b: float,
    exponent_n: float,
) -> float:
    """AQ/T 3046—2013 式(5)。暴露时间按标准上限截断为30 min。"""
    concentration = _finite_positive("concentration_mg_m3", concentration_mg_m3)
    exposure_time = min(_finite_positive("exposure_time_min", exposure_time_min), 30.0)
    _finite_positive("constant_b", constant_b)
    _finite_positive("exponent_n", exponent_n)
    return float(constant_a) + float(constant_b) * math.log(concentration ** float(exponent_n) * exposure_time)


def toxic_fatality_probability(
    concentration_mg_m3: float,
    exposure_time_min: float,
    constant_a: float,
    constant_b: float,
    exponent_n: float,
) -> float:
    return probit_to_probability(
        toxic_probit(
            concentration_mg_m3,
            exposure_time_min,
            constant_a,
            constant_b,
            exponent_n,
        )
    )


def thermal_radiation_probit(heat_flux_kw_m2: float, exposure_time_s: float) -> float:
    """AQ/T 3046—2013 式(6)。Q按式中要求换算为W/m²，暴露时间最大20 s。"""
    heat_flux_w_m2 = _finite_positive("heat_flux_kw_m2", heat_flux_kw_m2) * 1000.0
    exposure_time = min(_finite_positive("exposure_time_s", exposure_time_s), 20.0)
    return -36.38 + 2.56 * math.log(heat_flux_w_m2 ** (4.0 / 3.0) * exposure_time)


def thermal_fatality_probability(
    heat_flux_kw_m2: float,
    exposure_time_s: float,
    *,
    inside_fire_zone: bool = False,
) -> float:
    """AQ/T 3046—2013 10.3.1、10.3.2。"""
    heat_flux = _finite_positive("heat_flux_kw_m2", heat_flux_kw_m2, allow_zero=True)
    exposure_time = _finite_positive("exposure_time_s", exposure_time_s, allow_zero=True)
    if inside_fire_zone or heat_flux >= 37.5:
        return 1.0
    if heat_flux == 0.0 or exposure_time == 0.0:
        return 0.0
    return probit_to_probability(thermal_radiation_probit(heat_flux, exposure_time))


def corrected_thermal_fatality_probability(
    heat_flux_kw_m2: float,
    exposure_time_s: float,
    context: ExposureContext,
    *,
    inside_fire_zone: bool = False,
) -> float:
    """AQ/T 3046—2013 式(7)(8)及表10的热辐射修正。"""
    context = _exposure_context(context)
    base = thermal_fatality_probability(
        heat_flux_kw_m2,
        exposure_time_s,
        inside_fire_zone=inside_fire_zone,
    )
    if context == "individual_outdoor":
        beta = 1.0
    elif inside_fire_zone or heat_flux_kw_m2 >= 37.5:
        beta = 1.0
    elif context == "societal_outdoor":
        beta = 0.14
    elif context == "societal_indoor":
        beta = 0.0
    return _probability(beta * base)


def corrected_toxic_fatality_probability(
    base_probability: float,
    context: ExposureContext,
    *,
    indoor_dose_explicitly_modelled: bool = False,
) -> float:
    """AQ/T 3046—2013 表10毒性修正；无室内真实剂量时室内取室外的0.1倍。"""
    context = _exposure_context(context)
    base = _probability(base_probability)
    if context in ("individual_outdoor", "societal_outdoor"):
        return base
    if context == "societal_indoor":
        return base if indoor_dose_explicitly_modelled else 0.1 * base
    raise AssertionError("不可达的暴露情景")


def flash_fire_fatality_probability(inside_lfl_flame_envelope: bool) -> float:
    """AQ/T 3046—2013 10.4.1。"""
    return 1.0 if inside_lfl_flame_envelope else 0.0


def vce_fatality_probability(overpressure_kpa: float, context: ExposureContext) -> float:
    """AQ/T 3046—2013 10.4.2及表10注1。"""
    context = _exposure_context(context)
    pressure = _finite_positive("overpressure_kpa", overpressure_kpa, allow_zero=True)
    if pressure >= 30.0:
        return 1.0
    if pressure <= 10.0:
        return 0.0
    if context == "societal_indoor":
        return 0.025
    if context in ("individual_outdoor", "societal_outdoor"):
        return 0.0
    raise AssertionError("不可达的暴露情景")


def circular_orifice_area_m2(orifice_diameter_m: float) -> float:
    diameter = _finite_positive("orifice_diameter_m", orifice_diameter_m)
    return math.pi * diameter * diameter / 4.0


def critical_pressure_ratio(gamma: float) -> float:
    heat_capacity_ratio = _finite_positive("gamma", gamma)
    if heat_capacity_ratio <= 1.0:
        raise ValueError("gamma必须大于1")
    return (2.0 / (heat_capacity_ratio + 1.0)) ** (
        heat_capacity_ratio / (heat_capacity_ratio - 1.0)
    )


def subsonic_expansion_factor(
    downstream_pressure_pa_abs: float,
    upstream_pressure_pa_abs: float,
    gamma: float,
) -> float:
    """AQ/T 3046—2013 式(E.17)。"""
    downstream = _finite_positive("downstream_pressure_pa_abs", downstream_pressure_pa_abs)
    upstream = _finite_positive("upstream_pressure_pa_abs", upstream_pressure_pa_abs)
    heat_capacity_ratio = _finite_positive("gamma", gamma)
    if heat_capacity_ratio <= 1.0:
        raise ValueError("gamma必须大于1")
    if downstream >= upstream:
        raise ValueError("下游绝对压力必须小于上游绝对压力")
    ratio = downstream / upstream
    term_1 = ratio ** (1.0 / heat_capacity_ratio)
    term_2 = math.sqrt(1.0 - ratio ** ((heat_capacity_ratio - 1.0) / heat_capacity_ratio))
    term_3 = math.sqrt(
        (2.0 / (heat_capacity_ratio - 1.0))
        * ((heat_capacity_ratio + 1.0) / 2.0)
        ** ((heat_capacity_ratio + 1.0) / (heat_capacity_ratio - 1.0))
    )
    return term_1 * term_2 * term_3


@dataclass(frozen=True, slots=True)
class GasOrificeResult:
    flow_regime: Literal["sonic", "subsonic"]
    mass_flow_rate_kg_s: float
    pressure_ratio: float
    critical_pressure_ratio: float
    expansion_factor: float
    orifice_area_m2: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AdiabaticPipeRuptureResult:
    mass_flow_rate_kg_s: float
    mass_flux_kg_m2_s: float
    upstream_mach_number: float
    choked_pressure_pa_abs: float
    choked_temperature_k: float
    fanning_friction_factor: float
    effective_length_m: float
    inner_diameter_m: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def gas_orifice_mass_flow_rate(
    *,
    upstream_pressure_pa_abs: float,
    downstream_pressure_pa_abs: float,
    temperature_k: float,
    molar_mass_kg_mol: float,
    gamma: float,
    discharge_coefficient: float,
    orifice_diameter_m: float,
) -> GasOrificeResult:
    """AQ/T 3046—2013 式(E.13)～(E.17)，圆形气体孔口。"""
    upstream = _finite_positive("upstream_pressure_pa_abs", upstream_pressure_pa_abs)
    downstream = _finite_positive("downstream_pressure_pa_abs", downstream_pressure_pa_abs)
    temperature = _finite_positive("temperature_k", temperature_k)
    molar_mass = _finite_positive("molar_mass_kg_mol", molar_mass_kg_mol)
    heat_capacity_ratio = _finite_positive("gamma", gamma)
    coefficient = _finite_positive("discharge_coefficient", discharge_coefficient)
    if coefficient > 1.0:
        raise ValueError("discharge_coefficient不得大于1")
    if heat_capacity_ratio <= 1.0:
        raise ValueError("gamma必须大于1")
    if downstream >= upstream:
        raise ValueError("下游绝对压力必须小于上游绝对压力")

    area = circular_orifice_area_m2(orifice_diameter_m)
    ratio = downstream / upstream
    critical_ratio = critical_pressure_ratio(heat_capacity_ratio)
    choked_core = (
        coefficient
        * area
        * upstream
        * math.sqrt(
            molar_mass
            * heat_capacity_ratio
            / (R_UNIVERSAL_J_MOL_K * temperature)
            * (2.0 / (heat_capacity_ratio + 1.0))
            ** ((heat_capacity_ratio + 1.0) / (heat_capacity_ratio - 1.0))
        )
    )
    if ratio <= critical_ratio:
        return GasOrificeResult("sonic", choked_core, ratio, critical_ratio, 1.0, area)

    expansion_factor = subsonic_expansion_factor(downstream, upstream, heat_capacity_ratio)
    return GasOrificeResult(
        "subsonic",
        expansion_factor * choked_core,
        ratio,
        critical_ratio,
        expansion_factor,
        area,
    )


def fanning_friction_factor_fully_rough(
    *,
    inner_diameter_m: float,
    absolute_roughness_m: float,
) -> float:
    """AQ/T 3046—2013 式(E.8)，充分发展粗糙管湍流的Fanning摩擦因子。"""
    diameter = _finite_positive("inner_diameter_m", inner_diameter_m)
    roughness = _finite_positive("absolute_roughness_m", absolute_roughness_m)
    logarithm = math.log10(3.7 * diameter / roughness)
    if logarithm <= 0.0:
        raise ValueError("absolute_roughness_m必须显著小于inner_diameter_m")
    return 1.0 / (4.0 * logarithm) ** 2


def adiabatic_pipe_rupture_mass_flow_rate(
    *,
    upstream_pressure_pa_abs: float,
    upstream_temperature_k: float,
    molar_mass_kg_mol: float,
    gamma: float,
    inner_diameter_m: float,
    effective_length_m: float,
    fanning_friction_factor: float,
) -> AdiabaticPipeRuptureResult:
    """AQ/T 3046—2013 式(E.18)～(E.22)，单侧长管绝热断裂初始泄放。"""
    pressure = _finite_positive("upstream_pressure_pa_abs", upstream_pressure_pa_abs)
    temperature = _finite_positive("upstream_temperature_k", upstream_temperature_k)
    molar_mass = _finite_positive("molar_mass_kg_mol", molar_mass_kg_mol)
    heat_capacity_ratio = _finite_positive("gamma", gamma)
    diameter = _finite_positive("inner_diameter_m", inner_diameter_m)
    length = _finite_positive("effective_length_m", effective_length_m)
    friction = _finite_positive("fanning_friction_factor", fanning_friction_factor)
    if heat_capacity_ratio <= 1.0:
        raise ValueError("gamma必须大于1")

    friction_term = 4.0 * friction * length / diameter

    def residual(mach: float) -> float:
        y_1 = 1.0 + 0.5 * (heat_capacity_ratio - 1.0) * mach * mach
        return (
            0.5
            * (heat_capacity_ratio + 1.0)
            * math.log(2.0 * y_1 / ((heat_capacity_ratio + 1.0) * mach * mach))
            - heat_capacity_ratio * (1.0 / (mach * mach) - 1.0)
            + heat_capacity_ratio * friction_term
        )

    lower = 1.0e-8
    upper = 1.0
    if residual(lower) >= 0.0 or residual(upper) <= 0.0:
        raise RuntimeError("式(E.18)的马赫数求根区间无有效符号变化")
    for _ in range(240):
        middle = 0.5 * (lower + upper)
        if residual(middle) > 0.0:
            upper = middle
        else:
            lower = middle
        if upper - lower <= 1.0e-13:
            break
    mach = 0.5 * (lower + upper)
    y_1 = 1.0 + 0.5 * (heat_capacity_ratio - 1.0) * mach * mach
    choked_temperature = temperature * 2.0 * y_1 / (heat_capacity_ratio + 1.0)
    choked_pressure = pressure * mach * math.sqrt(
        2.0 * y_1 / (heat_capacity_ratio + 1.0)
    )
    mass_flux = choked_pressure * math.sqrt(
        heat_capacity_ratio
        * molar_mass
        / (R_UNIVERSAL_J_MOL_K * choked_temperature)
    )
    area = math.pi * diameter * diameter / 4.0
    return AdiabaticPipeRuptureResult(
        mass_flow_rate_kg_s=mass_flux * area,
        mass_flux_kg_m2_s=mass_flux,
        upstream_mach_number=mach,
        choked_pressure_pa_abs=choked_pressure,
        choked_temperature_k=choked_temperature,
        fanning_friction_factor=friction,
        effective_length_m=length,
        inner_diameter_m=diameter,
    )


def pasquill_gifford_coefficients(
    downwind_distance_m: float,
    stability_class: str,
    *,
    terrain: TerrainType = "rural",
) -> tuple[float, float]:
    """AQ/T 3046—2013 表E.7的烟羽扩散系数σy、σz。"""
    x = _finite_positive("downwind_distance_m", downwind_distance_m)
    stability = stability_class.upper()
    if stability not in "ABCDEF":
        raise ValueError("stability_class必须是A～F")

    if terrain == "rural":
        sigma_y = {
            "A": 0.22 * x / math.sqrt(1.0 + 0.0001 * x),
            "B": 0.16 * x / math.sqrt(1.0 + 0.0001 * x),
            "C": 0.11 * x / math.sqrt(1.0 + 0.0001 * x),
            "D": 0.08 * x / math.sqrt(1.0 + 0.0001 * x),
            "E": 0.06 * x / math.sqrt(1.0 + 0.0001 * x),
            "F": 0.04 * x / math.sqrt(1.0 + 0.0001 * x),
        }[stability]
        sigma_z = {
            "A": 0.20 * x,
            "B": 0.12 * x,
            "C": 0.08 * x / math.sqrt(1.0 + 0.0002 * x),
            "D": 0.06 * x / math.sqrt(1.0 + 0.0015 * x),
            "E": 0.03 * x / (1.0 + 0.0003 * x),
            "F": 0.016 * x / (1.0 + 0.0003 * x),
        }[stability]
        return sigma_y, sigma_z

    if terrain == "urban":
        if stability in ("A", "B"):
            return (
                0.32 * x / math.sqrt(1.0 + 0.0004 * x),
                0.24 * x * math.sqrt(1.0 + 0.0001 * x),
            )
        if stability == "C":
            return 0.22 * x / math.sqrt(1.0 + 0.0004 * x), 0.20 * x
        if stability == "D":
            return (
                0.16 * x / math.sqrt(1.0 + 0.0004 * x),
                0.14 * x / math.sqrt(1.0 + 0.0003 * x),
            )
        return (
            0.11 * x / math.sqrt(1.0 + 0.0004 * x),
            0.08 * x / math.sqrt(1.0 + 0.0015 * x),
        )
    raise ValueError("terrain必须是rural或urban")


def gaussian_plume_concentration_kg_m3(
    *,
    source_mass_rate_kg_s: float,
    wind_speed_m_s: float,
    downwind_distance_m: float,
    crosswind_distance_m: float,
    receptor_height_m: float,
    effective_release_height_m: float,
    stability_class: str,
    terrain: TerrainType = "rural",
) -> float:
    """AQ/T 3046—2013 式(E.33)，连续稳态源并考虑地面反射。"""
    source_rate = _finite_positive("source_mass_rate_kg_s", source_mass_rate_kg_s, allow_zero=True)
    wind_speed = _finite_positive("wind_speed_m_s", wind_speed_m_s)
    x = float(downwind_distance_m)
    if not math.isfinite(x):
        raise ValueError("downwind_distance_m必须为有限数")
    if x <= 0.0 or source_rate == 0.0:
        return 0.0
    y = float(crosswind_distance_m)
    if not math.isfinite(y):
        raise ValueError("crosswind_distance_m必须为有限数")
    z = _finite_positive("receptor_height_m", receptor_height_m, allow_zero=True)
    release_height = _finite_positive(
        "effective_release_height_m", effective_release_height_m, allow_zero=True
    )
    sigma_y, sigma_z = pasquill_gifford_coefficients(x, stability_class, terrain=terrain)
    lateral = math.exp(-0.5 * (y / sigma_y) ** 2)
    vertical = math.exp(-0.5 * ((z - release_height) / sigma_z) ** 2) + math.exp(
        -0.5 * ((z + release_height) / sigma_z) ** 2
    )
    return source_rate / (2.0 * math.pi * sigma_y * sigma_z * wind_speed) * lateral * vertical


def horizontal_jet_flame_length_m(
    heat_of_combustion_j_kg: float,
    mass_flow_rate_kg_s: float,
) -> float:
    """AQ/T 3046—2013 式(E.57)。"""
    heat = _finite_positive("heat_of_combustion_j_kg", heat_of_combustion_j_kg)
    flow = _finite_positive("mass_flow_rate_kg_s", mass_flow_rate_kg_s)
    return (heat * flow) ** 0.444 / 161.66


def logarithmic_atmospheric_transmissivity(distance_m: float) -> float:
    """AQ/T 3046—2013 式(E.59)，结果限制在物理范围[0,1]。"""
    distance = _finite_positive("distance_m", distance_m)
    return _probability(1.0 - 0.0565 * math.log(distance))


def horizontal_jet_fire_heat_flux_kw_m2(
    *,
    heat_of_combustion_j_kg: float,
    mass_flow_rate_kg_s: float,
    radiative_fraction: float,
    distance_from_point_source_m: float,
) -> float:
    """AQ/T 3046—2013 式(E.58)(E.59)的水平喷射火点源热通量。"""
    heat = _finite_positive("heat_of_combustion_j_kg", heat_of_combustion_j_kg)
    flow = _finite_positive("mass_flow_rate_kg_s", mass_flow_rate_kg_s)
    fraction = _finite_positive("radiative_fraction", radiative_fraction)
    if fraction > 1.0:
        raise ValueError("radiative_fraction不得大于1")
    distance = _finite_positive("distance_from_point_source_m", distance_from_point_source_m)
    transmissivity = logarithmic_atmospheric_transmissivity(distance)
    return fraction * heat * flow * transmissivity / (4.0 * math.pi * distance * distance * 1000.0)


def horizontal_jet_fire_threshold_distance_m(
    *,
    heat_of_combustion_j_kg: float,
    mass_flow_rate_kg_s: float,
    radiative_fraction: float,
    threshold_heat_flux_kw_m2: float,
    relative_tolerance: float = 1.0e-10,
) -> float:
    """反解式(E.58)(E.59)达到给定热通量的距离，采用确定性二分求根。"""
    threshold = _finite_positive("threshold_heat_flux_kw_m2", threshold_heat_flux_kw_m2)
    tolerance = _finite_positive("relative_tolerance", relative_tolerance)

    def residual(distance_m: float) -> float:
        return horizontal_jet_fire_heat_flux_kw_m2(
            heat_of_combustion_j_kg=heat_of_combustion_j_kg,
            mass_flow_rate_kg_s=mass_flow_rate_kg_s,
            radiative_fraction=radiative_fraction,
            distance_from_point_source_m=distance_m,
        ) - threshold

    lower = 1.0e-6
    upper = 1.0
    while residual(upper) > 0.0:
        upper *= 2.0
        if upper > 1.0e7:
            raise RuntimeError("热通量阈值距离求解未收敛")
    for _ in range(200):
        middle = 0.5 * (lower + upper)
        if residual(middle) > 0.0:
            lower = middle
        else:
            upper = middle
        if (upper - lower) / max(upper, 1.0) <= tolerance:
            break
    return 0.5 * (lower + upper)


def tno_explosion_energy_j(explosion_source_volume_m3: float) -> float:
    """AQ/T 3046—2013 式(E.60)。"""
    volume = _finite_positive("explosion_source_volume_m3", explosion_source_volume_m3)
    return volume * 3.5e6


def tno_sachs_scaled_distance(
    *,
    distance_m: float,
    explosion_energy_j: float,
    ambient_pressure_pa: float,
) -> float:
    """AQ/T 3046—2013 式(E.61)。"""
    distance = _finite_positive("distance_m", distance_m)
    energy = _finite_positive("explosion_energy_j", explosion_energy_j)
    pressure = _finite_positive("ambient_pressure_pa", ambient_pressure_pa)
    return distance / (energy / pressure) ** (1.0 / 3.0)


def tno_scaled_overpressure_from_curve(
    scaled_distance: float,
    curve_points: Sequence[tuple[float, float]],
) -> float:
    """在图E.1经批准的数据化曲线上按log-log坐标插值。"""
    distance = _finite_positive("scaled_distance", scaled_distance)
    points = [(float(x), float(y)) for x, y in curve_points]
    if len(points) < 2:
        raise ValueError("TNO曲线至少需要两个数据点")
    for index, (x_value, y_value) in enumerate(points):
        _finite_positive(f"curve_points[{index}].scaled_distance", x_value)
        _finite_positive(f"curve_points[{index}].scaled_overpressure", y_value)
        if index and x_value <= points[index - 1][0]:
            raise ValueError("TNO曲线的比拟距离必须严格递增")
        if index and y_value > points[index - 1][1]:
            raise ValueError("TNO曲线的比拟超压必须单调不增")

    if distance <= points[0][0]:
        return points[0][1]
    pair_index = len(points) - 2
    for index in range(len(points) - 1):
        if distance <= points[index + 1][0]:
            pair_index = index
            break
    x_1, y_1 = points[pair_index]
    x_2, y_2 = points[pair_index + 1]
    fraction = (math.log(distance) - math.log(x_1)) / (math.log(x_2) - math.log(x_1))
    return math.exp(math.log(y_1) + fraction * (math.log(y_2) - math.log(y_1)))


def tno_overpressure_kpa(
    *,
    distance_m: float,
    explosion_source_volume_m3: float,
    ambient_pressure_pa: float,
    curve_points: Sequence[tuple[float, float]],
) -> float:
    """AQ/T 3046—2013 式(E.60)～(E.62)及图E.1数据化曲线。"""
    energy = tno_explosion_energy_j(explosion_source_volume_m3)
    scaled_distance = tno_sachs_scaled_distance(
        distance_m=distance_m,
        explosion_energy_j=energy,
        ambient_pressure_pa=ambient_pressure_pa,
    )
    scaled_overpressure = tno_scaled_overpressure_from_curve(scaled_distance, curve_points)
    return scaled_overpressure * ambient_pressure_pa / 1000.0


__all__ = [
    "AdiabaticPipeRuptureResult",
    "GasOrificeResult",
    "adiabatic_pipe_rupture_mass_flow_rate",
    "corrected_thermal_fatality_probability",
    "corrected_toxic_fatality_probability",
    "critical_pressure_ratio",
    "flash_fire_fatality_probability",
    "fanning_friction_factor_fully_rough",
    "gas_orifice_mass_flow_rate",
    "gaussian_plume_concentration_kg_m3",
    "horizontal_jet_fire_heat_flux_kw_m2",
    "horizontal_jet_fire_threshold_distance_m",
    "horizontal_jet_flame_length_m",
    "logarithmic_atmospheric_transmissivity",
    "pasquill_gifford_coefficients",
    "probit_to_probability",
    "subsonic_expansion_factor",
    "thermal_fatality_probability",
    "thermal_radiation_probit",
    "tno_explosion_energy_j",
    "tno_overpressure_kpa",
    "tno_sachs_scaled_distance",
    "tno_scaled_overpressure_from_curve",
    "toxic_fatality_probability",
    "toxic_probit",
    "vce_fatality_probability",
]
