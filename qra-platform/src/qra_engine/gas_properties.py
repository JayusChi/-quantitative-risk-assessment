from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping


R_UNIVERSAL_J_MOL_K = 8.31446261815324


@dataclass(frozen=True, slots=True)
class GasComponent:
    molar_mass_kg_mol: float
    heat_capacity_ratio: float
    lower_heating_value_j_mol: float
    lfl_volume_fraction: float | None


@dataclass(frozen=True, slots=True)
class GasMixtureProperties:
    composition_mole_fraction: dict[str, float]
    molar_mass_kg_mol: float
    heat_capacity_ratio: float
    lower_heating_value_j_kg: float
    lfl_volume_fraction: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "property_model_id": "natural_gas.ideal_mixture.lhv.v1",
            "composition_mole_fraction": dict(self.composition_mole_fraction),
            "molar_mass_kg_mol": self.molar_mass_kg_mol,
            "heat_capacity_ratio": self.heat_capacity_ratio,
            "lower_heating_value_j_kg": self.lower_heating_value_j_kg,
            "lfl_volume_fraction": self.lfl_volume_fraction,
            "mixing_rules": {
                "molar_mass": "mole-fraction weighted",
                "heat_capacity": "ideal-gas mole-fraction weighted Cp at approximately 288-298 K",
                "lower_heating_value": "mole-fraction weighted molar LHV divided by mixture molar mass",
                "lfl": "Le Chatelier rule over combustible components",
            },
        }


# Versioned, near-ambient ideal-gas screening properties. Combustion values are
# lower heating values so that one definition is used by every consequence path.
COMPONENTS: dict[str, GasComponent] = {
    "methane": GasComponent(0.016043, 1.305, 802_300.0, 0.050),
    "ethane": GasComponent(0.030070, 1.187, 1_428_000.0, 0.030),
    "propane": GasComponent(0.044097, 1.130, 2_044_000.0, 0.021),
    "n_butane": GasComponent(0.058124, 1.096, 2_657_000.0, 0.018),
    "i_butane": GasComponent(0.058124, 1.096, 2_648_000.0, 0.018),
    "n_pentane": GasComponent(0.072151, 1.072, 3_272_000.0, 0.014),
    "i_pentane": GasComponent(0.072151, 1.072, 3_264_000.0, 0.014),
    "hydrogen": GasComponent(0.002016, 1.405, 241_800.0, 0.040),
    "nitrogen": GasComponent(0.0280134, 1.400, 0.0, None),
    "carbon_dioxide": GasComponent(0.0440095, 1.289, 0.0, None),
    "oxygen": GasComponent(0.031998, 1.395, 0.0, None),
}


def supported_component_names() -> tuple[str, ...]:
    return tuple(sorted(COMPONENTS))


def calculate_gas_mixture_properties(
    composition: Mapping[str, Any],
) -> GasMixtureProperties:
    if not isinstance(composition, Mapping) or not composition:
        raise ValueError("天然气组分不能为空")

    normalized: dict[str, float] = {}
    for raw_name, raw_fraction in composition.items():
        name = str(raw_name).strip().lower()
        if name not in COMPONENTS:
            raise ValueError(
                f"不支持的气体组分：{raw_name}；支持：{', '.join(supported_component_names())}"
            )
        if isinstance(raw_fraction, bool) or not isinstance(raw_fraction, (int, float)):
            raise ValueError(f"气体组分{name}的摩尔分数必须是有限数")
        fraction = float(raw_fraction)
        if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
            raise ValueError(f"气体组分{name}的摩尔分数必须位于[0,1]")
        normalized[name] = normalized.get(name, 0.0) + fraction

    total = math.fsum(normalized.values())
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1.0e-12):
        raise ValueError(f"天然气摩尔组分之和必须为1，当前为{total:.15g}")

    molar_mass = math.fsum(
        fraction * COMPONENTS[name].molar_mass_kg_mol
        for name, fraction in sorted(normalized.items())
    )
    molar_lhv = math.fsum(
        fraction * COMPONENTS[name].lower_heating_value_j_mol
        for name, fraction in sorted(normalized.items())
    )
    lfl_denominator = math.fsum(
        fraction / component.lfl_volume_fraction
        for name, fraction in sorted(normalized.items())
        if (component := COMPONENTS[name]).lfl_volume_fraction is not None
        and component.lower_heating_value_j_mol > 0.0
    )
    if molar_lhv <= 0.0 or lfl_denominator <= 0.0:
        raise ValueError("当前气体组分不具可燃性，不能使用天然气火灾爆炸后果模型")

    mixture_cp = math.fsum(
        fraction
        * (
            COMPONENTS[name].heat_capacity_ratio
            * R_UNIVERSAL_J_MOL_K
            / (COMPONENTS[name].heat_capacity_ratio - 1.0)
        )
        for name, fraction in sorted(normalized.items())
    )
    gamma = mixture_cp / (mixture_cp - R_UNIVERSAL_J_MOL_K)
    return GasMixtureProperties(
        composition_mole_fraction=dict(sorted(normalized.items())),
        molar_mass_kg_mol=molar_mass,
        heat_capacity_ratio=gamma,
        lower_heating_value_j_kg=molar_lhv / molar_mass,
        lfl_volume_fraction=1.0 / lfl_denominator,
    )


def gas_properties_from_case(case: Mapping[str, Any]) -> GasMixtureProperties:
    try:
        composition = case["pipeline"]["gas_composition_mole_fraction"]
    except (KeyError, TypeError) as exc:
        raise ValueError("缺少pipeline.gas_composition_mole_fraction") from exc
    return calculate_gas_mixture_properties(composition)


__all__ = [
    "COMPONENTS",
    "GasComponent",
    "GasMixtureProperties",
    "calculate_gas_mixture_properties",
    "gas_properties_from_case",
    "supported_component_names",
]
