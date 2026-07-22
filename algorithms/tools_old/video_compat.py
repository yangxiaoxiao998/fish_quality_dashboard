"""
video_compat.py

浏览器兼容 MP4 工具。

cv2.VideoWriter + fourcc(*"mp4v") 写出的 mp4 通常是 mpeg4 / Simple Profile，
Firefox / Chrome 等浏览器在 <video controls> 中报：

    No video with supported format and MIMEtype found.

解决方法：让 ffmpeg 把 mpeg4 转码成 H.264 / yuv420p。
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


BROWSER_FRIENDLY_CODEC = "h264"
BROWSER_FRIENDLY_PIX_FMT = "yuv420p"


def _safe_run(cmd: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    """subprocess.run wrapper, capture stdout/stderr text, never raise on non-zero."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def probe_video(video_path: Path) -> dict[str, Any]:
    """ffprobe -v error -show_streams / -show_format 返回纯文本结果。

    返回:
        {
          "v_streams": [{ codec_name, profile, pix_fmt, width, height, r_frame_rate }],
          "format_name": "mov,mp4,...",
        }

    ffprobe 不存在 / 文件损坏时返回空 dict。
    """
    if not video_path.exists() or not video_path.is_file():
        return {"v_streams": [], "format_name": ""}

    p = _safe_run(
        [
            "ffprobe",
            "-v", "error",
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            str(video_path),
        ],
    )
    if p.returncode != 0:
        logger.warning("[video_compat] ffprobe failed for %s: %s", video_path, p.stderr.strip())
        return {"v_streams": [], "format_name": ""}
    try:
        data = json.loads(p.stdout)
    except Exception as exc:
        logger.warning("[video_compat] ffprobe json parse failed for %s: %s", video_path, exc)
        return {"v_streams": [], "format_name": ""}
    streams = data.get("streams", [])
    v_streams = [
        {
            "codec_name": s.get("codec_name"),
            "profile": s.get("profile"),
            "pix_fmt": s.get("pix_fmt"),
            "width": s.get("width"),
            "height": s.get("height"),
            "r_frame_rate": s.get("r_frame_rate"),
        }
        for s in streams
        if s.get("codec_type") == "video"
    ]
    fmt = data.get("format", {})
    return {"v_streams": v_streams, "format_name": fmt.get("format_name", "")}


def is_browser_compatible(video_path: Path) -> bool:
    """只要首条视频流是 h264 + yuv420p 即视为浏览器兼容。

    注意：保留 moov atom 靠 +faststart；这一条件由 ensure_browser_compatible_mp4
    在转码时保证。
    """
    info = probe_video(video_path)
    if not info["v_streams"]:
        return False
    v0 = info["v_streams"][0]
    return (
        v0.get("codec_name") == BROWSER_FRIENDLY_CODEC
        and v0.get("pix_fmt") == BROWSER_FRIENDLY_PIX_FMT
    )


def transcode_to_h264(
    src: Path,
    dst: Path,
    *,
    crf: int = 23,
    preset: str = "veryfast",
) -> bool:
    """ffmpeg 把 src 转成 H.264 / yuv420p / High / level 4.1 / +faststart / 无音轨。

    返回 True 表示成功。
    """
    src = Path(src)
    dst = Path(dst)
    if not src.exists():
        logger.warning("[video_compat] transcode skipped, missing: %s", src)
        return False

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-level",
        "4.1",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-movflags",
        "+faststart",
        "-an",
        str(dst),
    ]
    p = _safe_run(cmd, timeout=1800)
    if p.returncode != 0:
        logger.warning(
            "[video_compat] ffmpeg transcode failed for %s: %s", src, p.stderr.strip()[-500:]
        )
        return False
    return dst.exists() and dst.stat().st_size > 0


def ensure_browser_compatible_mp4(
    video_path: Path | str,
    *,
    backup_suffix: str = "_original",
) -> bool:
    """
    确保 video_path 是一个浏览器可播放的 mp4 (H.264 / yuv420p / +faststart)。

    逻辑：
    1. 如果当前已经是 h264 + yuv420p，直接返回 True，不动原文件。
    2. 否则把 video_path 备份为 `<stem>{backup_suffix}<ext>` (默认 *_original.mp4)。
    3. 用 ffmpeg 转码 src -> 临时文件，验证转码结果。
    4. 验证通过后用临时文件替换原 video_path。
    5. 转码失败：保留原文件，return False，调用方自行记录警告。

    返回 True 表示最后 video_path 是兼容的；False 表示保持原状未做修改。
    """
    video_path = Path(video_path)
    if not video_path.exists() or not video_path.is_file():
        logger.warning("[video_compat] missing: %s", video_path)
        return False

    if is_browser_compatible(video_path):
        return True

    backup = video_path.with_name(
        f"{video_path.stem}{backup_suffix}{video_path.suffix}"
    )
    try:
        if not backup.exists():
            shutil.copy2(video_path, backup)
    except Exception as exc:
        logger.warning(
            "[video_compat] backup %s -> %s failed: %s", video_path, backup, exc
        )

    # ffmpeg 在某些版本上不能正确处理 "x.mp4.tmp" 这种 multi-dot 路径，
    # 我们改成 "x_h264.tmp.mp4"。
    tmp_out = video_path.with_name(
        f"{video_path.stem}_h264_tmp{video_path.suffix}"
    )
    ok = transcode_to_h264(video_path, tmp_out)

    if not ok or not is_browser_compatible(tmp_out):
        # 转码失败，回滚保留原文件，清理临时文件
        try:
            if tmp_out.exists():
                tmp_out.unlink()
        except Exception:
            pass
        logger.warning(
            "[video_compat] transcode failed for %s; original kept", video_path
        )
        return False

    # 用转码后的临时文件替换原文件
    try:
        shutil.move(str(tmp_out), str(video_path))
    except Exception as exc:
        logger.warning(
            "[video_compat] move %s -> %s failed: %s", tmp_out, video_path, exc
        )
        return False

    logger.info(
        "[video_compat] transcoded %s (backup=%s) to %s/%s",
        video_path.name,
        backup.name,
        BROWSER_FRIENDLY_CODEC,
        BROWSER_FRIENDLY_PIX_FMT,
    )
    return True
