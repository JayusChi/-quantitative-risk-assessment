from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Literal


RiskLevel = Literal["LOW_ACCEPTABLE", "MEDIUM_ALARP", "HIGH_UNACCEPTABLE"]


@dataclass(frozen=True, slots=True)
class IndividualRiskCriterion:
    criterion_id: str
    source: str
    acceptable_upper_per_year: float
    unacceptable_lower_per_year: float
    legal_force: str
    applicability_note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


GBT34346_2017_ANNEX_C_IR = IndividualRiskCriterion(
    criterion_id="GBT34346_2017_ANNEX_C_FIG_C2_REFERENCE_IR",
    source="GB/T 34346—2017 附录C 图C.2",
    acceptable_upper_per_year=1.0e-5,
    unacceptable_lower_per_year=1.0e-3,
    legal_force="RECOMMENDED_STANDARD_REFERENCE_VALUE",
    applicability_note=(
        "用于油气管道隐患位置个人风险ALARP判断；图中数值标注为参考值。"
        "项目合同、属地法规或主管部门另有批准准则时，应由版本化准则集替换。"
    ),
)


def classify_individual_risk(
    individual_risk_per_year: float,
    criterion: IndividualRiskCriterion = GBT34346_2017_ANNEX_C_IR,
) -> dict[str, Any]:
    value = float(individual_risk_per_year)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError("individual_risk_per_year必须为非负有限数")
    if value > criterion.unacceptable_lower_per_year:
        level: RiskLevel = "HIGH_UNACCEPTABLE"
        label = "高风险（不可接受）"
        action = "必须降低风险后方可接受"
    elif value <= criterion.acceptable_upper_per_year:
        level = "LOW_ACCEPTABLE"
        label = "低风险（可接受）"
        action = "维持控制并持续监测"
    else:
        level = "MEDIUM_ALARP"
        label = "中风险（ALARP）"
        action = "开展成本效益分析并将风险降至尽可能低"
    return {
        "level": level,
        "label_zh": label,
        "action": action,
        "individual_risk_per_year": value,
        "criterion_id": criterion.criterion_id,
        "acceptable_upper_per_year": criterion.acceptable_upper_per_year,
        "unacceptable_lower_per_year": criterion.unacceptable_lower_per_year,
    }


__all__ = [
    "GBT34346_2017_ANNEX_C_IR",
    "IndividualRiskCriterion",
    "RiskLevel",
    "classify_individual_risk",
]
