"""
video_playback_service.py

负责确保摄像头录制的视频能被浏览器 HTML5 <video> 播放。

历史问题：
    OpenCV VideoWriter 默认使用 mp4v / mpeg4 Simple Profile fourcc 保存 MP4。
    这种编码在现代浏览器（Chrome / Edge / Safari / Firefox）中通常无法播放，
    会触发：
        "No video with supported format and MIME type found."

解决思路：
    1. 保留原始 mp4v 源文件（algorithms / ffmpeg pipeline 可能仍要使用）。
    2. 同时生成 H.264 (libx264, yuv420p, +faststart) 的 playable 副本。
    3. 前端视频预览只用 playable 副本。
    4. 提供 ensure_browser_playable_video() 入口，按需懒生成。

playable 副本路径：
    data/uploads/videos/playable/<video_id>_h264.mp4
    -> /static/uploads/videos/playable/<video_id>_h264.mp4
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from app.paths import DATA_DIR
from app.services.path_service import to_abs_data_path, to_data_relative, to_static_url

logger = logging.getLogger("video_playback_service")


PLAYABLE_DIR_REL = "data/uploads/videos/playable"
PLAYABLE_DIR_ABS = DATA_DIR / "uploads" / "videos" / "playable"
PLAYABLE_SUFFIX = "_h264.mp4"

FFPROBE_BIN = shutil.which("ffprobe")
FFMPEG_BIN = shutil.which("ffmpeg")


def _ensure_playable_dir() -> Path:
    PLAYABLE_DIR_ABS.mkdir(parents=True, exist_ok=True)
    return PLAYABLE_DIR_ABS


def _candidate_source_paths(video_id: str) -> list[Path]:
    """
    列出可能的源视频路径，按优先级返回：

    1. data/uploads/videos/<video_id>.mp4
    2. data/uploads/videos/<video_id>_h264.mp4（已经是 H.264 副本）
    """
    base = DATA_DIR / "uploads" / "videos"
    return [
        base / f"{video_id}.mp4",
        base / f"{video_id}_h264.mp4",
    ]


def _resolve_source_path(video_id: str) -> Optional[Path]:
    for p in _candidate_source_paths(video_id):
        if p.exists() and p.is_file():
            return p
    return None


def _resolve_playable_path(video_id: str) -> Path:
    return PLAYABLE_DIR_ABS / f"{video_id}{PLAYABLE_SUFFIX}"


def _probe_codec(video_path: Path, timeout: float = 8.0) -> dict[str, Any]:
    """
    用 ffprobe 探测视频编码。

    返回：
        {
            "codec_name": "h264" | "mpeg4" | ...,
            "pix_fmt": "yuv420p" | ...,
            "playable_native": True | False,
        }
    """
    if not FFPROBE_BIN:
        return {
            "codec_name": None,
            "pix_fmt": None,
            "playable_native": False,
            "error_message": "服务器未安装 ffprobe。",
        }
    try:
        proc = subprocess.run(
            [
                FFPROBE_BIN,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,pix_fmt",
                "-of", "json",
                str(video_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return {
            "codec_name": None,
            "pix_fmt": None,
            "playable_native": False,
            "error_message": f"ffprobe 超时（{timeout}s）：{video_path}",
        }
    except Exception as exc:
        return {
            "codec_name": None,
            "pix_fmt": None,
            "playable_native": False,
            "error_message": f"{type(exc).__name__}: {exc}",
        }
    if proc.returncode != 0:
        return {
            "codec_name": None,
            "pix_fmt": None,
            "playable_native": False,
            "error_message": (proc.stderr or proc.stdout or "").strip()[:400],
        }
    try:
        payload = json.loads(proc.stdout or "{}")
    except Exception as exc:
        return {
            "codec_name": None,
            "pix_fmt": None,
            "playable_native": False,
            "error_message": f"ffprobe JSON parse failed: {exc}",
        }
    streams = payload.get("streams") or []
    if not streams:
        return {
            "codec_name": None,
            "pix_fmt": None,
            "playable_native": False,
            "error_message": "ffprobe 未返回视频流。",
        }
    stream = streams[0] or {}
    codec_name = (stream.get("codec_name") or "").strip().lower() or None
    pix_fmt = (stream.get("pix_fmt") or "").strip().lower() or None
    playable_native = (codec_name == "h264") and (pix_fmt == "yuv420p")
    return {
        "codec_name": codec_name,
        "pix_fmt": pix_fmt,
        "playable_native": playable_native,
    }


def _transcode_to_h264(
    source: Path,
    target: Path,
    timeout: float = 7200.0,
) -> dict[str, Any]:
    """
    用 ffmpeg 转码为 H.264 + yuv420p + +faststart。

    返回：
        {"ok": True, "output_path": "..."} 或
        {"ok": False, "error_message": "..."}
    """
    if not FFMPEG_BIN:
        return {"ok": False, "error_message": "服务器未安装 ffmpeg。"}
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [
                FFMPEG_BIN,
                "-y",
                "-i", str(source),
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-preset", "veryfast",
                "-crf", "23",
                "-movflags", "+faststart",
                "-an",
                str(target),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=True,
        )
    except subprocess.TimeoutExpired:
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass
        return {"ok": False, "error_message": f"ffmpeg 转码超时（{timeout}s）：{source}"}
    except Exception as exc:
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass
        return {"ok": False, "error_message": f"{type(exc).__name__}: {exc}"}
    if proc.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        err = (proc.stderr or proc.stdout or "").strip()[-400:]
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass
        return {
            "ok": False,
            "error_message": f"ffmpeg 转码失败（returncode={proc.returncode}）：{err}",
        }
    return {"ok": True, "output_path": str(target)}


def ensure_browser_playable_video(
    video_id: str,
    *,
    force_reconvert: bool = False,
    timeout: float = 7200.0,
) -> dict[str, Any]:
    """
    确保指定 video_id 的浏览器可播放版本存在。

    流程：
        1. 找到源视频（优先 mp4v 源，再退化到 _h264）。
        2. ffprobe 检查 codec：若 codec_name=h264 且 pix_fmt=yuv420p，则直接返回源 URL。
        3. 否则生成 data/uploads/videos/playable/<video_id>_h264.mp4：
              ffmpeg -y -i <src> -c:v libx264 -pix_fmt yuv420p -preset veryfast
                     -crf 23 -movflags +faststart -an <dst>
        4. 若 playable 已存在且 mtime >= 源 mtime，跳过转码。

    返回：
        {
            "ok": True,
            "video_id": "...",
            "source_path": "data/uploads/videos/<video_id>.mp4",
            "source_url":  "/static/uploads/videos/<video_id>.mp4",
            "playable_path": "data/uploads/videos/playable/<video_id>_h264.mp4",
            "playable_url":  "/static/uploads/videos/playable/<video_id>_h264.mp4",
            "codec_name": "mpeg4" | "h264" | ...,
            "pix_fmt": "yuv420p" | ...,
            "converted": True | False,
        }
    """
    source = _resolve_source_path(video_id)
    if source is None:
        return {
            "ok": False,
            "video_id": video_id,
            "error_code": "VIDEO_FILE_NOT_FOUND",
            "error_message": (
                f"视频文件不存在：data/uploads/videos/{video_id}.mp4"
            ),
        }

    probe = _probe_codec(source)
    codec_name = probe.get("codec_name")
    pix_fmt = probe.get("pix_fmt")

    source_rel = to_data_relative(source) or str(source)
    source_url = to_static_url(source) or f"/static/{source_rel[len('data/'):]}"

    playable_abs = _resolve_playable_path(video_id)
    playable_rel = (
        to_data_relative(playable_abs)
        or f"{PLAYABLE_DIR_REL}/{video_id}{PLAYABLE_SUFFIX}"
    )
    playable_url = (
        to_static_url(playable_abs)
        or f"/static/{PLAYABLE_DIR_REL[len('data/'):]}/{video_id}{PLAYABLE_SUFFIX}"
    )

    # 情况 A：源已经是 H.264 + yuv420p：直接返回源 URL（无需副本）。
    if probe.get("playable_native"):
        return {
            "ok": True,
            "video_id": video_id,
            "source_path": source_rel,
            "source_url": source_url,
            "playable_path": source_rel,
            "playable_url": source_url,
            "codec_name": codec_name,
            "pix_fmt": pix_fmt,
            "converted": False,
        }

    # 情况 B：playable 副本已存在且比源新（且不是强制重转）。
    if (
        not force_reconvert
        and playable_abs.exists()
        and playable_abs.stat().st_mtime >= source.stat().st_mtime
    ):
        return {
            "ok": True,
            "video_id": video_id,
            "source_path": source_rel,
            "source_url": source_url,
            "playable_path": playable_rel,
            "playable_url": playable_url,
            "codec_name": codec_name,
            "pix_fmt": pix_fmt,
            "converted": False,
            "cached": True,
        }

    # 情况 C：需要生成 playable 副本。
    _ensure_playable_dir()
    result = _transcode_to_h264(source, playable_abs, timeout=timeout)
    if not result.get("ok"):
        return {
            "ok": False,
            "video_id": video_id,
            "error_code": "TRANSCODE_FAILED",
            "error_message": result.get("error_message"),
            "source_path": source_rel,
            "source_url": source_url,
            "codec_name": codec_name,
            "pix_fmt": pix_fmt,
        }

    # 转码后再 probe 一次确认
    new_probe = _probe_codec(playable_abs)
    return {
        "ok": True,
        "video_id": video_id,
        "source_path": source_rel,
        "source_url": source_url,
        "playable_path": playable_rel,
        "playable_url": playable_url,
        "codec_name": new_probe.get("codec_name") or codec_name,
        "pix_fmt": new_probe.get("pix_fmt") or pix_fmt,
        "converted": True,
    }


def batch_ensure_browser_playable(
    video_ids: Optional[list[str]] = None,
    *,
    timeout_per_video: float = 7200.0,
) -> dict[str, Any]:
    """
    批量为指定 video_id 列表（或扫描 data/uploads/videos/ 全部）生成 playable 副本。

    返回：
        {"ok": True, "total": N, "converted": K, "skipped": S, "failed": F, "items": [...]}
    """
    if video_ids is None:
        # 扫描全部 .mp4
        if not (DATA_DIR / "uploads" / "videos").exists():
            return {"ok": True, "total": 0, "converted": 0, "skipped": 0, "failed": 0, "items": []}
        video_ids = []
        for p in sorted((DATA_DIR / "uploads" / "videos").glob("*.mp4")):
            # 只取 <video_id>.mp4，不取 <video_id>_h264.mp4
            if p.stem.endswith("_h264"):
                continue
            video_ids.append(p.stem)

    items: list[dict[str, Any]] = []
    converted = 0
    skipped = 0
    failed = 0

    for vid in video_ids:
        try:
            result = ensure_browser_playable_video(
                vid,
                force_reconvert=False,
                timeout=timeout_per_video,
            )
        except Exception as exc:
            items.append({"video_id": vid, "ok": False, "error_message": str(exc)})
            failed += 1
            continue
        if result.get("ok"):
            if result.get("converted"):
                converted += 1
            else:
                skipped += 1
        else:
            failed += 1
        items.append(result)

    return {
        "ok": True,
        "total": len(items),
        "converted": converted,
        "skipped": skipped,
        "failed": failed,
        "items": items,
    }