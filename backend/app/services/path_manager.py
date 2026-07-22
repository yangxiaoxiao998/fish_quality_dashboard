"""
path_manager.py - 统一路径管理

所有涉及路径构建的逻辑集中于此，避免散落在各处。

目录结构：
    - 抽帧：  data/uploads/original_images/{year}/{month}/{day}/{video_id}/chunk_xxxx/{frame_index:08d}.jpg
    - 透视：  data/results/perspective_images/{year}/{month}/{day}/{video_id}/chunk_xxxx/{frame_index:08d}_perspective.jpg
    - 裁剪：  data/results/crop_images/{year}/{month}/{day}/{video_id}/chunk_xxxx/{fish_id}_crop.jpg
    - 矫正：  data/results/corrected_images/{year}/{month}/{day}/{video_id}/chunk_xxxx/{fish_id}_corrected.jpg
    - 视频：  data/uploads/videos/{video_id}.mp4

chunk_size = 2000，确保单目录文件数可控。

入库路径：统一为相对 DATA_DIR 的路径，例如 "results/crop_images/2026/05/24/..."

video_id 兼容：
    - 新格式 YYYYMMDD_NNNN_xxxxxxxxxxxx
    - 旧格式 YYYYMMDD_xxxxxxxxxxxx（前 8 位是日期）
    - 调试格式 test_* / debug_*（fallback_today 用今日日期）
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Literal

CHUNK_SIZE = 2000


def video_id_date_parts(
    video_id: str,
    fallback_today: bool = True,
) -> tuple[str, str, str]:
    """
    从 video_id 提取 year/month/day。

    优先看前 8 位是否是数字。
    - 是：返回该日期。
    - 否：fallback 到今日北京时间。
    """
    if len(video_id) >= 8 and video_id[:8].isdigit():
        return video_id[:4], video_id[4:6], video_id[6:8]
    if fallback_today:
        from app.utils.time_utils import now_beijing
        today = now_beijing().strftime("%Y%m%d")
        warnings.warn(
            f"video_id {video_id!r} has no date prefix, "
            f"falling back to today {today}",
            RuntimeWarning,
            stacklevel=2,
        )
        return today[:4], today[4:6], today[6:8]
    raise ValueError(f"video_id has no date prefix: {video_id!r}")


def build_video_dir(base_dir: Path, video_id: str) -> Path:
    """视频文件所在目录：uploads/videos/"""
    return base_dir / "uploads" / "videos"


def build_video_path(base_dir: Path, video_id: str, suffix: str = ".mp4") -> Path:
    """视频文件路径：uploads/videos/{video_id}{suffix}"""
    return build_video_dir(base_dir, video_id) / f"{video_id}{suffix}"


def build_frame_dir(base_dir: Path, video_id: str) -> Path:
    """抽帧目录：uploads/original_images/{year}/{month}/{day}/{video_id}/"""
    y, m, d = video_id_date_parts(video_id)
    return base_dir / "uploads" / "original_images" / y / m / d / video_id


def build_frame_output_path(
    video_id: str,
    frame_index: int,
    base_dir: Path | str,
    chunk_size: int = CHUNK_SIZE,
) -> Path:
    """
    抽帧图片路径。

    结构：uploads/original_images/{y}/{m}/{d}/{video_id}/chunk_{chunk_index:04d}/{frame_index:08d}.jpg
    例如：uploads/original_images/2026/05/24/20260524_xxx/chunk_0000/00000001.jpg
    """
    base = Path(base_dir)
    y, m, d = video_id_date_parts(video_id)
    chunk_index = frame_index // chunk_size
    chunk_name = f"chunk_{chunk_index:04d}"
    frame_name = f"{frame_index:08d}.jpg"

    frame_dir = base / "uploads" / "original_images" / y / m / d / video_id / chunk_name
    frame_dir.mkdir(parents=True, exist_ok=True)
    return frame_dir / frame_name


def build_perspective_frame_output_path(
    video_id: str,
    frame_index: int,
    base_dir: Path | str,
    chunk_size: int = CHUNK_SIZE,
) -> Path:
    """
    透视矫正后的抽帧图片路径（与原始帧一一对应，但单独保存）。

    结构：results/perspective_images/{y}/{m}/{d}/{video_id}/chunk_{chunk_index:04d}/{frame_index:08d}_perspective.jpg
    例如：results/perspective_images/2026/05/24/20260524_xxx/chunk_0000/00000001_perspective.jpg

    注意：
        - frame_index 编号规则与 build_frame_output_path 完全一致（同样的 1-based 顺序）。
        - chunk_index = frame_index // chunk_size，与原始帧共用同一套 chunk 切分。
        - 不会覆盖 uploads/original_images 下的原始帧。
    """
    base = Path(base_dir)
    y, m, d = video_id_date_parts(video_id)
    chunk_index = frame_index // chunk_size
    chunk_name = f"chunk_{chunk_index:04d}"
    frame_name = f"{frame_index:08d}_perspective.jpg"

    perspective_dir = (
        base / "results" / "perspective_images" / y / m / d / video_id / chunk_name
    )
    perspective_dir.mkdir(parents=True, exist_ok=True)
    return perspective_dir / frame_name


def build_crop_output_path(
    video_id: str,
    fish_id: str,
    frame_index: int,
    base_dir: Path | str,
    chunk_size: int = CHUNK_SIZE,
) -> Path:
    """
    鱼体裁剪图路径。

    结构：results/crop_images/{y}/{m}/{d}/{video_id}/chunk_{chunk_index:04d}/{fish_id}_crop.jpg
    """
    base = Path(base_dir)
    y, m, d = video_id_date_parts(video_id)
    chunk_index = frame_index // chunk_size
    chunk_name = f"chunk_{chunk_index:04d}"

    crop_dir = base / "results" / "crop_images" / y / m / d / video_id / chunk_name
    crop_dir.mkdir(parents=True, exist_ok=True)
    return crop_dir / f"{fish_id}_crop.png"


def build_corrected_output_path(
    video_id: str,
    fish_id: str,
    frame_index: int,
    base_dir: Path | str,
    chunk_size: int = CHUNK_SIZE,
) -> Path:
    """
    鱼体矫正图路径。

    结构：results/corrected_images/{y}/{m}/{d}/{video_id}/chunk_{chunk_index:04d}/{fish_id}_corrected.jpg
    """
    base = Path(base_dir)
    y, m, d = video_id_date_parts(video_id)
    chunk_index = frame_index // chunk_size
    chunk_name = f"chunk_{chunk_index:04d}"

    corrected_dir = base / "results" / "corrected_images" / y / m / d / video_id / chunk_name
    corrected_dir.mkdir(parents=True, exist_ok=True)
    return corrected_dir / f"{fish_id}_corrected.jpg"


def path_to_data_relative(abs_path: str | Path, data_dir: Path) -> str | None:
    """
    将绝对路径转换为相对 DATA_DIR 的路径，用于入库。

    例如：
        /home/.../data/results/crop_images/2026/05/24/xxx/chunk_0000/fish_crop.jpg
        -> results/crop_images/2026/05/24/xxx/chunk_0000/fish_crop.jpg

    如果不在 DATA_DIR 下，返回 None。
    """
    p = Path(abs_path)
    if not p.is_absolute():
        return p.as_posix()
    try:
        return str(p.relative_to(data_dir).as_posix())
    except ValueError:
        return None

