from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
from pathlib import Path
from typing import Any

from app.paths import VID_DIR


SEGMENT_SECONDS = max(2, int(os.getenv("FISH_CAMERA_SEGMENT_SECONDS", "10")))
SEGMENTS_ROOT = VID_DIR / "segments"


class SegmentedRecordingService:
    """Record a camera stream as short MPEG-TS files and finalize to MP4."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._video_id: str | None = None
        self._stderr_path: Path | None = None
        self._stderr_file = None

    def segment_dir(self, video_id: str) -> Path:
        return SEGMENTS_ROOT / video_id

    def start(self, video_id: str, stream_url: str) -> dict[str, Any]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                if self._video_id == video_id:
                    return self.status()
                raise RuntimeError("已有分段录制进程正在运行")
            if shutil.which("ffmpeg") is None:
                raise RuntimeError("系统未找到 ffmpeg，无法启动断电保护录制")

            segment_dir = self.segment_dir(video_id)
            segment_dir.mkdir(parents=True, exist_ok=True)
            if self._stderr_file is not None:
                try:
                    self._stderr_file.close()
                except Exception:
                    pass
            self._stderr_path = segment_dir / "ffmpeg.log"
            self._stderr_file = self._stderr_path.open("a", encoding="utf-8")
            pattern = str(segment_dir / "segment_%06d.ts")
            start_number = len(list(segment_dir.glob("segment_*.ts")))
            command = [
                "ffmpeg", "-hide_banner", "-loglevel", "warning",
                "-rw_timeout", "15000000", "-i", stream_url,
                "-map", "0:v:0", "-an", "-c:v", "copy",
                "-f", "segment", "-segment_time", str(SEGMENT_SECONDS),
                "-segment_start_number", str(start_number),
                "-segment_format", "mpegts", "-reset_timestamps", "1",
                "-segment_atclocktime", "0", "-y", pattern,
            ]
            self._process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=self._stderr_file, text=True, start_new_session=True,
            )
            self._video_id = video_id
            return self.status()

    def stop(self, timeout: float = 15.0) -> dict[str, Any]:
        with self._lock:
            process = self._process
            video_id = self._video_id
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGINT)
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        with self._lock:
            returncode = process.returncode if process is not None else None
            if self._stderr_file is not None:
                try:
                    self._stderr_file.close()
                except Exception:
                    pass
            self._stderr_file = None
            self._process = None
            self._video_id = None
        return {"ok": returncode in (None, 0, 255), "video_id": video_id, "returncode": returncode}

    def status(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            video_id = self._video_id
            running = bool(process is not None and process.poll() is None)
        segments = sorted(self.segment_dir(video_id).glob("segment_*.ts")) if video_id else []
        return {
            "running": running, "video_id": video_id,
            "pid": process.pid if running and process else None,
            "segment_seconds": SEGMENT_SECONDS, "segment_count": len(segments),
            "bytes": sum(path.stat().st_size for path in segments),
        }

    def _probe(self, path: Path) -> bool:
        if not path.is_file() or path.stat().st_size < 1880:
            return False
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_name", "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, text=True, timeout=12, check=False,
            )
            return result.returncode == 0 and bool(result.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            return False

    @staticmethod
    def _looks_like_transport_stream(path: Path) -> bool:
        try:
            if not path.is_file() or path.stat().st_size < 1880:
                return False
            with path.open("rb") as stream:
                data = stream.read(188 * 8)
            return len(data) >= 188 * 3 and all(data[index] == 0x47 for index in range(0, len(data), 188))
        except OSError:
            return False
    def valid_segments(self, video_id: str | None) -> list[Path]:
        if not video_id:
            return []
        candidates = sorted(self.segment_dir(video_id).glob("segment_*.ts"))
        return [path for path in candidates if self._looks_like_transport_stream(path)]

    def finalize(self, video_id: str, output_path: str | Path, *, cleanup: bool = True) -> dict[str, Any]:
        output = Path(output_path)
        segments = self.valid_segments(video_id)
        if not segments:
            return {"ok": False, "video_id": video_id, "error": "没有可恢复的有效视频分段"}
        output.parent.mkdir(parents=True, exist_ok=True)
        segment_dir = self.segment_dir(video_id)
        concat_file = segment_dir / "concat.txt"
        concat_file.write_text(
            "".join(f"file '{str(path).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n" for path in segments),
            encoding="utf-8",
        )
        temporary = output.with_suffix(".recovering.mp4")
        temporary.unlink(missing_ok=True)
        copy_command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "concat",
            "-safe", "0", "-i", str(concat_file), "-an", "-c:v", "copy",
            "-movflags", "+faststart", "-y", str(temporary),
        ]
        result = subprocess.run(copy_command, capture_output=True, text=True, timeout=600, check=False)
        if result.returncode != 0 or not self._probe(temporary):
            temporary.unlink(missing_ok=True)
            transcode_command = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "concat",
                "-safe", "0", "-i", str(concat_file), "-an", "-c:v", "libx264",
                "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                "-y", str(temporary),
            ]
            result = subprocess.run(transcode_command, capture_output=True, text=True, timeout=1800, check=False)
        if result.returncode != 0 or not self._probe(temporary):
            temporary.unlink(missing_ok=True)
            return {"ok": False, "video_id": video_id, "error": (result.stderr or "视频合并失败")[-1000:]}
        temporary.replace(output)
        if cleanup:
            shutil.rmtree(segment_dir, ignore_errors=True)
        return {"ok": True, "video_id": video_id, "output_path": str(output),
                "segment_count": len(segments), "bytes": output.stat().st_size}


segmented_recording_service = SegmentedRecordingService()
