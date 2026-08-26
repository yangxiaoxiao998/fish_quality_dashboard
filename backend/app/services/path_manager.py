"""
path_manager.py - 统一路径管理（已迁移到 vendor 流水线后保留的最小工具集）

历史背景：
- 旧抽帧设计（data/uploads/original_images/.../chunk_xxxx/）已废弃。
  当前正式项目不再使用 chunk 目录。
- 视频原始文件路径：data/uploads/videos/{video_id}.mp4（仍有效）
- 算法中间抽帧路径：data/jobs/{video_id}/frames/raw/（流水线新设计）

正式流水线：
    输入视频
    → data/jobs/{video_id}/
    → frames/raw/           # 算法抽出的原始帧
    → frames/adjusted/      # 透视矫正后的帧
    → 检测、分割、测量
    → data/results/{video_id}/

本文件当前不再被任何生产代码 import。
保留它是为了方便未来按 data/jobs/<video_id>/frames/raw/ 目录结构再次接入；
新代码请直接用 algorithms/config/paths.py 中的 video_frames_raw_dir 等。
"""
from __future__ import annotations

from pathlib import Path

CHUNK_SIZE = 2000  # 历史默认值；新流水线不再按 chunk 切分


def video_id_date_parts(
    video_id: str,
    fallback_today: bool = True,
) -> tuple[str, str, str]:
    """从 video_id 提取 year/month/day。"""
    if len(video_id) >= 8 and video_id[:8].isdigit():
        return video_id[:4], video_id[4:6], video_id[6:8]
    if fallback_today:
        from app.utils.time_utils import now_beijing
        today = now_beijing().strftime("%Y%m%d")
        return today[:4], today[4:6], today[6:8]
    raise ValueError(f"video_id has no date prefix: {video_id!r}")


def build_video_dir(base_dir: Path, video_id: str) -> Path:
    """视频文件所在目录：uploads/videos/"""
    return base_dir / "uploads" / "videos"


def build_video_path(base_dir: Path, video_id: str, suffix: str = ".mp4") -> Path:
    """视频文件路径：uploads/videos/{video_id}{suffix}"""
    return build_video_dir(base_dir, video_id) / f"{video_id}{suffix}"


def path_to_data_relative(abs_path: str | Path, data_dir: Path) -> str | None:
    """
    将绝对路径转换为相对 DATA_DIR 的路径，用于入库。

    如果不在 DATA_DIR 下，返回 None。
    """
    p = Path(abs_path)
    if not p.is_absolute():
        return p.as_posix()
    try:
        return str(p.relative_to(data_dir).as_posix())
    except ValueError:
        return None
