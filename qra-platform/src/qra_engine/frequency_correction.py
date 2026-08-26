from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .indicators import IndicatorCatalog, load_indicator_catalog, resolve_indicator_value


APPROVED_STATUSES = {"APPROVED_CALIBRATED", "APPROVED_PROJECT_FACTORS"}


@dataclass(frozen=True, slots=True)
class CorrectionFactorResolution:
    factors_by_segment: dict[str, dict[str, float]]
    diagnostics: dict[str, Any]


def _observation_value(
    case: dict[str, Any],
    segment_id: str,
    indicator_id: str,
    catalog: IndicatorCatalog,
) -> float:
    try:
        value = resolve_indicator_value(
            case,
            indicator_id,
            segment_id=segment_id,
            catalog=catalog,
        )
    except (KeyError, ValueError) as exc:
        raise ValueError(
            f"{segment_id}缺少修正模型输入指标{indicator_id}"
        ) from exc
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{segment_id}.{indicator_id}必须为有限数值")
    return float(value)


def _log_linear_factors(
    case: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    mechanisms = config.get("mechanisms", {})
    catalog = load_indicator_catalog()
    expected_mechanisms = set(
        case["frequency_library"]["base_frequency_by_mechanism"]
    )
    if set(mechanisms) != expected_mechanisms:
        raise ValueError("log_linear_calibrated修正模型必须覆盖全部失效机理")
    factors_by_segment: dict[str, dict[str, float]] = {}
    term_trace: dict[str, Any] = {}
    for segment in case["segments"]:
        segment_id = segment["segment_id"]
        segment_factors: dict[str, float] = {}
        segment_trace: dict[str, Any] = {}
        for mechanism, model in mechanisms.items():
            score = float(model.get("intercept", 0.0))
            terms_trace: list[dict[str, float | str]] = []
            for term in model.get("terms", []):
                indicator_id = term["indicator_id"]
                value = _observation_value(
                    case,
                    segment_id,
                    indicator_id,
                    catalog,
                )
                reference = float(term.get("reference", 0.0))
                scale = float(term.get("scale", 1.0))
                coefficient = float(term["coefficient"])
                if scale == 0.0:
                    raise ValueError(f"{mechanism}.{indicator_id}的scale不能为0")
                normalized = (value - reference) / scale
                contribution = coefficient * normalized
                score += contribution
                terms_trace.append(
                    {
                        "indicator_id": indicator_id,
                        "value": value,
                        "normalized_value": normalized,
                        "coefficient": coefficient,
                        "log_factor_contribution": contribution,
                    }
                )
            raw_factor = math.exp(score)
            minimum = float(model.get("minimum_factor", 0.01))
            maximum = float(model.get("maximum_factor", 100.0))
            if minimum <= 0.0 or maximum < minimum:
                raise ValueError(f"{mechanism}修正因子边界无效")
            factor = min(max(raw_factor, minimum), maximum)
            segment_factors[mechanism] = factor
            segment_trace[mechanism] = {
                "log_factor": score,
                "raw_factor": raw_factor,
                "bounded_factor": factor,
                "terms": terms_trace,
            }
        factors_by_segment[segment_id] = segment_factors
        term_trace[segment_id] = segment_trace
    return factors_by_segment, term_trace


def resolve_segment_correction_factors(
    case: dict[str, Any],
) -> CorrectionFactorResolution:
    config = case.get(
        "frequency_correction_model",
        {
            "model_id": "frequency.correction.legacy_provided.v1",
            "version": "1.0.0",
            "status": "UNVERSIONED_INPUT",
            "model_type": "provided_factors",
        },
    )
    model_type = config.get("model_type")
    if model_type == "provided_factors":
        factors = {
            segment_id: {
                mechanism: float(value)
                for mechanism, value in mechanism_values.items()
            }
            for segment_id, mechanism_values in case[
                "segment_correction_factor"
            ].items()
        }
        term_trace = None
    elif model_type == "log_linear_calibrated":
        factors, term_trace = _log_linear_factors(case, config)
    else:
        raise ValueError(f"unsupported frequency correction model type: {model_type}")

    status = config.get("status", "UNSPECIFIED")
    blockers: list[str] = []
    if status not in APPROVED_STATUSES:
        blockers.append(
            "失效频率修正模型或其系数未标记为APPROVED_CALIBRATED/APPROVED_PROJECT_FACTORS"
        )
    if case.get("engineering_indicators", {}).get("data_classification") == "SYNTHETIC_TEST_ONLY":
        blockers.append("工程指标观测为合成测试数据")
    diagnostics = {
        "model_id": config.get("model_id"),
        "version": config.get("version"),
        "status": status,
        "model_type": model_type,
        "source": config.get("source", "SY/T 6891.2-2020 Annex A formula A.1"),
        "approved_for_formal_qra": not blockers,
        "formal_release_blockers": blockers,
        "factors_by_segment": factors,
        "term_trace": term_trace,
        "method_note": (
            "SY/T 6891.2附录A定义K_j(a1,a2,...)但不提供统一权重。"
            "log_linear_calibrated只是受支持的校准函数形式，系数必须由适用失效数据拟合并批准。"
        ),
    }
    return CorrectionFactorResolution(factors, diagnostics)


__all__ = [
    "APPROVED_STATUSES",
    "CorrectionFactorResolution",
    "resolve_segment_correction_factors",
]
