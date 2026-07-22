# todo
'''
1.传zhuzhen图片
2.生成 视频  保留临时文件
'''

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import cv2
import numpy as np
from fastapi import HTTPException


@dataclass
class CameraRuntimeState:
    running: bool = False
    current_session_id: str | None = None
    camera_index: int = 0
    started_at: datetime | None = None
    frames_received: int = 0


class CameraService:
    def __init__(self) -> None:
        self.state = CameraRuntimeState()

    def start_camera(self, camera_index: int = 0) -> dict[str, Any]:
        if self.state.running:
            raise HTTPException(status_code=400, detail="Camera already running")

        session_id = f"cam_{uuid.uuid4().hex[:8]}"
        self.state.camera_index = camera_index
        self.state.running = True
        self.state.current_session_id = session_id
        self.state.started_at = datetime.now()
        self.state.frames_received = 0

        return {
            "session_id": session_id,
            "status": "started",
        }

    def stop_camera(self) -> dict[str, Any]:
        if not self.state.running:
            raise HTTPException(status_code=400, detail="Camera not running")

        session_id = self.state.current_session_id
        self.state.running = False
        self.state.current_session_id = None
        self.state.started_at = None
        self.state.frames_received = 0

        return {
            "session_id": session_id,
            "status": "stopped",
        }

    def get_camera_status(self) -> dict[str, Any]:
        current_fps: float | None = None

        if self.state.running and self.state.started_at is not None:
            elapsed = (datetime.now() - self.state.started_at).total_seconds()
            if elapsed > 0:
                current_fps = self.state.frames_received / elapsed

        return {
            "running": self.state.running,
            "session_id": self.state.current_session_id,
            "current_fps": current_fps,
            "frames_captured": self.state.frames_received,
        }

    def receive_frame_bytes(self, data: bytes) -> dict[str, Any]:
        if not self.state.running:
            raise HTTPException(status_code=400, detail="Camera not running")

        if not data:
            raise HTTPException(status_code=400, detail="empty frame")

        np_buffer = np.frombuffer(data, dtype=np.uint8)
        frame = cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)
        if frame is None:
            raise HTTPException(status_code=400, detail="invalid image bytes")

        height, width = frame.shape[:2]

        self.state.frames_received += 1

        print(
            f"[camera] received frame #{self.state.frames_received}, "
            f"size={len(data)} bytes, shape=({width}, {height})"
        )

        return {
            "status": "received",
            "session_id": self.state.current_session_id,
            "size": len(data),
            "width": width,
            "height": height,
            "frames_captured": self.state.frames_received,
            "timestamp": datetime.now().isoformat(),
        }


camera_service = CameraService()