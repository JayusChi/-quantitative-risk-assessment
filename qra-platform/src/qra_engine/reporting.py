from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from copy import deepcopy
from html import escape
from pathlib import Path
from typing import Any, Callable, Iterable


REPORT_STYLE_ID = "pipeline-qra-reference-blue-v1"

DEFAULT_MATRIX_CRITERIA: dict[str, Any] = {
    "criteria_id": "QRA_DISPLAY_MATRIX_V1",
    "status": "DISPLAY_ONLY_NOT_ACCEPTANCE_CRITERION",
    "likelihood_metric": "segment_initiating_failure_frequency_per_year",
    "likelihood_upper_bounds_per_year": [1.0e-5, 3.0e-5, 8.0e-5, 2.0e-4],
    "consequence_metric": "equivalent_fatalities_per_initiating_failure",
    "consequence_upper_bounds_fatalities": [1.0, 5.0, 20.0, 100.0],
    "likelihood_labels": {
        "1": "极低",
        "2": "较低",
        "3": "中等",
        "4": "较高",
        "5": "高",
    },
    "consequence_labels": {
        "A": "<1人",
        "B": "1-5人",
        "C": "5-20人",
        "D": "20-100人",
        "E": ">=100人",
    },
    "note": (
        "该5x5矩阵只用于结果展示、筛选和报告排版，不替代PLL、个人风险IR、F-N曲线，"
        "也不构成项目风险接受准则。正式项目必须用经批准的阈值覆盖本配置。"
    ),
}

DEFAULT_CHART_IDS = (
    "frequency_composition",
    "segment_pll_ranking",
    "risk_matrix",
    "route_profile",
    "individual_risk",
    "fn_curve",
    "priority_bubble",
)

CHART_FILENAMES = {
    "frequency_composition": "01_管段失效频率构成.svg",
    "segment_pll_ranking": "02_管段PLL排序.svg",
    "risk_matrix": "03_风险矩阵.svg",
    "route_profile": "04_沿线风险剖面.svg",
    "individual_risk": "05_个人风险.svg",
    "fn_curve": "06_FN曲线.svg",
    "priority_bubble": "07_融合优先级.svg",
}

COLORS = {
    "navy": "#173F67",
    "blue": "#2F75B5",
    "cyan": "#5B9BD5",
    "pale": "#DDEBF7",
    "grid": "#D9E2F3",
    "text": "#243447",
    "muted": "#66788A",
    "low": "#F7F9FC",
    "medium": "#FFE699",
    "medium_high": "#F4B183",
    "high": "#E66B78",
    "green": "#70AD47",
}

MECHANISM_LABELS = {
    "external_corrosion": "外腐蚀",
    "third_party_damage": "第三方损伤",
    "internal_corrosion": "内腐蚀",
    "stress_corrosion_cracking": "应力腐蚀",
    "manufacturing_construction": "制造/施工",
    "natural_geohazard": "地质灾害",
    "misoperation": "误操作",
}

MECHANISM_COLORS = {
    "external_corrosion": "#4472C4",
    "third_party_damage": "#ED7D31",
    "internal_corrosion": "#A5A5A5",
    "stress_corrosion_cracking": "#FFC000",
    "manufacturing_construction": "#5B9BD5",
    "natural_geohazard": "#70AD47",
    "misoperation": "#8064A2",
}

RISK_BAND_LABELS = {
    "LOW": "低",
    "MEDIUM": "中",
    "MEDIUM_HIGH": "中高",
    "HIGH": "高",
}

RISK_BAND_COLORS = {
    "LOW": COLORS["low"],
    "MEDIUM": COLORS["medium"],
    "MEDIUM_HIGH": COLORS["medium_high"],
    "HIGH": COLORS["high"],
}


def _require_spatial_result(result: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    human = result.get("human_risk")
    if not isinstance(human, dict) or "segment_risk" not in human:
        raise ValueError("所选计算结果不包含管段空间QRA结果，无法生成风险矩阵和参考图表")
    ranking = human["segment_risk"].get("ranking")
    if not isinstance(ranking, list) or not ranking:
        raise ValueError("管段风险排序为空，无法生成报告输出")
    return human, ranking


def _grade(value: float, upper_bounds: Iterable[float]) -> int:
    grade = 1
    for upper in upper_bounds:
        if value < float(upper):
            return grade
        grade += 1
    return grade


def _consequence_letter(grade: int) -> str:
    return chr(ord("A") + grade - 1)


def _risk_band(likelihood_grade: int, consequence_grade: int) -> str:
    # 仅复刻参考报告的展示色带；不是QRA接受准则。
    grid = {
        1: ("LOW", "LOW", "MEDIUM", "MEDIUM", "MEDIUM_HIGH"),
        2: ("LOW", "LOW", "MEDIUM", "MEDIUM", "MEDIUM_HIGH"),
        3: ("LOW", "LOW", "MEDIUM", "MEDIUM_HIGH", "HIGH"),
        4: ("MEDIUM", "MEDIUM", "MEDIUM_HIGH", "MEDIUM_HIGH", "HIGH"),
        5: ("MEDIUM_HIGH", "MEDIUM_HIGH", "MEDIUM_HIGH", "HIGH", "HIGH"),
    }
    return grid[likelihood_grade][consequence_grade - 1]


def build_risk_matrix(
    result: dict[str, Any], criteria: dict[str, Any] | None = None
) -> dict[str, Any]:
    human, ranking = _require_spatial_result(result)
    resolved = deepcopy(DEFAULT_MATRIX_CRITERIA)
    if criteria:
        resolved.update(criteria)
    likelihood_bounds = resolved["likelihood_upper_bounds_per_year"]
    consequence_bounds = resolved["consequence_upper_bounds_fatalities"]
    if len(likelihood_bounds) != 4 or len(consequence_bounds) != 4:
        raise ValueError("5x5风险矩阵必须分别提供4个可能性阈值和4个后果阈值")

    segments: list[dict[str, Any]] = []
    cell_segments: defaultdict[tuple[int, str], list[str]] = defaultdict(list)
    band_counts: defaultdict[str, int] = defaultdict(int)
    for row in ranking:
        likelihood = float(row.get("initiating_failure_frequency_per_year") or 0.0)
        maximum = row.get("maximum_conditional_consequence") or {}
        maximum_consequence = float(maximum.get("expected_fatalities") or 0.0)
        pll = float(row["risk_value_fatalities_per_year"])
        consequence = pll / likelihood if likelihood > 0.0 else 0.0
        likelihood_grade = _grade(likelihood, likelihood_bounds)
        consequence_grade_number = _grade(consequence, consequence_bounds)
        consequence_grade = _consequence_letter(consequence_grade_number)
        band = _risk_band(likelihood_grade, consequence_grade_number)
        cell_segments[(likelihood_grade, consequence_grade)].append(str(row["segment_id"]))
        band_counts[band] += 1
        segments.append(
            {
                "segment_id": row["segment_id"],
                "start_km": row["start_km"],
                "end_km": row["end_km"],
                "initiating_failure_frequency_per_year": likelihood,
                "likelihood_grade": likelihood_grade,
                "equivalent_fatalities_per_initiating_failure": consequence,
                "maximum_conditional_expected_fatalities": maximum_consequence,
                "consequence_grade": consequence_grade,
                "display_risk_band": band,
                "display_risk_band_zh": RISK_BAND_LABELS[band],
                "pll_per_year": pll,
                "maximum_individual_risk_per_year": row.get(
                    "maximum_segment_individual_risk_per_year"
                ),
                "authoritative_ir_level": row["risk_level"]["level"],
                "authoritative_ir_label_zh": row["risk_level"]["label_zh"],
            }
        )

    cells = []
    for likelihood_grade in range(1, 6):
        for consequence_grade_number in range(1, 6):
            consequence_grade = _consequence_letter(consequence_grade_number)
            band = _risk_band(likelihood_grade, consequence_grade_number)
            cells.append(
                {
                    "likelihood_grade": likelihood_grade,
                    "consequence_grade": consequence_grade,
                    "display_risk_band": band,
                    "display_risk_band_zh": RISK_BAND_LABELS[band],
                    "segment_ids": cell_segments[(likelihood_grade, consequence_grade)],
                }
            )

    segments.sort(key=lambda row: (row["start_km"], row["segment_id"]))
    return {
        "schema_version": "1.0.0",
        "matrix_type": "QRA_RESULT_DISPLAY_MATRIX",
        "criteria": resolved,
        "authoritative_risk_metrics": {
            "segment_risk_value": "PLL = sum(f_s * N_s)",
            "matrix_consequence": "N_q = segment_PLL / segment_initiating_failure_frequency",
            "individual_risk": "IR = sum(f_s * P_fatality,s)",
            "societal_risk": "F-N curve",
            "judgement_status": human.get("judgement_status"),
        },
        "summary": {
            "segment_count": len(segments),
            "band_counts": dict(sorted(band_counts.items())),
        },
        "cells": cells,
        "segments": segments,
    }


def write_risk_matrix_files(matrix: dict[str, Any], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "risk_matrix.json"
    csv_path = output_dir / "risk_matrix.csv"
    json_path.write_text(
        json.dumps(matrix, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    fieldnames = [
        "segment_id",
        "start_km",
        "end_km",
        "initiating_failure_frequency_per_year",
        "likelihood_grade",
        "equivalent_fatalities_per_initiating_failure",
        "maximum_conditional_expected_fatalities",
        "consequence_grade",
        "display_risk_band",
        "display_risk_band_zh",
        "pll_per_year",
        "maximum_individual_risk_per_year",
        "authoritative_ir_level",
        "authoritative_ir_label_zh",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(matrix["segments"])
    return [json_path, csv_path]


def _svg_start(title: str, subtitle: str, width: int, height: int) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>text{font-family:'Microsoft YaHei','SimHei',Arial,sans-serif;fill:#243447} .small{font-size:14px}.axis{font-size:15px}.label{font-size:16px}.title{font-size:26px;font-weight:700}.subtitle{font-size:14px;fill:#66788A}</style>",
        f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>',
        f'<text x="50" y="48" class="title">{escape(title)}</text>',
        f'<text x="50" y="76" class="subtitle">{escape(subtitle)}</text>',
    ]


def _write_svg(path: Path, parts: list[str]) -> Path:
    parts.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return path


def _format_scientific(value: float) -> str:
    if value == 0.0:
        return "0"
    return f"{value:.2e}"


def _segment_rows_by_chainage(result: dict[str, Any]) -> list[dict[str, Any]]:
    _, ranking = _require_spatial_result(result)
    return sorted(ranking, key=lambda row: (float(row["start_km"]), str(row["segment_id"])))


def _chart_frequency_composition(
    result: dict[str, Any], matrix: dict[str, Any], path: Path
) -> Path:
    rows = _segment_rows_by_chainage(result)
    diagnostics = result.get("calculation_diagnostics", {})
    by_segment: defaultdict[str, defaultdict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    for loc in diagnostics.get("loc_frequency", []):
        for mechanism, value in (loc.get("mechanism_contribution") or {}).items():
            by_segment[str(loc["segment_id"])][mechanism] += float(value)
    mechanisms = [key for key in MECHANISM_LABELS if any(key in values for values in by_segment.values())]
    width, height = 1500, 800
    parts = _svg_start(
        "各管段失效频率及威胁构成",
        "对应参考报告的堆叠柱图；柱高为管段起始失效频率，颜色表示失效机理贡献。",
        width,
        height,
    )
    left, top, plot_w, plot_h = 90, 160, 1330, 500
    maximum = max((sum(by_segment[str(row["segment_id"])].values()) for row in rows), default=1.0)
    maximum = maximum or 1.0
    for tick in range(6):
        y = top + plot_h - tick * plot_h / 5
        value = maximum * tick / 5
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left+plot_w}" y2="{y:.1f}" stroke="{COLORS["grid"]}"/>')
        parts.append(f'<text x="{left-12}" y="{y+5:.1f}" text-anchor="end" class="small">{escape(_format_scientific(value))}</text>')
    slot = plot_w / max(len(rows), 1)
    bar_w = slot * 0.68
    for index, row in enumerate(rows):
        segment_id = str(row["segment_id"])
        x = left + index * slot + (slot - bar_w) / 2
        cursor = top + plot_h
        for mechanism in mechanisms:
            value = by_segment[segment_id].get(mechanism, 0.0)
            rect_h = value / maximum * plot_h
            cursor -= rect_h
            parts.append(
                f'<rect x="{x:.1f}" y="{cursor:.1f}" width="{bar_w:.1f}" height="{rect_h:.1f}" fill="{MECHANISM_COLORS[mechanism]}"/>'
            )
        parts.append(f'<text x="{x+bar_w/2:.1f}" y="{top+plot_h+24}" text-anchor="end" class="small" transform="rotate(-48 {x+bar_w/2:.1f} {top+plot_h+24})">{escape(segment_id)}</text>')
    legend_x, legend_y = 90, 112
    for mechanism in mechanisms:
        parts.append(f'<rect x="{legend_x}" y="{legend_y}" width="16" height="16" fill="{MECHANISM_COLORS[mechanism]}"/>')
        parts.append(f'<text x="{legend_x+22}" y="{legend_y+14}" class="small">{escape(MECHANISM_LABELS[mechanism])}</text>')
        legend_x += 145
    parts.append(f'<text x="22" y="{top+plot_h/2}" class="axis" transform="rotate(-90 22 {top+plot_h/2})">年失效频率（1/年）</text>')
    parts.append(f'<text x="{left+plot_w/2}" y="755" text-anchor="middle" class="axis">管段（按里程顺序）</text>')
    return _write_svg(path, parts)


def _chart_segment_pll_ranking(
    result: dict[str, Any], matrix: dict[str, Any], path: Path
) -> Path:
    _, ranking = _require_spatial_result(result)
    ranking = sorted(ranking, key=lambda row: int(row["risk_value_rank"]))
    width, height = 1250, max(780, 150 + len(ranking) * 30)
    parts = _svg_start(
        "管段定量风险值（PLL）排序",
        "PLL = sum(场景频率 x 场景死亡人数)，是当前管段风险排序的主指标。",
        width,
        height,
    )
    left, top, plot_w = 220, 120, 900
    row_h = 27
    maximum = max(float(row["risk_value_fatalities_per_year"]) for row in ranking) or 1.0
    band_by_segment = {row["segment_id"]: row["display_risk_band"] for row in matrix["segments"]}
    for index, row in enumerate(ranking):
        y = top + index * row_h
        value = float(row["risk_value_fatalities_per_year"])
        bar_w = value / maximum * plot_w
        color = RISK_BAND_COLORS[band_by_segment[str(row["segment_id"])]]
        parts.append(f'<text x="{left-12}" y="{y+18}" text-anchor="end" class="small">{escape(str(row["segment_id"]))}</text>')
        parts.append(f'<rect x="{left}" y="{y+3}" width="{bar_w:.1f}" height="20" rx="3" fill="{color}" stroke="#AAB7C4"/>')
        parts.append(f'<text x="{left+bar_w+8:.1f}" y="{y+18}" class="small">{escape(_format_scientific(value))}</text>')
    footer_y = top + len(ranking) * row_h + 35
    parts.append(f'<text x="{left}" y="{footer_y}" class="subtitle">颜色仅对应展示矩阵；风险接受判断以IR准则及批准的F-N准则为准。</text>')
    return _write_svg(path, parts)


def _chart_risk_matrix(result: dict[str, Any], matrix: dict[str, Any], path: Path) -> Path:
    width, height = 1080, 930
    parts = _svg_start(
        f'{matrix["summary"]["segment_count"]}个管段QRA结果展示矩阵',
        "纵轴为起始失效频率等级，横轴为等效后果人数N_q；频率 x N_q = 管段PLL。",
        width,
        height,
    )
    left, top, cell_w, cell_h = 170, 135, 155, 125
    cell_lookup = {(row["likelihood_grade"], row["consequence_grade"]): row for row in matrix["cells"]}
    for display_row, likelihood_grade in enumerate(range(5, 0, -1)):
        y = top + display_row * cell_h
        parts.append(f'<text x="{left-25}" y="{y+cell_h/2+5}" text-anchor="end" class="label">{likelihood_grade}</text>')
        for consequence_number in range(1, 6):
            consequence_grade = _consequence_letter(consequence_number)
            x = left + (consequence_number - 1) * cell_w
            cell = cell_lookup[(likelihood_grade, consequence_grade)]
            fill = RISK_BAND_COLORS[cell["display_risk_band"]]
            parts.append(f'<rect x="{x}" y="{y}" width="{cell_w}" height="{cell_h}" fill="{fill}" stroke="#8EA9C1"/>')
            segments = cell["segment_ids"]
            if not segments:
                lines = ["-"]
            else:
                shown = segments[:8]
                lines = [", ".join(shown[i:i+2]) for i in range(0, len(shown), 2)]
                if len(segments) > len(shown):
                    lines.append(f"另{len(segments)-len(shown)}段")
            start_y = y + cell_h / 2 - (len(lines) - 1) * 9
            for line_index, line in enumerate(lines[:5]):
                parts.append(f'<text x="{x+cell_w/2}" y="{start_y+line_index*18}" text-anchor="middle" class="small">{escape(line)}</text>')
    for consequence_number in range(1, 6):
        grade = _consequence_letter(consequence_number)
        x = left + (consequence_number - 0.5) * cell_w
        label = matrix["criteria"]["consequence_labels"].get(grade, "")
        parts.append(f'<text x="{x}" y="{top+5*cell_h+30}" text-anchor="middle" class="label">{grade}</text>')
        parts.append(f'<text x="{x}" y="{top+5*cell_h+51}" text-anchor="middle" class="small">{escape(label)}</text>')
    parts.append(f'<text x="32" y="{top+2.5*cell_h}" class="axis" transform="rotate(-90 32 {top+2.5*cell_h})">失效可能性等级</text>')
    parts.append(f'<text x="{left+2.5*cell_w}" y="{top+5*cell_h+82}" text-anchor="middle" class="axis">后果等级（等效后果人数 N_q = PLL/频率）</text>')
    legend_x = 190
    for band in ("LOW", "MEDIUM", "MEDIUM_HIGH", "HIGH"):
        parts.append(f'<rect x="{legend_x}" y="855" width="22" height="18" fill="{RISK_BAND_COLORS[band]}" stroke="#8EA9C1"/>')
        parts.append(f'<text x="{legend_x+29}" y="870" class="small">{RISK_BAND_LABELS[band]}</text>')
        legend_x += 180
    return _write_svg(path, parts)


def _chart_route_profile(result: dict[str, Any], matrix: dict[str, Any], path: Path) -> Path:
    rows = _segment_rows_by_chainage(result)
    matrix_by_segment = {str(row["segment_id"]): row for row in matrix["segments"]}
    width, height = 1400, 850
    parts = _svg_start(
        "沿线风险等级与PLL剖面",
        "参考报告的沿里程耦合图：上部为展示矩阵色带，下部为管段PLL定量曲线。",
        width,
        height,
    )
    left, plot_w = 100, 1220
    top1, h1, top2, h2 = 130, 190, 410, 300
    start = min(float(row["start_km"]) for row in rows)
    end = max(float(row["end_km"]) for row in rows)
    span = end - start or 1.0
    band_value = {"LOW": 1, "MEDIUM": 2, "MEDIUM_HIGH": 3, "HIGH": 4}
    for level in range(1, 5):
        y = top1 + h1 - level * h1 / 4
        parts.append(f'<line x1="{left}" y1="{y}" x2="{left+plot_w}" y2="{y}" stroke="{COLORS["grid"]}"/>')
        parts.append(f'<text x="{left-15}" y="{y+5}" text-anchor="end" class="small">{level}</text>')
    for row in rows:
        segment_id = str(row["segment_id"])
        x1 = left + (float(row["start_km"]) - start) / span * plot_w
        x2 = left + (float(row["end_km"]) - start) / span * plot_w
        band = matrix_by_segment[segment_id]["display_risk_band"]
        level = band_value[band]
        y = top1 + h1 - level * h1 / 4
        parts.append(f'<rect x="{x1}" y="{y}" width="{max(x2-x1,2)}" height="{top1+h1-y}" fill="{RISK_BAND_COLORS[band]}" stroke="#FFFFFF"/>')
        parts.append(f'<text x="{(x1+x2)/2}" y="{top1+h1+22}" text-anchor="middle" class="small" transform="rotate(-45 {(x1+x2)/2} {top1+h1+22})">{escape(segment_id)}</text>')
    maximum_pll = max(float(row["risk_value_fatalities_per_year"]) for row in rows) or 1.0
    points = []
    for row in rows:
        mid = (float(row["start_km"]) + float(row["end_km"])) / 2
        x = left + (mid - start) / span * plot_w
        y = top2 + h2 - float(row["risk_value_fatalities_per_year"]) / maximum_pll * h2
        points.append((x, y, str(row["segment_id"])))
    for tick in range(6):
        y = top2 + h2 - tick * h2 / 5
        value = maximum_pll * tick / 5
        parts.append(f'<line x1="{left}" y1="{y}" x2="{left+plot_w}" y2="{y}" stroke="{COLORS["grid"]}"/>')
        parts.append(f'<text x="{left-12}" y="{y+5}" text-anchor="end" class="small">{escape(_format_scientific(value))}</text>')
    if points:
        parts.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x,y,_ in points)}" fill="none" stroke="{COLORS["blue"]}" stroke-width="3"/>')
    for x, y, label in points:
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{COLORS["navy"]}"/>')
        parts.append(f'<text x="{x:.1f}" y="{y-10:.1f}" text-anchor="middle" class="small">{escape(label)}</text>')
    parts.append(f'<text x="25" y="{top1+h1/2}" transform="rotate(-90 25 {top1+h1/2})" class="axis">展示等级</text>')
    parts.append(f'<text x="25" y="{top2+h2/2}" transform="rotate(-90 25 {top2+h2/2})" class="axis">PLL（人/年）</text>')
    parts.append(f'<text x="{left+plot_w/2}" y="805" text-anchor="middle" class="axis">相对里程（km）</text>')
    return _write_svg(path, parts)


def _chart_individual_risk(result: dict[str, Any], matrix: dict[str, Any], path: Path) -> Path:
    human, _ = _require_spatial_result(result)
    ir = human["individual_risk"]
    values = ir.get("by_population_cell") or ir.get("by_receptor") or {}
    ordered = sorted(((str(key), float(value)) for key, value in values.items()), key=lambda row: -row[1])
    width, height = 1250, max(650, 210 + len(ordered) * 45)
    parts = _svg_start(
        "人口单元个人风险IR",
        "对数坐标；标出GB/T 34346-2017附录C图C.2参考值1e-5/年与1e-3/年。",
        width,
        height,
    )
    left, top, plot_w = 210, 140, 900
    min_log, max_log = -8.0, -2.0
    row_h = 42
    for exponent in range(-8, -1):
        x = left + (exponent - min_log) / (max_log - min_log) * plot_w
        parts.append(f'<line x1="{x}" y1="{top-15}" x2="{x}" y2="{top+row_h*len(ordered)}" stroke="{COLORS["grid"]}"/>')
        parts.append(f'<text x="{x}" y="{top-25}" text-anchor="middle" class="small">1e{exponent}</text>')
    for index, (cell_id, value) in enumerate(ordered):
        y = top + index * row_h
        log_value = max(min_log, min(max_log, math.log10(max(value, 10**min_log))))
        bar_w = (log_value - min_log) / (max_log - min_log) * plot_w
        parts.append(f'<text x="{left-12}" y="{y+24}" text-anchor="end" class="label">{escape(cell_id)}</text>')
        parts.append(f'<rect x="{left}" y="{y+6}" width="{bar_w}" height="25" rx="3" fill="{COLORS["cyan"]}"/>')
        parts.append(f'<text x="{left+bar_w+8}" y="{y+24}" class="small">{escape(_format_scientific(value))}</text>')
    for criterion, label, color in ((1e-5, "可接受参考值", COLORS["green"]), (1e-3, "不可接受参考值", COLORS["high"])):
        x = left + (math.log10(criterion) - min_log) / (max_log - min_log) * plot_w
        parts.append(f'<line x1="{x}" y1="{top-20}" x2="{x}" y2="{top+row_h*len(ordered)}" stroke="{color}" stroke-width="3" stroke-dasharray="8,6"/>')
        parts.append(f'<text x="{x+5}" y="{top+row_h*len(ordered)+25}" class="small">{escape(label)}</text>')
    return _write_svg(path, parts)


def _chart_fn_curve(result: dict[str, Any], matrix: dict[str, Any], path: Path) -> Path:
    human, _ = _require_spatial_result(result)
    curve = human["societal_risk"].get("fn_curve", [])
    width, height = 1050, 800
    parts = _svg_start(
        "社会风险F-N曲线",
        "当前仅计算，不自动判级；须配置经项目批准的F-N接受曲线后才能作合规判断。",
        width,
        height,
    )
    left, top, plot_w, plot_h = 130, 130, 800, 540
    positive = [(float(row["fatalities_at_least"]), float(row["cumulative_frequency_per_year"])) for row in curve if float(row["fatalities_at_least"]) > 0 and float(row["cumulative_frequency_per_year"]) > 0]
    if positive:
        min_x = math.floor(math.log10(min(x for x, _ in positive)))
        max_x = math.ceil(math.log10(max(x for x, _ in positive)))
        if max_x == min_x:
            max_x += 1
        min_y = math.floor(math.log10(min(y for _, y in positive))) - 1
        max_y = math.ceil(math.log10(max(y for _, y in positive)))
        if max_y == min_y:
            max_y += 1
        for exponent in range(min_x, max_x + 1):
            x = left + (exponent - min_x) / (max_x - min_x) * plot_w
            parts.append(f'<line x1="{x}" y1="{top}" x2="{x}" y2="{top+plot_h}" stroke="{COLORS["grid"]}"/>')
            parts.append(f'<text x="{x}" y="{top+plot_h+28}" text-anchor="middle" class="small">1e{exponent}</text>')
        for exponent in range(min_y, max_y + 1):
            y = top + plot_h - (exponent - min_y) / (max_y - min_y) * plot_h
            parts.append(f'<line x1="{left}" y1="{y}" x2="{left+plot_w}" y2="{y}" stroke="{COLORS["grid"]}"/>')
            parts.append(f'<text x="{left-15}" y="{y+5}" text-anchor="end" class="small">1e{exponent}</text>')
        points = []
        for fatalities, frequency in positive:
            x = left + (math.log10(fatalities) - min_x) / (max_x - min_x) * plot_w
            y = top + plot_h - (math.log10(frequency) - min_y) / (max_y - min_y) * plot_h
            points.append((x, y))
        parts.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x,y in points)}" fill="none" stroke="{COLORS["blue"]}" stroke-width="4"/>')
        for x, y in points:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{COLORS["navy"]}"/>')
    parts.append(f'<text x="{left+plot_w/2}" y="740" text-anchor="middle" class="axis">死亡人数N（人，对数）</text>')
    parts.append(f'<text x="35" y="{top+plot_h/2}" transform="rotate(-90 35 {top+plot_h/2})" class="axis">F(N)（1/年，对数）</text>')
    return _write_svg(path, parts)


def _chart_priority_bubble(result: dict[str, Any], matrix: dict[str, Any], path: Path) -> Path:
    rows = matrix["segments"]
    width, height = 1250, 820
    parts = _svg_start(
        "管段融合优先级气泡图",
        "横轴为最大IR，纵轴为等效后果等级，气泡大小表示起始失效频率；每级仅标注PLL最高的3段。",
        width,
        height,
    )
    left, top, plot_w, plot_h = 130, 130, 980, 560
    min_log, max_log = -8.0, -2.0
    for exponent in range(-8, -1):
        x = left + (exponent - min_log) / (max_log - min_log) * plot_w
        parts.append(f'<line x1="{x}" y1="{top}" x2="{x}" y2="{top+plot_h}" stroke="{COLORS["grid"]}"/>')
        parts.append(f'<text x="{x}" y="{top+plot_h+28}" text-anchor="middle" class="small">1e{exponent}</text>')
    for grade in range(1, 6):
        y = top + plot_h - (grade - 0.5) / 5 * plot_h
        parts.append(f'<line x1="{left}" y1="{y}" x2="{left+plot_w}" y2="{y}" stroke="{COLORS["grid"]}"/>')
        parts.append(f'<text x="{left-20}" y="{y+5}" text-anchor="end" class="label">{_consequence_letter(grade)}</text>')
    max_frequency = max(float(row["initiating_failure_frequency_per_year"]) for row in rows) or 1.0
    labelled_by_grade: dict[str, list[str]] = {}
    for grade in ("A", "B", "C", "D", "E"):
        group = sorted(
            (row for row in rows if row["consequence_grade"] == grade),
            key=lambda row: (-float(row["pll_per_year"]), str(row["segment_id"])),
        )
        labelled_by_grade[grade] = [str(row["segment_id"]) for row in group[:3]]
    for row in rows:
        ir = float(row["maximum_individual_risk_per_year"])
        x = left + (max(min_log, min(max_log, math.log10(max(ir, 10**min_log)))) - min_log) / (max_log - min_log) * plot_w
        grade = ord(str(row["consequence_grade"])) - ord("A") + 1
        y = top + plot_h - (grade - 0.5) / 5 * plot_h
        radius = 7 + 12 * math.sqrt(float(row["initiating_failure_frequency_per_year"]) / max_frequency)
        color = RISK_BAND_COLORS[str(row["display_risk_band"])]
        segment_id = str(row["segment_id"])
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{color}" fill-opacity="0.58" stroke="{COLORS["navy"]}"><title>{escape(segment_id)} | PLL={escape(_format_scientific(float(row["pll_per_year"])))} | IR={escape(_format_scientific(ir))}</title></circle>')
        if segment_id in labelled_by_grade[str(row["consequence_grade"])]:
            label_index = labelled_by_grade[str(row["consequence_grade"])].index(segment_id)
            label_y = y + (label_index - 1) * 22
            label_x = x + radius + 10
            parts.append(f'<line x1="{x+radius:.1f}" y1="{y:.1f}" x2="{label_x-3:.1f}" y2="{label_y:.1f}" stroke="{COLORS["muted"]}"/>')
            parts.append(f'<text x="{label_x:.1f}" y="{label_y+5:.1f}" class="small">{escape(segment_id)}</text>')
    parts.append(f'<text x="{left+plot_w/2}" y="765" text-anchor="middle" class="axis">最大管段个人风险IR（1/年，对数）</text>')
    parts.append(f'<text x="35" y="{top+plot_h/2}" transform="rotate(-90 35 {top+plot_h/2})" class="axis">等效后果等级 N_q</text>')
    return _write_svg(path, parts)


CHART_RENDERERS: dict[str, Callable[[dict[str, Any], dict[str, Any], Path], Path]] = {
    "frequency_composition": _chart_frequency_composition,
    "segment_pll_ranking": _chart_segment_pll_ranking,
    "risk_matrix": _chart_risk_matrix,
    "route_profile": _chart_route_profile,
    "individual_risk": _chart_individual_risk,
    "fn_curve": _chart_fn_curve,
    "priority_bubble": _chart_priority_bubble,
}


def render_charts(
    result: dict[str, Any],
    matrix: dict[str, Any],
    output_dir: Path,
    chart_ids: Iterable[str] = DEFAULT_CHART_IDS,
) -> list[Path]:
    paths: list[Path] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    for chart_id in chart_ids:
        if chart_id not in CHART_RENDERERS:
            raise ValueError(f"未知图表类型：{chart_id}")
        paths.append(
            CHART_RENDERERS[chart_id](
                result,
                matrix,
                output_dir / CHART_FILENAMES[chart_id],
            )
        )
    return paths


def write_dashboard(
    result: dict[str, Any],
    matrix: dict[str, Any],
    chart_paths: Iterable[Path],
    output_path: Path,
) -> Path:
    human, ranking = _require_spatial_result(result)
    maximum_ir = human["individual_risk"]["maximum"]
    pipeline_pll = human["societal_risk"]["pipeline_pll_per_year"]
    top = sorted(ranking, key=lambda row: int(row["risk_value_rank"]))[0]
    cards = [
        ("管段数", str(len(ranking))),
        ("全线PLL", f"{pipeline_pll:.4e} 人/年"),
        ("最大IR", f"{maximum_ir['value_per_year']:.4e} /年"),
        ("PLL最高管段", str(top["segment_id"])),
    ]
    card_html = "".join(
        f'<div class="card"><div class="k">{escape(label)}</div><div class="v">{escape(value)}</div></div>'
        for label, value in cards
    )
    figures = "".join(
        f'<figure><img src="charts/{escape(path.name)}" alt="{escape(path.stem)}"><figcaption>{escape(path.stem)}</figcaption></figure>'
        for path in chart_paths
    )
    blocker_text = "；".join(result.get("run", {}).get("formal_report_blockers", [])) or "无"
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>QRA自动化计算结果</title><style>
body{{margin:0;background:#f3f6fa;color:#243447;font-family:'Microsoft YaHei','SimHei',sans-serif}}header{{background:#173F67;color:white;padding:28px 5vw}}header h1{{margin:0 0 8px}}main{{max-width:1400px;margin:auto;padding:28px}}.cards{{display:grid;grid-template-columns:repeat(4,minmax(180px,1fr));gap:16px}}.card,figure,.notice{{background:white;border-radius:10px;box-shadow:0 3px 14px #173f6714;padding:18px}}.k{{color:#66788A}}.v{{font-size:23px;font-weight:700;margin-top:8px}}.notice{{margin:20px 0;border-left:6px solid #F4B183}}figure{{margin:20px 0}}img{{display:block;width:100%;height:auto}}figcaption{{text-align:center;color:#66788A;margin-top:8px}}@media(max-width:800px){{.cards{{grid-template-columns:1fr 1fr}}}}
</style></head><body><header><h1>QRA自动化计算结果</h1><div>样式：{escape(REPORT_STYLE_ID)} ｜ 计算配置：{escape(str(result.get('run',{}).get('calculation_profile','')))}</div></header>
<main><section class="cards">{card_html}</section><section class="notice"><b>使用边界：</b>{escape(matrix['criteria']['note'])}<br><b>正式报告阻断项：</b>{escape(blocker_text)}</section>{figures}</main></body></html>"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path
