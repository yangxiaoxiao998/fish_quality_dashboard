from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def now_china() -> datetime:
    return datetime.now(CHINA_TZ)


def format_china_datetime(dt: datetime | None = None) -> str:
    if dt is None:
        dt = now_china()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CHINA_TZ)
    else:
        dt = dt.astimezone(CHINA_TZ)
    return dt.strftime("%Y-%m-%d %H:%M")


def format_china_date(dt: datetime | None = None) -> str:
    if dt is None:
        dt = now_china()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CHINA_TZ)
    else:
        dt = dt.astimezone(CHINA_TZ)
    return dt.strftime("%Y-%m-%d")
