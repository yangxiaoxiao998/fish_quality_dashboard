"""鱼品质检测日报 - 通用格式化工具。

被 report_stats_service / report_docx_service 共用。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.utils.time_utils import BEIJING_TZ
from app.utils.timezone import format_china_datetime


# ------------------------- 比例归一化 -------------------------

def normalize_ratio_percent(value: Any) -> float | None:
    """把任意输入归一化为"百分比"（0~100 范围）。

    规则：
        - None / 非数字 / 负数 → None
        - 0 <= x <= 1 → x * 100
        - 1 <  x <= 100 → x

    业务字段：surface_damage_ratio / wound_area_ratio / wound_ratio
    这些字段在 DB 里是 0~1，但下游展示需要百分比。
    """
    if value is None:
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if x < 0:
        return None
    if x <= 1.0:
        x *= 100.0
    if x > 100.0:
        # 大于 100% 一律截到 100（损坏面积不可能超过鱼体）
        x = 100.0
    return x


def format_percent(value: Any, digits: int = 2) -> str:
    """百分比格式化。None / 异常 → '--'。"""
    pct = normalize_ratio_percent(value)
    if pct is None:
        return "--"
    return f"{pct:.{digits}f}%"


def has_surface_damage(value: Any) -> bool:
    """判定一条鱼是否"有体表损伤"。

    用 surface_damage_ratio > 0 当作阈值。
    旧字段 surface_damage（字符串）保留兼容。
    """
    pct = normalize_ratio_percent(value)
    if pct is not None and pct > 0:
        return True
    return False


# ------------------------- 数值 / 文本 -------------------------

def safe_num(value: Any, digits: int = 2) -> str:
    """通用数值格式化。None / 异常 → '--'。"""
    if value is None:
        return "--"
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "--"
    return f"{x:.{digits}f}"


def safe_int(value: Any) -> str:
    if value is None:
        return "0"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "0"


def safe_text(value: Any) -> str:
    if value is None:
        return "--"
    s = str(value).strip()
    return s if s else "--"


# ------------------------- 时间 -------------------------

def format_dt(value: Any) -> str:
    """统一时间格式：YYYY-MM-DD HH:mm（北京时间）。"""
    if value is None:
        return "--"
    try:
        return format_china_datetime(value)
    except Exception:
        return "--"


def format_date_cn(value: date | str | None) -> str:
    """中文长日期：2026年06月14日"""
    if value is None:
        return "--"
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return value
    return f"{value.year}年{value.month:02d}月{value.day:02d}日"


# ------------------------- 视频状态 / 来源 中文映射 -------------------------

_VIDEO_STATUS_CN = {
    "queued": "已入队",
    "processing": "处理中",
    "done": "已完成",
    "failed": "失败",
    "stopped": "已停止",
}


_VIDEO_SOURCE_CN = {
    "upload": "上传",
    "rtsp": "RTSP",
}


def video_status_cn(value: Any) -> str:
    return _VIDEO_STATUS_CN.get(str(value or ""), safe_text(value))


def video_source_cn(value: Any) -> str:
    return _VIDEO_SOURCE_CN.get(str(value or ""), safe_text(value))


# ------------------------- 异常鱼原因生成 -------------------------

def build_abnormal_reason(
    *,
    surface_damage_pct: float | None,
    abnormal: bool,
    is_partial: bool = False,
) -> str:
    """根据损伤占比 / 异常状态拼接原因字符串。"""
    if not abnormal:
        return "—"

    parts: list[str] = []
    if surface_damage_pct is not None and surface_damage_pct > 50.0:
        parts.append(f"体表损伤占比 {surface_damage_pct:.2f}%")
    if not parts:
        return "指标异常"
    return "；".join(parts)
