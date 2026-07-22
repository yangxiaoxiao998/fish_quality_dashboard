"""
path_service.py - 统一路径管理服务

所有路径与静态 URL 的转换集中在此处，避免散落在各处。

约定：
    - 数据库中只保存相对于 DATA_DIR 的相对路径（posix 格式）。
    - 前端访问图片/视频统一使用 /static/<rel_path> 形式的 URL。
    - 本地文件系统读取使用 DATA_DIR / rel_path 或调用 to_abs_data_path()。

DATA_DIR = /home/.../fish_quality_dashboard/data
"""
from __future__ import annotations

from pathlib import Path

from app.paths import DATA_DIR, PROJECT_ROOT


def to_data_relative(path: str | Path | None) -> str | None:
    """
    将任意路径转换为相对于 DATA_DIR 的相对路径（posix 格式）。

    输入支持：
        1. None                             -> None
        2. 已经是相对路径                   -> 原样规范化为 posix 格式
        3. DATA_DIR 下的绝对路径            -> 去掉 DATA_DIR 前缀
        4. 不属于 DATA_DIR 的绝对路径       -> None

    返回：
        posix 格式的相对路径字符串，或 None。
    """
    if path is None:
        return None

    if isinstance(path, str) and path == "":
        return None

    p = Path(path)

    if p.is_absolute():
        try:
            rel = p.resolve().relative_to(DATA_DIR.resolve())
            return rel.as_posix()
        except ValueError:
            return None

    s = p.as_posix().lstrip("/")
    return s if s else None


def to_abs_data_path(path: str | Path | None) -> Path | None:
    """
    将数据库中的相对路径转换为 DATA_DIR 下的绝对路径。

    输入：
        1. None                            -> None
        2. 相对路径                        -> DATA_DIR / path
        3. DATA_DIR 下的绝对路径           -> 原样返回（resolve）
        4. 不属于 DATA_DIR 的绝对路径      -> None
    """
    if path is None:
        return None

    if isinstance(path, str) and path == "":
        return None

    p = Path(path)

    if p.is_absolute():
        try:
            p.resolve().relative_to(DATA_DIR.resolve())
            return p
        except ValueError:
            return None

    s = p.as_posix().lstrip("/")
    if not s:
        return None
    return DATA_DIR / s


def resolve_data_path(value: str | Path | None) -> Path | None:
    """统一解析多种形态的图片/标定路径到 DATA_DIR 下的本地绝对路径。

    支持：
        1. ``/static/uploads/...``        → ``DATA_DIR / uploads/...``
        2. ``data/uploads/...``           → ``DATA_DIR / uploads/...``
        3. ``/data/uploads/...``          → ``DATA_DIR / uploads/...``
        4. ``uploads/...``                → ``DATA_DIR / uploads/...``（不再解析到 PROJECT_ROOT/uploads/...）
        5. ``/uploads/...``               → ``DATA_DIR / uploads/...``
        6. ``results/...``                → ``DATA_DIR / results/...``
        7. 绝对路径                       → 校验是否在 DATA_DIR 下，否者回退到 ``DATA_DIR / uploads/...``
        8. 已经是 ``DATA_DIR`` 下的绝对路径 → 原样返回
    """
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None

    s = str(value).strip()

    # 1) /static/<rel>  →  DATA_DIR / <rel>  (剥掉 data/ 前缀，因为 StaticFiles 根目录就是 DATA_DIR)
    if s.startswith("/static/"):
        rel = s[len("/static/"):].lstrip("/")
        if rel.startswith("data/"):
            rel = rel[len("data/"):]
        return DATA_DIR / rel

    # 2) data/<rel>  →  DATA_DIR / <rel>
    if s.startswith("data/"):
        return DATA_DIR / s[len("data/"):]

    # 3) /data/<rel>  →  DATA_DIR / <rel>
    if s.startswith("/data/"):
        return DATA_DIR / s[len("/data/"):]

    # 4) uploads/...  / /uploads/...  →  DATA_DIR / uploads/...
    if s.startswith("uploads/") or s.startswith("/uploads/"):
        rel = s.lstrip("/")
        return DATA_DIR / rel

    # 5) results/...  / /results/...  →  DATA_DIR / results/...
    if s.startswith("results/") or s.startswith("/results/"):
        rel = s.lstrip("/")
        return DATA_DIR / rel

    # 6) 绝对路径：
    p = Path(s)
    if p.is_absolute():
        try:
            # 已经是 DATA_DIR 下的绝对路径 → 原样返回
            p.resolve().relative_to(DATA_DIR.resolve())
            return p
        except ValueError:
            # 不在 DATA_DIR 下，且路径形如 .../uploads/... → 自动补 data/
            # （处理旧数据曾把 uploads/ 写到 PROJECT_ROOT 下而非 DATA_ROOT 下的情况）
            # 兼容：/home/.../uploads/.../x.jpg 也尝试 /home/.../data/uploads/.../x.jpg
            normalized = s.replace("\\", "/")
            parts = normalized.split("/uploads/", 1)
            if len(parts) == 2:
                alt = parts[0] + "/data/uploads/" + parts[1]
                alt_path = Path(alt)
                if alt_path.exists():
                    return alt_path
            return p

    # 7) 其它相对路径：默认按 PROJECT_ROOT 处理
    return PROJECT_ROOT / s


def to_static_url(path: str | Path | None) -> str | None:
    """
    将数据库路径转换为前端可访问的静态 URL。

    约定：
        - 后端 StaticFiles 挂载在 /static，对应 DATA_DIR。
        - 数据库字段保存 POSIX 路径，例如 data/results/<video_id>/fish_images/.../x.jpg
        - 前端 URL 必须是 /static/results/<video_id>/... 而不是 /static/data/results/.../
        - 本函数会自动去除开头的 "data/" 前缀。

    返回：
        /static/<relative_path>
    """
    if path is None:
        return None

    if isinstance(path, str) and path == "":
        return None

    p = Path(path)

    if p.is_absolute():
        try:
            rel = p.resolve().relative_to(DATA_DIR.resolve()).as_posix()
        except ValueError:
            return None
    else:
        rel = p.as_posix().lstrip("/")

    if rel.startswith("data/"):
        rel = rel[len("data/"):]

    rel = rel.lstrip("/")
    if not rel:
        return None
    return f"/static/{rel}"
