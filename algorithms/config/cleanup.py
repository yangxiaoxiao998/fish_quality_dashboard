"""通用清理工具。

仅允许删除 ``DATA_ROOT`` 之下（即 ``data/jobs/<video_id>/`` 等）的目录内容，
禁止触碰项目目录之外的任何位置。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from config.paths import DATA_ROOT, resolve_under_data


def _is_safe_target(path: Path) -> Path:
    """确认待清理目录位于 ``DATA_ROOT`` 内。"""

    abs_path = resolve_under_data(path)
    data_root = DATA_ROOT.resolve()
    if abs_path == data_root:
        raise ValueError(
            f"禁止直接清空数据根目录 {data_root}，请指定具体的 video_id 子目录。"
        )
    return abs_path


def remove_dir_contents(path: Path | str) -> None:
    """删除 ``path`` 下的全部内容（但保留 ``path`` 本身）。

    要求：

    - ``path`` 必须位于 ``DATA_ROOT`` 内（且不能等于 ``DATA_ROOT``）。
    - 操作前会对路径 ``resolve`` 并校验父子关系。
    - 该函数会捕获 ``FileNotFoundError``，目录不存在时不做任何事。
    """

    abs_path = _is_safe_target(Path(path))
    if not abs_path.exists():
        return
    for child in abs_path.iterdir():
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        except FileNotFoundError:
            continue


def remove_dir(path: Path | str) -> None:
    """删除整个 ``path``（包含自身）。

    与 ``remove_dir_contents`` 一样会校验路径必须在 ``DATA_ROOT`` 内。
    """

    abs_path = _is_safe_target(Path(path))
    if abs_path.exists():
        shutil.rmtree(abs_path)


__all__ = ["remove_dir_contents", "remove_dir"]