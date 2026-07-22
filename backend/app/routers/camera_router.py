from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel
from app.schemas.camera_schema import (
    CameraStatusResponse,
    StartCameraRequest,
    StartCameraResponse,
)

from app.services.camera.camera_service import camera_service

router = APIRouter()

# class CameraStatusResponse(BaseModel):
#     running: bool
#     session_id: str | None
#     current_fps: float | None
#     frames_captured: int


# class StartCameraRequest(BaseModel):
#     camera_index: int = 0


# class StartCameraResponse(BaseModel):
#     session_id: str
#     status: str

# start_camera 接口：接收前端请求，调用 camera_service.start_camera，返回结果
@router.post("/camera/start", response_model=StartCameraResponse)
async def start_camera(request: StartCameraRequest):
    try:
        print("router start request:", request)
        result = camera_service.start_camera(camera_index=request.camera_index)
        print("router start result:", result)
        return StartCameraResponse(**result)
    except Exception as e:
        print("router start error:", repr(e))
        raise

@router.post("/camera/stop")
async def stop_camera():
    return camera_service.stop_camera()


@router.get("/camera/status", response_model=CameraStatusResponse)
async def get_camera_status():
    result = camera_service.get_camera_status()
    return CameraStatusResponse(**result)

@router.post("/camera/frame")
async def receive_frame(request: Request):
    data = await request.body()
    return camera_service.receive_frame_bytes(data)