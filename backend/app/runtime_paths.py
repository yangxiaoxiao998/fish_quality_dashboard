"""统一解析源码模式和 PyInstaller 冻结模式下的运行时路径。

业务数据、日志和模型必须位于安装目录（可写），不能写入 PyInstaller
的临时 ``_MEIPASS`` 目录。源码运行时则默认回到项目根目录。可通过
``FISH_*_ROOT`` 环境变量覆盖，便于把数据放在独立磁盘或做升级迁移。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


IS_FROZEN = bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def _env_path(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    return Path(value).expanduser().resolve()


def _default_install_root() -> Path:
    if IS_FROZEN:
        # onedir 布局：fish-quality-backend 位于 <release>/backend/。
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


INSTALL_ROOT = (
    _env_path("FISH_INSTALL_ROOT")
    or _env_path("FISH_PROJECT_ROOT")
    or _default_install_root()
)
BUNDLE_ROOT = Path(sys._MEIPASS).resolve() if IS_FROZEN else INSTALL_ROOT  # type: ignore[attr-defined]
RESOURCE_ROOT = _env_path("FISH_RESOURCE_ROOT") or BUNDLE_ROOT
DATA_ROOT = _env_path("FISH_DATA_ROOT") or (INSTALL_ROOT / "data")
CONFIG_ROOT = _env_path("FISH_CONFIG_ROOT") or (INSTALL_ROOT / "config")
ALGORITHMS_ROOT = _env_path("FISH_ALGO_ROOT") or (INSTALL_ROOT / "algorithms")


def resource_path(*parts: str) -> Path:
    """返回只读资源路径；业务数据不得使用此函数写入。"""

    bundled = RESOURCE_ROOT.joinpath(*parts)
    if bundled.exists():
        return bundled
    return INSTALL_ROOT.joinpath(*parts)


def settings_env_file() -> Path | None:
    explicit = _env_path("FISH_ENV_FILE")
    if explicit is not None:
        return explicit
    candidates = (
        CONFIG_ROOT / "backend.env",
        INSTALL_ROOT / ".env",
        INSTALL_ROOT / "backend" / ".env",
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


__all__ = [
    "ALGORITHMS_ROOT",
    "BUNDLE_ROOT",
    "CONFIG_ROOT",
    "DATA_ROOT",
    "INSTALL_ROOT",
    "IS_FROZEN",
    "RESOURCE_ROOT",
    "resource_path",
    "settings_env_file",
]
