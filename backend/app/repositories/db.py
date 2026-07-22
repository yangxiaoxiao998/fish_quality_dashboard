# /repositories/db.py
from __future__ import annotations

import logging
import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.settings import settings

logger = logging.getLogger("db")


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def _mask_url(url: str) -> str:
    """Mask password in DATABASE_URL for safe logging."""
    if "@" not in url or "://" not in url:
        return url
    try:
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            creds, host = rest.split("@", 1)
            if ":" in creds:
                user, _ = creds.split(":", 1)
                creds = f"{user}:***"
            return f"{scheme}://{creds}@{host}"
    except Exception:
        return url
    return url


def _current_database() -> str:
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT current_database()")).fetchone()
            if row:
                return str(row[0])
    except Exception as e:
        return f"<error: {e}>"
    return "<unknown>"


def log_db_connection() -> None:
    """在启动时打印当前数据库连接信息。"""
    print(f"[DB] DATABASE_URL={_mask_url(settings.database_url)}")
    print(f"[DB] current_database={_current_database()}")


REQUIRED_TABLES = ("fish", "video_task", "stats_5min")


def _existing_tables() -> list[str]:
    try:
        insp = inspect(engine)
        return sorted(insp.get_table_names())
    except Exception as e:
        print(f"[DB] inspect tables failed: {e}")
        return []


def init_db_schema() -> dict:
    """
    检查必需表是否存在。

    行为：
        - 如果所有必需表都存在：返回 {"created": [], "existed": [...]}
        - 如果有表缺失：调用 Base.metadata.create_all 自动建表，返回缺失表

    必须在所有 models 都被 import 后调用，否则 create_all 不知道表结构。
    """
    from app.repositories import models  # noqa: F401 强制 import 表结构

    existed = _existing_tables()
    missing = [t for t in REQUIRED_TABLES if t not in existed]

    if not missing:
        print(f"[DB] schema OK, tables={existed}")
        return {"created": [], "existed": list(existed)}

    print(f"[DB] missing tables: {missing}, creating...")
    Base.metadata.create_all(engine)
    existed_after = _existing_tables()
    created = [t for t in missing if t in existed_after]
    print(f"[DB] schema after create: tables={existed_after}, created={created}")
    return {"created": created, "existed": existed_after}


def get_db():
    """
    FastAPI 依赖注入：
    routers/repositories 之外不允许直接创建 SessionLocal（防止连接泄漏）
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
