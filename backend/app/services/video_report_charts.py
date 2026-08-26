"""单视频报表统计图生成（matplotlib Agg）。"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np

from app.services.fixed_report_images import prepare_fixed_body_demo_image

logger = logging.getLogger("video_report_pdf.charts")

_CJK_CANDIDATES = [
    "Noto Sans CJK SC", "Noto Sans CJK TC", "Noto Sans CJK KR", "Noto Sans CJK JP",
    "Noto Sans CJK", "Source Han Sans SC", "WenQuanYi Zen Hei", "WenQuanYi Micro Hei",
    "SimHei", "Microsoft YaHei", "Arial Unicode MS", "DejaVu Sans",
]


def _configure_cjk_font() -> str | None:
    for path in (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/arphic/ukai.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
    ):
        if Path(path).exists():
            try:
                fm.fontManager.addfont(path)
            except Exception:
                pass
    available = {font.name for font in fm.fontManager.ttflist}
    for name in _CJK_CANDIDATES:
        if name in available:
            matplotlib.rcParams["font.sans-serif"] = [name] + _CJK_CANDIDATES
            matplotlib.rcParams["axes.unicode_minus"] = False
            return name
    matplotlib.rcParams["axes.unicode_minus"] = False
    return None


_CONFIGURED_FONT = _configure_cjk_font()

CHART_KEYS = [
    "chart_body_length_distribution",
    "chart_body_length_without_tail_distribution",
    "chart_body_weight_distribution",
    "chart_condition_factor_distribution",
    "chart_surface_damage_distribution",
]
FIXED_BODY_DEMO_CHART_KEY = "chart_large_yellow_croaker_body"
ALL_CHART_KEYS = CHART_KEYS + [FIXED_BODY_DEMO_CHART_KEY]

CHART_FIGSIZE = (11.2, 5.2)
CHART_DPI = 300
DISTRIBUTION_BAR_WIDTH = 0.70
CATEGORY_BAR_WIDTH = 0.42
DISTRIBUTION_BAR_X_SPACING = 0.70
CATEGORY_BAR_X_SPACING = 0.55
X_TICK_LEFT_SHIFT_RATIO = 0.40
CHART_SAVE_PAD_INCHES = 0.10
CHART_TITLE_FONTSIZE = 23
CHART_X_AXIS_LABEL_FONTSIZE = CHART_TITLE_FONTSIZE
CHART_Y_AXIS_LABEL_FONTSIZE = CHART_TITLE_FONTSIZE
CHART_TICK_FONTSIZE = CHART_TITLE_FONTSIZE
CHART_X_TICK_FONTSIZE = CHART_TITLE_FONTSIZE
BODY_WEIGHT_X_TICK_FONTSIZE = CHART_TITLE_FONTSIZE
CHART_NO_DATA_FONTSIZE = 23
CHART_FONT_WEIGHT = 900
CHART_TEXT_COLOR = "#17324d"
CHART_MUTED_TEXT_COLOR = "#7f8c9a"
CHART_BACKGROUND_COLOR = "#fbfcff"
CHART_GRID_COLOR = "#dce6f2"
CHART_SPINE_COLOR = "#cbd7e6"
CHART_BAR_PALETTE = [
    "#4c78e8", "#f28e2b", "#59a14f", "#e15759", "#76b7b2",
    "#b07aa1", "#edc948", "#ff9da7", "#9c755f", "#79706e",
]


def _to_float(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def counts_to_percentages(counts: list[int]) -> list[float]:
    total = sum(int(count) for count in counts)
    return [0.0 for _ in counts] if total <= 0 else [round(int(count) * 100 / total, 2) for count in counts]


def _no_data_chart(out: Path, title: str) -> None:
    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, dpi=CHART_DPI)
    fig.patch.set_facecolor("#fff")
    ax.set_facecolor(CHART_BACKGROUND_COLOR)
    ax.axis("off")
    ax.text(0.5, 0.5, "暂无数据", ha="center", va="center", fontsize=CHART_NO_DATA_FONTSIZE,
            color=CHART_MUTED_TEXT_COLOR, fontweight=CHART_FONT_WEIGHT)
    ax.set_title(title, fontsize=CHART_TITLE_FONTSIZE, color=CHART_TEXT_COLOR,
                 pad=14, fontweight=CHART_FONT_WEIGHT)
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight", pad_inches=CHART_SAVE_PAD_INCHES, facecolor="#fff")
    plt.close(fig)


def _configure_percentage_y_axis(ax: Any, percentages: list[float]) -> None:
    maximum = max((float(value) for value in percentages), default=0.0)
    if maximum <= 0:
        step, upper = 2, 10
    elif maximum <= 10:
        step = 2
        upper = math.ceil(maximum * 1.08 / step) * step
    elif maximum <= 25:
        step = 5
        upper = math.ceil(maximum * 1.08 / step) * step
    elif maximum <= 60:
        step = 10
        upper = math.ceil(maximum * 1.08 / step) * step
    else:
        step = 20
        upper = math.ceil(maximum * 1.08 / step) * step
    upper = min(100, max(step, upper))
    ax.set_ylim(0, upper)
    ax.set_yticks(np.arange(0, upper + step * 0.5, step))


def _percent_distribution_data(values: list[float], bins: int) -> tuple[list[str], list[int], list[float]]:
    if not values:
        return [], [], []
    counts, edges = np.histogram(np.asarray(values, dtype=float), bins=bins)
    labels = [f"{float(edges[index]):.1f}" for index in range(len(edges) - 1)]
    counts_list = [int(count) for count in counts.tolist()]
    return labels, counts_list, counts_to_percentages(counts_list)


def _style_axes(ax: Any, *, xlabel: str | None = None, x_tick_fontsize: int = CHART_X_TICK_FONTSIZE) -> None:
    ax.set_facecolor(CHART_BACKGROUND_COLOR)
    ax.set_ylabel("占比（%）", fontsize=CHART_Y_AXIS_LABEL_FONTSIZE,
                  color=CHART_TEXT_COLOR, labelpad=10, fontweight=CHART_FONT_WEIGHT)
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=CHART_X_AXIS_LABEL_FONTSIZE,
                      color=CHART_TEXT_COLOR, labelpad=10, fontweight=CHART_FONT_WEIGHT)
    ax.tick_params(axis="x", colors=CHART_TEXT_COLOR, labelsize=x_tick_fontsize,
                   labelrotation=0, length=0, pad=7)
    ax.tick_params(axis="y", colors=CHART_TEXT_COLOR, labelsize=CHART_TICK_FONTSIZE,
                   length=0, pad=6)
    for label in [*ax.get_xticklabels(), *ax.get_yticklabels()]:
        label.set_fontweight(CHART_FONT_WEIGHT)
    ax.set_axisbelow(True)
    ax.grid(axis="y", linestyle=(0, (4, 4)), linewidth=0.8, alpha=0.8, color=CHART_GRID_COLOR)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(CHART_SPINE_COLOR)
        ax.spines[spine].set_linewidth(0.8)


def create_percent_distribution_chart(labels: list[str], percentages: list[float], *, output_path: Path,
                                      title: str, xlabel: str, sample_n: int,
                                      x_tick_fontsize: int = CHART_X_TICK_FONTSIZE) -> None:
    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, dpi=CHART_DPI)
    fig.patch.set_facecolor("#fff")
    if not labels:
        _no_data_chart(output_path, title)
        plt.close(fig)
        return
    positions = np.arange(len(labels)) * DISTRIBUTION_BAR_X_SPACING
    colors = [CHART_BAR_PALETTE[index % len(CHART_BAR_PALETTE)] for index in range(len(labels))]
    ax.bar(positions, percentages, width=DISTRIBUTION_BAR_WIDTH, color=colors, edgecolor=colors, alpha=0.95)
    tick_positions = positions - DISTRIBUTION_BAR_WIDTH / 2 - DISTRIBUTION_BAR_WIDTH * X_TICK_LEFT_SHIFT_RATIO
    ax.set_xticks(tick_positions, labels)
    ax.set_xlim(-DISTRIBUTION_BAR_X_SPACING / 2 - DISTRIBUTION_BAR_WIDTH * X_TICK_LEFT_SHIFT_RATIO,
                positions[-1] + DISTRIBUTION_BAR_X_SPACING / 2)
    ax.set_title(title, fontsize=CHART_TITLE_FONTSIZE, color=CHART_TEXT_COLOR, pad=16, fontweight=CHART_FONT_WEIGHT)
    _style_axes(ax, xlabel=xlabel, x_tick_fontsize=x_tick_fontsize)
    _configure_percentage_y_axis(ax, percentages)
    for label in ax.get_xticklabels():
        label.set_horizontalalignment("left")
    fig.tight_layout(rect=(0.10, 0.07, 0.92, 0.97), pad=1.1)
    fig.savefig(output_path, bbox_inches="tight", pad_inches=CHART_SAVE_PAD_INCHES, facecolor="#fff")
    plt.close(fig)


def create_percent_bar_chart(labels: list[str], counts: list[int], *, output_path: Path, title: str) -> None:
    if not labels or sum(int(count) for count in counts) <= 0:
        _no_data_chart(output_path, title)
        return
    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, dpi=CHART_DPI)
    fig.patch.set_facecolor("#fff")
    percentages = counts_to_percentages(counts)
    positions = np.arange(len(labels)) * CATEGORY_BAR_X_SPACING
    colors = [CHART_BAR_PALETTE[index % len(CHART_BAR_PALETTE)] for index in range(len(labels))]
    ax.bar(positions, percentages, width=CATEGORY_BAR_WIDTH, color=colors, edgecolor=colors, alpha=0.95)
    ax.set_xticks(positions, labels)
    ax.set_xlim(-CATEGORY_BAR_WIDTH / 2, positions[-1] + CATEGORY_BAR_X_SPACING / 2)
    ax.set_title(title, fontsize=CHART_TITLE_FONTSIZE, color=CHART_TEXT_COLOR, pad=16, fontweight=CHART_FONT_WEIGHT)
    _style_axes(ax)
    _configure_percentage_y_axis(ax, percentages)
    for label in ax.get_xticklabels():
        label.set_horizontalalignment("center")
    fig.tight_layout(rect=(0.10, 0.07, 0.92, 0.97), pad=1.1)
    fig.savefig(output_path, bbox_inches="tight", pad_inches=CHART_SAVE_PAD_INCHES, facecolor="#fff")
    plt.close(fig)


def _collect(fish: list[dict], key: str) -> list[float]:
    return [number for item in fish if isinstance(item, dict)
            if (number := _to_float(item.get(key))) is not None]


def _damage_buckets(fish: list[dict]) -> tuple[list[str], list[int]]:
    labels = ["无损伤", "轻微损伤", "中度损伤", "严重损伤"]
    counts = [0, 0, 0, 0]
    for item in fish:
        ratio = _to_float(item.get("surface_damage_ratio")) if isinstance(item, dict) else None
        if ratio is None:
            continue
        ratio = ratio / 100 if ratio > 1.5 else ratio
        counts[0 if ratio <= 0 else 1 if ratio <= 0.05 else 2 if ratio <= 0.15 else 3] += 1
    return labels, counts


def _purge_old_charts(out_dir: Path) -> None:
    for key in [*ALL_CHART_KEYS, "chart_meta"]:
        path = out_dir / f"{key}.png" if key != "chart_meta" else out_dir / "chart_meta.json"
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("删除旧图失败 %s: %s", path, exc)


def generate_fixed_body_demo(out_dir: Path) -> dict[str, Any]:
    out = out_dir / f"{FIXED_BODY_DEMO_CHART_KEY}.png"
    source = prepare_fixed_body_demo_image(out_dir)
    meta: dict[str, Any] = {"labels": [], "counts": [], "percentages": [], "sum_percentages": 0.0,
                            "y_axis": "—", "fixed_image": True}
    if not source or not Path(source).exists():
        _no_data_chart(out, "图6：大黄鱼形体指标测量示意图")
        meta["error"] = "source_image_missing"
        return {FIXED_BODY_DEMO_CHART_KEY: meta}
    fig, ax = plt.subplots(figsize=(7.2, 4.0), dpi=180)
    fig.patch.set_facecolor("#fff")
    ax.axis("off")
    try:
        from PIL import Image
        with Image.open(source) as image:
            ax.imshow(image)
    except Exception:
        ax.text(0.5, 0.5, "示意图加载失败", ha="center", va="center", fontsize=14,
                color=CHART_MUTED_TEXT_COLOR, fontweight=CHART_FONT_WEIGHT)
    # 模板单元格本身已经有“大黄鱼形体指标测量示意图”文字，图片内不再
    # 重复绘制图号和标题，避免 PDF 中出现两套标题。
    fig.tight_layout(pad=1.0)
    fig.savefig(out, bbox_inches="tight", pad_inches=0.10, facecolor="#fff")
    plt.close(fig)
    return {FIXED_BODY_DEMO_CHART_KEY: meta}


def generate_charts(fish: list[dict], out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    _purge_old_charts(out_dir)
    out_files: dict[str, Path] = {}
    metadata: dict[str, dict[str, Any]] = {}
    pairs = [
        ("chart_body_length_distribution", "图1：全长分布图", _collect(fish, "body_length_cm"), "全长 (cm)"),
        ("chart_body_length_without_tail_distribution", "图2：体高分布图", _collect(fish, "body_height_cm"), "体高 (cm)"),
        ("chart_body_weight_distribution", "图3：体重分布图", _collect(fish, "pred_weight_g"), "体重 (g)"),
        ("chart_condition_factor_distribution", "图4：肥满度分布图", _collect(fish, "condition_factor"), "肥满度"),
    ]
    for key, title, values, xlabel in pairs:
        out = out_dir / f"{key}.png"
        n = len(values)
        bins = max(4, min(10, int(round(n ** 0.5 / 1.5)))) if n else 0
        labels, counts, percentages = _percent_distribution_data(values, bins)
        if key == "chart_body_weight_distribution":
            labels = [f"{float(label):.0f}" for label in labels]
        create_percent_distribution_chart(labels, percentages, output_path=out, title=title, xlabel=xlabel,
                                          sample_n=n, x_tick_fontsize=BODY_WEIGHT_X_TICK_FONTSIZE)
        metadata[key] = {"labels": labels, "counts": counts, "percentages": percentages,
                         "sum_percentages": round(sum(percentages), 2), "y_axis": "占比（%）", "sample_n": n}
        out_files[key] = out
    damage_labels, damage_counts = _damage_buckets(fish)
    damage_out = out_dir / "chart_surface_damage_distribution.png"
    create_percent_bar_chart(damage_labels, damage_counts, output_path=damage_out, title="图5：体表损伤程度统计图")
    damage_percentages = counts_to_percentages(damage_counts)
    metadata["chart_surface_damage_distribution"] = {
        "labels": damage_labels, "counts": damage_counts, "percentages": damage_percentages,
        "sum_percentages": round(sum(damage_percentages), 2), "y_axis": "占比（%）",
        "sample_n": sum(damage_counts),
    }
    out_files["chart_surface_damage_distribution"] = damage_out
    fixed = generate_fixed_body_demo(out_dir)
    metadata[FIXED_BODY_DEMO_CHART_KEY] = fixed[FIXED_BODY_DEMO_CHART_KEY]
    out_files[FIXED_BODY_DEMO_CHART_KEY] = out_dir / f"{FIXED_BODY_DEMO_CHART_KEY}.png"
    (out_dir / "chart_meta.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_files


def charts_rel_paths(charts_dir: Path, project_root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in ALL_CHART_KEYS:
        path = charts_dir / f"{key}.png"
        if path.exists():
            try:
                result[key] = str(path.relative_to(project_root))
            except ValueError:
                result[key] = str(path)
    return result


__all__ = ["ALL_CHART_KEYS", "CHART_KEYS", "FIXED_BODY_DEMO_CHART_KEY", "charts_rel_paths",
           "counts_to_percentages", "create_percent_bar_chart", "create_percent_distribution_chart",
           "generate_charts", "generate_fixed_body_demo"]
