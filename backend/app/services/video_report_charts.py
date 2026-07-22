"""单视频报表统计图生成（matplotlib Agg）。

输出 PNG 到：data/results/<video_id>/reports/charts/<chart_key>.png

要求（v3，唯一图表入口）：
- 5 张分布图 + 1 张固定大黄鱼示意图（图 6）。
- 强制百分比纵轴：y 轴 0~100，标题"占比（%）"，柱顶 xx.x%。
- 不再使用 ax.hist（旧 ax.hist + weights 已经删除）。
- 必须先删除旧 chart_*.png 再重新生成，避免 PDF 缓存。
- 生成 chart_meta.json 记录 labels / counts / percentages / sum_percentages / y_axis。
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # noqa: E402
import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np

from app.services.fixed_report_images import prepare_fixed_body_demo_image

logger = logging.getLogger("video_report_pdf.charts")

_CJK_CANDIDATES = [
    "Noto Sans CJK SC",
    "Noto Sans CJK TC",
    "Noto Sans CJK KR",
    "Noto Sans CJK JP",
    "Noto Sans CJK",
    "Source Han Sans SC",
    "WenQuanYi Zen Hei",
    "WenQuanYi Micro Hei",
    "SimHei",
    "Microsoft YaHei",
    "Arial Unicode MS",
    "DejaVu Sans",
]


def _configure_cjk_font() -> str | None:
    available = {f.name for f in fm.fontManager.ttflist}

    from pathlib import Path as _P
    for ttf in (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/arphic/ukai.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
    ):
        if _P(ttf).exists():
            try:
                fm.fontManager.addfont(ttf)
            except Exception:
                pass

    available = {f.name for f in fm.fontManager.ttflist}
    for name in _CJK_CANDIDATES:
        if name in available:
            matplotlib.rcParams["font.sans-serif"] = [name] + _CJK_CANDIDATES
            matplotlib.rcParams["axes.unicode_minus"] = False
            logger.info(f"[video_report_charts] CJK font: {name}")
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


# 统一的图表尺寸 / 柱宽 / 留白常量。
# 这些常量专门用于"报表统计图"，不能影响其他业务图。
CHART_FIGSIZE = (7.2, 4.0)
DISTRIBUTION_BAR_WIDTH = 0.48
CATEGORY_BAR_WIDTH = 0.42
CHART_X_MARGIN = 0.08
CHART_SAVE_PAD_INCHES = 0.08


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        if f != f:
            return None
        return f
    except Exception:
        return None


def _no_data_chart(out: Path, title: str) -> None:
    fig, ax = plt.subplots(figsize=(6.0, 3.2), dpi=160)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#f7fbff")
    ax.axis("off")
    ax.text(0.5, 0.5, "暂无数据", ha="center", va="center", fontsize=18, color="#999")
    ax.set_title(title, fontsize=13, color="#17324d")
    for spine in ("top", "right", "left", "bottom"):
        ax.spines[spine].set_visible(False)
    fig.tight_layout()
    fig.savefig(
        str(out),
        bbox_inches="tight",
        pad_inches=CHART_SAVE_PAD_INCHES,
        facecolor="#ffffff",
    )
    plt.close(fig)


def counts_to_percentages(counts: list[int]) -> list[float]:
    total = sum(int(c) for c in counts)
    if total <= 0:
        return [0.0 for _ in counts]
    return [round(int(c) * 100.0 / total, 2) for c in counts]


def _bucket_labels_from_edges(edges: list[float]) -> list[str]:
    return [f"{edges[i]:.1f}-{edges[i+1]:.1f}" for i in range(len(edges) - 1)]


def _percent_distribution_data(values: list[float], bins: int) -> tuple[list[str], list[int], list[float]]:
    """连续指标分箱并转百分比。

    返回 (labels, counts, percentages)，percentages 合计约 100%。
    """
    if not values:
        return [], [], []
    arr = np.asarray([float(v) for v in values], dtype=float)
    counts_arr, edges = np.histogram(arr, bins=bins)
    counts_list = [int(c) for c in counts_arr.tolist()]
    labels = _bucket_labels_from_edges([float(e) for e in edges.tolist()])
    percentages = counts_to_percentages(counts_list)
    return labels, counts_list, percentages


def create_percent_distribution_chart(
    labels: list[str],
    percentages: list[float],
    *,
    output_path: Path,
    title: str,
    xlabel: str,
    sample_n: int,
) -> None:
    """画百分比分布图（直方/连续指标）。

    - y 轴 0~100，标题"占比（%）"。
    - 柱顶 xx.x%。
    - 不再使用 ax.hist。
    - 柱宽固定为 ``DISTRIBUTION_BAR_WIDTH``，避免柱形过宽。
    """
    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, dpi=180)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#f7fbff")

    if not labels:
        ax.text(0.5, 0.5, "暂无数据", ha="center", va="center", fontsize=16, color="#999")
        ax.set_title(title, fontsize=13, color="#17324d")
        for spine in ("top", "right", "left", "bottom"):
            ax.spines[spine].set_visible(False)
        fig.tight_layout(pad=1.0)
        fig.savefig(
            str(output_path),
            bbox_inches="tight",
            pad_inches=CHART_SAVE_PAD_INCHES,
            facecolor="#ffffff",
        )
        plt.close(fig)
        return

    bars = ax.bar(
        labels,
        percentages,
        width=DISTRIBUTION_BAR_WIDTH,
        color="#2f7bff",
        edgecolor="#12d6df",
        linewidth=1.0,
    )
    ax.margins(x=CHART_X_MARGIN)

    ax.set_title(title, fontsize=14, color="#17324d", pad=12)
    ax.set_xlabel(xlabel, fontsize=11, color="#17324d")
    ax.set_ylabel("占比（%）", fontsize=11, color="#17324d")
    ax.set_ylim(0, 100)
    ax.tick_params(axis="x", colors="#17324d", labelsize=9, labelrotation=20)
    ax.tick_params(axis="y", colors="#17324d", labelsize=9)
    ax.grid(axis="y", linestyle="--", alpha=0.35, color="#d8e6ff")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color("#d8e6ff")
    ax.spines["bottom"].set_color("#d8e6ff")

    for bar, pct in zip(bars, percentages):
        if pct > 0.05:
            ax.text(
                bar.get_x() + bar.get_width() / 2.0,
                min(100, bar.get_height() + 1),
                f"{pct:.1f}%",
                ha="center",
                va="bottom",
                fontsize=9,
                color="#17324d",
            )

    fig.suptitle(
        f"样本数 n={sample_n}",
        x=0.99,
        y=0.01,
        ha="right",
        fontsize=8,
        color="#86909c",
    )
    fig.tight_layout(rect=(0.02, 0.04, 0.98, 0.98), pad=1.0)
    fig.savefig(
        str(output_path),
        bbox_inches="tight",
        pad_inches=CHART_SAVE_PAD_INCHES,
        facecolor="#ffffff",
    )
    plt.close(fig)


def create_percent_bar_chart(
    labels: list[str],
    counts: list[int],
    *,
    output_path: Path,
    title: str,
) -> None:
    """离散类（损伤等级等）百分比柱图。

    - 柱宽固定为 ``CATEGORY_BAR_WIDTH``，避免 4 根柱形贴满整图。
    - 左右两端留出可见间距。
    """
    fig, ax = plt.subplots(figsize=CHART_FIGSIZE, dpi=180)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#f7fbff")

    if not labels or sum(int(c) for c in counts) <= 0:
        ax.text(0.5, 0.5, "暂无数据", ha="center", va="center", fontsize=16, color="#999")
        ax.set_title(title, fontsize=13, color="#17324d")
        for spine in ("top", "right", "left", "bottom"):
            ax.spines[spine].set_visible(False)
        fig.tight_layout(pad=1.0)
        fig.savefig(
            str(output_path),
            bbox_inches="tight",
            pad_inches=CHART_SAVE_PAD_INCHES,
            facecolor="#ffffff",
        )
        plt.close(fig)
        return

    pct = counts_to_percentages([int(c) for c in counts])
    palette = ["#2f7bff", "#12d6df", "#7c5cff", "#ffb020", "#ff4d4f"]
    colors = [palette[i % len(palette)] for i in range(len(labels))]
    bars = ax.bar(
        labels,
        pct,
        width=CATEGORY_BAR_WIDTH,
        color=colors,
        edgecolor="#d8e6ff",
        linewidth=0.6,
    )
    ax.margins(x=CHART_X_MARGIN)

    ax.set_title(title, fontsize=14, color="#17324d", pad=12)
    ax.set_ylabel("占比（%）", fontsize=10, color="#17324d")
    ax.set_ylim(0, 100)
    ax.tick_params(axis="x", colors="#17324d", labelsize=10)
    ax.tick_params(axis="y", colors="#17324d", labelsize=9)
    ax.grid(axis="y", linestyle="--", alpha=0.35, color="#d8e6ff")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color("#d8e6ff")
    ax.spines["bottom"].set_color("#d8e6ff")

    for b, p in zip(bars, pct):
        if p > 0.05:
            ax.text(
                b.get_x() + b.get_width() / 2.0,
                p,
                f"{p:.1f}%",
                ha="center",
                va="bottom",
                fontsize=10,
                color="#17324d",
                fontweight="bold",
            )

    total = sum(int(c) for c in counts)
    fig.suptitle(
        f"样本数 n={total}",
        x=0.99,
        y=0.01,
        ha="right",
        fontsize=8,
        color="#86909c",
    )
    fig.tight_layout(rect=(0.02, 0.04, 0.98, 0.98), pad=1.0)
    fig.savefig(
        str(output_path),
        bbox_inches="tight",
        pad_inches=CHART_SAVE_PAD_INCHES,
        facecolor="#ffffff",
    )
    plt.close(fig)


def _collect(fish: list[dict], key: str) -> list[float]:
    out: list[float] = []
    for f in fish:
        if not isinstance(f, dict):
            continue
        v = _to_float(f.get(key))
        if v is None:
            continue
        out.append(v)
    return out


def _damage_buckets(fish: list[dict]) -> tuple[list[str], list[int]]:
    """按 surface_damage_ratio 分级：无 / 轻微 / 中度 / 严重。"""
    labels = ["无损伤", "轻微损伤", "中度损伤", "严重损伤"]
    counts = [0, 0, 0, 0]
    for f in fish:
        if not isinstance(f, dict):
            continue
        ratio = _to_float(f.get("surface_damage_ratio"))
        if ratio is None:
            continue
        # surface_damage_ratio 可能 0~1 或 0~100；统一按 0~1 阈值判断
        if ratio > 1.5:
            ratio = ratio / 100.0
        if ratio <= 0:
            counts[0] += 1
        elif ratio <= 0.05:
            counts[1] += 1
        elif ratio <= 0.15:
            counts[2] += 1
        else:
            counts[3] += 1
    return labels, counts


def _purge_old_charts(out_dir: Path) -> None:
    """删除旧的 chart_*.png 和 chart_meta.json，避免 PDF 缓存旧图。"""
    for pattern in (
        "chart_body_length_distribution.png",
        "chart_body_length_without_tail_distribution.png",
        "chart_body_weight_distribution.png",
        "chart_condition_factor_distribution.png",
        "chart_surface_damage_distribution.png",
        "chart_large_yellow_croaker_body.png",
        "chart_meta.json",
    ):
        p = out_dir / pattern
        try:
            if p.exists():
                p.unlink()
        except OSError as exc:
            logger.warning(f"[video_report_charts] 删除 {p} 失败：{exc}")


def generate_fixed_body_demo(out_dir: Path) -> dict[str, Any]:
    """生成图 6：大黄鱼形体指标测量示意图。"""
    out = out_dir / f"{FIXED_BODY_DEMO_CHART_KEY}.png"
    src_png = prepare_fixed_body_demo_image(out_dir)
    meta: dict[str, Any] = {
        "labels": [],
        "counts": [],
        "percentages": [],
        "sum_percentages": 0.0,
        "y_axis": "—",
        "fixed_image": True,
    }
    if not src_png or not Path(src_png).exists():
        _no_data_chart(out, "图6：大黄鱼形体指标测量示意图")
        meta["error"] = "source_image_missing"
        return {FIXED_BODY_DEMO_CHART_KEY: meta}

    fig, ax = plt.subplots(figsize=(7.2, 4.0), dpi=180)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#f7fbff")
    ax.axis("off")
    try:
        from PIL import Image
        with Image.open(str(src_png)) as img:
            ax.imshow(img)
    except Exception:
        ax.text(
            0.5, 0.5, "示意图加载失败",
            ha="center", va="center", fontsize=14, color="#86909c",
        )
    ax.set_title("图6：大黄鱼形体指标测量示意图", fontsize=14, color="#17324d", pad=10)
    fig.tight_layout(pad=1.0)
    fig.savefig(
        str(out),
        bbox_inches="tight",
        pad_inches=CHART_SAVE_PAD_INCHES,
        facecolor="#ffffff",
    )
    plt.close(fig)
    return {FIXED_BODY_DEMO_CHART_KEY: meta}


def generate_charts(fish: list[dict], out_dir: Path) -> dict[str, Path]:
    """生成 5 张百分比分布图 + 1 张固定示意图。

    唯一图表入口。所有 chart_*.png 必须经过此函数生成。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    _purge_old_charts(out_dir)

    out_files: dict[str, Path] = {}
    meta: dict[str, dict[str, Any]] = {}

    body = _collect(fish, "body_length_cm")
    no_tail = _collect(fish, "body_length_no_tail_cm")
    weight = _collect(fish, "pred_weight_g")
    cf = _collect(fish, "condition_factor")
    dmg_labels, dmg_counts = _damage_buckets(fish)

    pairs = [
        (
            "chart_body_length_distribution",
            "图1：鱼体长度分布图",
            body,
            "体长 (cm)",
        ),
        (
            "chart_body_length_without_tail_distribution",
            "图2：体长（无尾鳍）分布图",
            no_tail,
            "无尾体长 (cm)",
        ),
        (
            "chart_body_weight_distribution",
            "图3：体重分布图",
            weight,
            "体重 (g)",
        ),
        (
            "chart_condition_factor_distribution",
            "图4：肥满度分布图",
            cf,
            "肥满度",
        ),
    ]

    for key, title, values, xlabel in pairs:
        out = out_dir / f"{key}.png"
        n = len(values)
        bins = max(4, min(14, int(round((n ** 0.5) / 1.5)))) if n > 0 else 0
        try:
            labels, counts, pct = _percent_distribution_data(values, bins)
            create_percent_distribution_chart(
                labels,
                pct,
                output_path=out,
                title=title,
                xlabel=xlabel,
                sample_n=n,
            )
            meta[key] = {
                "labels": labels,
                "counts": counts,
                "percentages": [round(float(p), 2) for p in pct],
                "sum_percentages": round(float(sum(pct)), 2),
                "y_axis": "占比（%）",
                "sample_n": n,
            }
        except Exception as exc:
            logger.warning(f"[video_report_charts] 生成 {key} 失败：{exc}; 写占位图")
            _no_data_chart(out, title)
            meta[key] = {
                "labels": [],
                "counts": [],
                "percentages": [],
                "sum_percentages": 0.0,
                "y_axis": "占比（%）",
                "sample_n": 0,
                "error": str(exc),
            }
        out_files[key] = out

    out = out_dir / "chart_surface_damage_distribution.png"
    try:
        create_percent_bar_chart(
            dmg_labels,
            [int(c) for c in dmg_counts],
            output_path=out,
            title="图5：体表损伤程度统计图",
        )
        pct = counts_to_percentages([int(c) for c in dmg_counts])
        meta["chart_surface_damage_distribution"] = {
            "labels": list(dmg_labels),
            "counts": [int(c) for c in dmg_counts],
            "percentages": [round(float(p), 2) for p in pct],
            "sum_percentages": round(float(sum(pct)), 2),
            "y_axis": "占比（%）",
            "sample_n": sum(int(c) for c in dmg_counts),
        }
    except Exception as exc:
        logger.warning(f"[video_report_charts] 生成 chart_surface_damage_distribution 失败：{exc}; 写占位图")
        _no_data_chart(out, "图5：体表损伤程度统计图")
        meta["chart_surface_damage_distribution"] = {
            "labels": [],
            "counts": [],
            "percentages": [],
            "sum_percentages": 0.0,
            "y_axis": "占比（%）",
            "sample_n": 0,
            "error": str(exc),
        }
    out_files["chart_surface_damage_distribution"] = out

    fixed_meta = generate_fixed_body_demo(out_dir)
    meta[FIXED_BODY_DEMO_CHART_KEY] = fixed_meta.get(FIXED_BODY_DEMO_CHART_KEY, {})
    out_files[FIXED_BODY_DEMO_CHART_KEY] = out_dir / f"{FIXED_BODY_DEMO_CHART_KEY}.png"

    try:
        (out_dir / "chart_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning(f"[video_report_charts] 写 chart_meta.json 失败：{exc}")

    return out_files


def charts_rel_paths(charts_dir: Path, project_root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in ALL_CHART_KEYS:
        p = charts_dir / f"{k}.png"
        if not p.exists():
            continue
        try:
            rel = str(p.relative_to(project_root))
        except ValueError:
            rel = str(p)
        out[k] = rel
    return out


__all__ = [
    "ALL_CHART_KEYS",
    "CHART_KEYS",
    "FIXED_BODY_DEMO_CHART_KEY",
    "charts_rel_paths",
    "counts_to_percentages",
    "create_percent_bar_chart",
    "create_percent_distribution_chart",
    "generate_charts",
    "generate_fixed_body_demo",
]