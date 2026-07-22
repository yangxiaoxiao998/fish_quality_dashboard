# 作用：提供统一的北京时间工具函数
from datetime import datetime, timezone, timedelta

# 北京时区 (UTC+8)
BEIJING_TZ = timezone(timedelta(hours=8))


def now_beijing() -> datetime:
    """获取当前北京时间"""
    return datetime.now(BEIJING_TZ)


def beijing_timezone() -> timezone:
    """返回北京时间时区对象"""
    return BEIJING_TZ


def to_beijing(dt: datetime | None) -> datetime | None:
    """将 datetime 转换为北京时间 (如果有时区信息则转换，否则视为本地时间)"""
    if dt is None:
        return None

    if dt.tzinfo is None:
        # 假设输入是 UTC 时间
        return dt.replace(tzinfo=timezone.utc).astimezone(BEIJING_TZ)
    else:
        return dt.astimezone(BEIJING_TZ)
