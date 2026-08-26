"""鱼品质分布图统一分桶规则。"""
from __future__ import annotations

from math import isfinite
from typing import Any


FISH_DISTRIBUTION_BINS: dict[str, tuple[tuple[float, float, str], ...]] = {
    "body_length_cm": (
        (-float("inf"), 10, "<10"),
        (10, 15, "10-15"),
        (15, 20, "15-20"),
        (20, 25, "20-25"),
        (25, 30, "25-30"),
        (30, 40, "30-40"),
        (40, float("inf"), ">=40"),
    ),
    "body_height_cm": (
        (-float("inf"), 2, "<2"),
        (2, 4, "2-4"),
        (4, 6, "4-6"),
        (6, 8, "6-8"),
        (8, 10, "8-10"),
        (10, float("inf"), ">=10"),
    ),
    "body_weight_g": (
        (-float("inf"), 50, "<50"),
        (50, 100, "50-100"),
        (100, 200, "100-200"),
        (200, 400, "200-400"),
        (400, 800, "400-800"),
        (800, float("inf"), ">=800"),
    ),
    "condition_factor": (
        (-float("inf"), 0.8, "<0.8"),
        (0.8, 1.0, "0.8-1.0"),
        (1.0, 1.2, "1.0-1.2"),
        (1.2, 1.5, "1.2-1.5"),
        (1.5, 2.0, "1.5-2.0"),
        (2.0, float("inf"), ">=2.0"),
    ),
    "length_weight_ratio": (
        (-float("inf"), 0.05, "<0.05"),
        (0.05, 0.10, "0.05-0.10"),
        (0.10, 0.15, "0.10-0.15"),
        (0.15, 0.20, "0.15-0.20"),
        (0.20, 0.30, "0.20-0.30"),
        (0.30, float("inf"), ">=0.30"),
    ),
    "surface_damage_percent": (
        (-float("inf"), 0.0001, "0%"),
        (0.0001, 5, "0-5%"),
        (5, 10, "5-10%"),
        (10, 20, "10-20%"),
        (20, 30, "20-30%"),
        (30, float("inf"), ">=30%"),
    ),
}


def _valid_values(values: list[Any], *, allow_zero: bool = False) -> list[float]:
    result: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if isfinite(number) and (number >= 0 if allow_zero else number > 0):
            result.append(number)
    return result


def build_fixed_distribution(
    values: list[Any],
    metric: str,
) -> list[dict[str, Any]]:
    """按固定左闭右开区间生成完整分布，最后一个区间包含最大值。"""
    bins = FISH_DISTRIBUTION_BINS[metric]
    cleaned = _valid_values(
        values,
        allow_zero=metric == "surface_damage_percent",
    )
    total = len(cleaned)
    result: list[dict[str, Any]] = []
    for index, (low, high, label) in enumerate(bins):
        if index == len(bins) - 1:
            count = sum(1 for value in cleaned if low <= value <= high)
        else:
            count = sum(1 for value in cleaned if low <= value < high)
        result.append({"label": label, "count": count, "ratio": count / total if total else 0.0})
    return result


__all__ = ["FISH_DISTRIBUTION_BINS", "build_fixed_distribution"]
