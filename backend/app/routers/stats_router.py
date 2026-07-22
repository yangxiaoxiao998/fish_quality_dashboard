# 作用：提供统计数据的 API 接口
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select, and_

from app.repositories.db import get_db
from app.repositories import stats_repo
from app.repositories.models import Stats5Min, Fish
from app.utils.time_utils import to_beijing

router = APIRouter()


def _parse_range_dt(value: str | None) -> datetime | None:
    """
    解析 ISO8601 字符串为带时区的 datetime；
    解析失败返回 None。
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


@router.get("/stats")
def stats(
    video_id: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    interval: Optional[str] = "5min",
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = select(Stats5Min)

    if video_id:
        stmt = stmt.where(Stats5Min.video_id == video_id)

    start_dt = _parse_range_dt(start_time)
    if start_dt:
        stmt = stmt.where(Stats5Min.bucket_start >= start_dt)

    end_dt = _parse_range_dt(end_time)
    if end_dt:
        stmt = stmt.where(Stats5Min.bucket_start <= end_dt)

    stmt = stmt.order_by(Stats5Min.bucket_start.desc()).limit(288)
    rows = list(db.execute(stmt).scalars().all())

    out: list[dict] = []
    for r in rows:
        mean_length = (r.sum_body_length / r.count) if r.count else 0.0
        mean_height = (r.sum_body_height / r.count) if r.count else 0.0
        mean_weight = (r.sum_body_weight / r.count) if r.count else 0.0
        mean_cf = (r.sum_condition_factor / r.count) if r.count else 0.0
        abnormal_rate = (r.abnormal_count / r.count) if r.count else 0.0

        out.append(
            {
                "bucket_start": r.bucket_start,
                "bucket_end": r.bucket_end,
                "count": r.count,
                "mean_body_length": round(mean_length, 2),
                "mean_body_height": round(mean_height, 2),
                "mean_body_weight": round(mean_weight, 2),
                "mean_condition_factor": round(mean_cf, 3),
                "abnormal_rate": round(abnormal_rate, 3),
            }
        )

    return out


# ============================================================
# 动态分布 helper（用于首页四个统计图）
# ============================================================

def _finite_positive(v) -> float | None:
    """
    过滤 None / NaN / Inf / <=0。
    返回清洗后的 float，否则 None。
    """
    if v is None:
        return None
    try:
        x = float(v)
    except Exception:
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    if x <= 0:
        return None
    return x


def build_dynamic_distribution(
    values,
    bins: int = 10,
    digits: int = 2,
) -> list[dict]:
    """
    根据当前数据动态计算等宽分桶。

    返回: [{"label", "min", "max", "count", "ratio"}, ...]
    - 总和 ratio ≈ 1（最后一个 ratio 用 1 - 之前之和修正浮点误差）
    - 空数据 → []
    - 单点 → 1 个 bin，ratio=1.0
    """
    cleaned = [x for x in (_finite_positive(v) for v in values) if x is not None]
    total = len(cleaned)
    if total == 0:
        return []

    if total == 1:
        x = cleaned[0]
        # 单点时围绕该值生成小区间，避免 0 宽度
        width = max(abs(x) * 0.05, 1.0 if x > 10 else 0.1)
        a = x - width / 2
        b = x + width / 2
        return [
            {
                "label": f"{a:.{digits}f}-{b:.{digits}f}",
                "min": round(a, digits),
                "max": round(b, digits),
                "count": 1,
                "ratio": 1.0,
            }
        ]

    mn = min(cleaned)
    mx = max(cleaned)

    # 全部相等时人为撑开一个小区间
    if mn == mx:
        width = max(abs(mn) * 0.1, 1.0 if mn > 10 else 0.1)
        mn = mn - width / 2
        mx = mx + width / 2

    # bin 数量: 5~bins 之间，根据数据量自适应
    bin_count = min(max(5, int(math.sqrt(total)) + 1), bins)
    step = (mx - mn) / bin_count

    counts = [0] * bin_count
    for x in cleaned:
        idx = int((x - mn) / step)
        if idx >= bin_count:
            idx = bin_count - 1
        elif idx < 0:
            idx = 0
        counts[idx] += 1

    result: list[dict] = []
    for i, c in enumerate(counts):
        a = mn + step * i
        b = mn + step * (i + 1)
        result.append(
            {
                "label": f"{a:.{digits}f}-{b:.{digits}f}",
                "min": round(a, digits),
                "max": round(b, digits),
                "count": int(c),
                "ratio": round((c / total), 6) if total else 0.0,
            }
        )

    # 用最后一个 ratio 修正浮点误差，使 sum(ratio) 严格 == 1
    if result:
        s = round(sum(x["ratio"] for x in result[:-1]), 6)
        last_ratio = max(0.0, round(1.0 - s, 6))
        result[-1]["ratio"] = last_ratio

    return result


@router.get("/stats/distribution")
def stats_distribution(
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    db: Session = Depends(get_db),
) -> dict:
    """
    首页四个统计图所需分布数据。

    字段（每个 distribution 都是 [{label, min, max, count, ratio}]）：
        length_distribution      体长分布  (cm, digits=2)
        weight_distribution      体重分布  (g,  digits=1)
        condition_distribution   肥满度分布 (digits=3)
        length_weight_ratio      体长/体重比 (digits=3)
        abnormal_distribution    异常比例  {normal, abnormal, total}
        count                    区间内的鱼总数

    行为：
        - 横轴 bins 根据当前筛选范围数据动态计算
        - 过滤 None / NaN / Inf / <=0
        - 数据量 0 → 返回空数组
        - 数据量 ≥ 1 → 5~10 个 bin（自适应）
        - 单点 → 1 个 bin，ratio=1
        - ratio 之和 = 1（最后一个用 1-之前之和修正浮点误差）
    """
    stmt = select(Fish).where(Fish.status == "done")

    start_dt = _parse_range_dt(start_time)
    if start_dt:
        stmt = stmt.where(Fish.created_at >= start_dt)
    end_dt = _parse_range_dt(end_time)
    if end_dt:
        stmt = stmt.where(Fish.created_at <= end_dt)

    fishes = db.execute(stmt).scalars().all()

    lengths: list[float] = []
    weights: list[float] = []
    conditions: list[float] = []
    ratios: list[float] = []
    abnormal_count = 0
    normal_count = 0

    for f in fishes:
        bl = _finite_positive(getattr(f, "body_length", None))
        bw = _finite_positive(getattr(f, "body_weight", None))
        cf = _finite_positive(getattr(f, "condition_factor", None))
        if bl is not None:
            lengths.append(bl)
        if bw is not None:
            weights.append(bw)
        if cf is not None:
            conditions.append(cf)
        if bl is not None and bw is not None:
            ratios.append(bl / bw)

        # surface_damage 是字符串：'无' / '轻微' / '严重'，异常指非"无"
        if f.surface_damage and f.surface_damage != "无":
            abnormal_count += 1
        else:
            normal_count += 1

    return {
        "length_distribution": build_dynamic_distribution(lengths, bins=10, digits=2),
        "weight_distribution": build_dynamic_distribution(weights, bins=10, digits=1),
        "condition_distribution": build_dynamic_distribution(conditions, bins=8, digits=3),
        "length_weight_ratio": build_dynamic_distribution(ratios, bins=10, digits=3),
        "abnormal_distribution": {
            "normal": normal_count,
            "abnormal": abnormal_count,
            "total": len(fishes),
        },
        "count": len(fishes),
    }
