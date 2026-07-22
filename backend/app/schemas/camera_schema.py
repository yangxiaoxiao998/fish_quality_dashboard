"""
camera_schema.py - 摄像头相关 Pydantic 模型

仅放 BaseModel，不创建 router。
"""
from __future__ import annotations

from pydantic import BaseModel


class CameraStatusResponse(BaseModel):
    running: bool
    session_id: str | None
    current_fps: float | None
    frames_captured: int


class StartCameraRequest(BaseModel):
    camera_index: int = 0


class StartCameraResponse(BaseModel):
    session_id: str
    status: str
