"""统一的帧与文件命名工具。

规则：

- 帧编号采用 12 位补零，例如 ``000000000001.jpg``。
- 标注帧图：``000000000001_grounded_sam.jpg``。
- 单鱼截图：``id_<track_id>_000000000001_crop.jpg``。
- 单鱼掩码：``id_<track_id>_000000000001_mask_crop_bw.png``。

读取文件时同时兼容 6 位和 12 位两种命名，但新生成的文件必须使用 12 位。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, NamedTuple


FRAME_INDEX_WIDTH: int = 12

FRAME_SUFFIX: str = ".jpg"
ANNOTATED_SUFFIX: str = "_grounded_sam.jpg"
FISH_CROP_SUFFIX: str = "_crop.jpg"
FISH_MASK_SUFFIX: str = "_mask_crop_bw.png"

_FRAME_INDEX_RE = re.compile(r"^0*(\d+)$")
_FISH_NAME_RE = re.compile(
    r"^id_(\d+)_(\d+)_(?:crop|mask_crop_bw)\.(?:jpg|png)$"
)


def format_frame_index(frame_index: int) -> str:
    """将 ``frame_index`` 格式化为宽度为 ``FRAME_INDEX_WIDTH`` 的字符串。"""

    if frame_index < 0:
        raise ValueError(f"frame_index 必须为非负整数: {frame_index}")
    return f"{int(frame_index):0{FRAME_INDEX_WIDTH}d}"


def make_frame_name(frame_index: int, suffix: str = FRAME_SUFFIX) -> str:
    return f"{format_frame_index(frame_index)}{suffix}"


def make_annotated_frame_name(frame_index: int) -> str:
    return f"{format_frame_index(frame_index)}{ANNOTATED_SUFFIX}"


def make_fish_crop_name(track_id: int, frame_index: int) -> str:
    if track_id is None:
        raise ValueError("track_id 不能为空")
    return f"id_{int(track_id)}_{format_frame_index(frame_index)}{FISH_CROP_SUFFIX}"


def make_fish_mask_name(track_id: int, frame_index: int) -> str:
    if track_id is None:
        raise ValueError("track_id 不能为空")
    return f"id_{int(track_id)}_{format_frame_index(frame_index)}{FISH_MASK_SUFFIX}"


def parse_frame_index(stem: str) -> int | None:
    """从纯数字 stem 中解析帧编号。

    兼容 ``"1"``、``"000001"``、``"000000000001"`` 等写法。
    """

    if not stem:
        return None
    match = _FRAME_INDEX_RE.match(stem)
    if match is None:
        return None
    return int(match.group(1))


class FishFileInfo(NamedTuple):
    track_id: int
    frame_index: int
    kind: str  # "crop" or "mask"


def parse_fish_file_name(name: str) -> FishFileInfo | None:
    """从 ``id_<track_id>_<frame>_crop.jpg`` 或 ``id_<...>_mask_crop_bw.png`` 中解析。

    接受 6 位或 12 位的 frame 字段；返回 None 表示不匹配。
    """

    if not isinstance(name, str):
        return None
    base = Path(name).name
    match = _FISH_NAME_RE.match(base)
    if match is None:
        return None
    track_id = int(match.group(1))
    frame_index = parse_frame_index(match.group(2))
    if frame_index is None:
        return None
    if base.endswith(FISH_CROP_SUFFIX):
        kind = "crop"
    elif base.endswith(FISH_MASK_SUFFIX):
        kind = "mask"
    else:
        return None
    return FishFileInfo(track_id=track_id, frame_index=frame_index, kind=kind)


def extract_frame_index_from_path(path: Path | str) -> int | None:
    """从图片文件名中尝试解析帧编号。

    支持 ``000000000001.jpg``、``id_0_000000000001_crop.jpg``、
    ``000000000001_grounded_sam.jpg`` 等形式。
    """

    p = Path(path)
    stem = p.stem
    info = parse_fish_file_name(p.name)
    if info is not None:
        return info.frame_index
    direct = parse_frame_index(stem)
    if direct is not None:
        return direct
    if ANNOTATED_SUFFIX and stem.endswith(ANNOTATED_SUFFIX.rsplit(".", 1)[0]):
        candidate = stem[: -len(ANNOTATED_SUFFIX.rsplit(".", 1)[0])]
        return parse_frame_index(candidate)
    return None


def iter_matching_files(directory: Path, suffixes: Iterable[str]):
    """遍历目录中以 ``suffixes`` 结尾的文件。"""

    suffixes = tuple(s.lower() for s in suffixes)
    if not directory.exists():
        return
    for entry in sorted(directory.iterdir()):
        if entry.is_file() and entry.suffix.lower() in suffixes:
            yield entry


def find_max_frame_index(directory: Path, suffixes: Iterable[str] = (".jpg", ".jpeg", ".png")) -> int:
    """扫描 ``directory`` 中已有命名规范的帧图，返回最大的帧编号（找不到则 0）。

    支持 6 位与 12 位命名混存场景；只统计 ``suffixes`` 指定的扩展名。
    """

    max_index = 0
    for entry in iter_matching_files(directory, suffixes):
        idx = parse_frame_index(entry.stem)
        if idx is None:
            continue
        if idx > max_index:
            max_index = idx
    return max_index


def iter_input_frames(
    directory: Path,
    suffixes: Iterable[str] = (".jpg", ".jpeg", ".png"),
):
    """按数值帧号升序遍历输入帧图（兼容 6 位 / 12 位）。"""

    if not directory.exists():
        return
    items: list[tuple[int, str]] = []
    for entry in iter_matching_files(directory, suffixes):
        idx = parse_frame_index(entry.stem)
        if idx is None:
            continue
        items.append((idx, entry.name))
    items.sort(key=lambda x: x[0])
    for _, name in items:
        yield directory / name


__all__ = [
    "FRAME_INDEX_WIDTH",
    "FRAME_SUFFIX",
    "ANNOTATED_SUFFIX",
    "FISH_CROP_SUFFIX",
    "FISH_MASK_SUFFIX",
    "FishFileInfo",
    "format_frame_index",
    "make_frame_name",
    "make_annotated_frame_name",
    "make_fish_crop_name",
    "make_fish_mask_name",
    "parse_frame_index",
    "parse_fish_file_name",
    "extract_frame_index_from_path",
    "iter_matching_files",
    "find_max_frame_index",
    "iter_input_frames",
]