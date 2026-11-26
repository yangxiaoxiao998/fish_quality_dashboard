"""摄像头视角标定兼容接口。

正式标定只允许从当前摄像头抓帧，旧的本地图片上传入口仅保留隐藏的
410 响应，避免旧客户端得到一个看似可用的文件上传表单。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.calibration_config_service import (
    clear_calibration_config,
    get_calibration_status,
    get_margin_config,
    save_calibration_config,
    save_margin_config,
)

router = APIRouter()


class CalibrationSaveRequest(BaseModel):
    enabled: bool = True
    camera_id: str = "default"
    source_image_path: str | None = None
    belt_mask_path: str | None = None
    rectified_preview_path: str | None = None
    transform_matrix: list[list[float]] | None = Field(default=None)
    vertical_distance: float | None = None
    pixel_length: float | None = None
    note: str | None = None


@router.get("/calibration/status")
def calibration_status() -> dict[str, Any]:
    return get_calibration_status()


@router.post("/calibration/save")
def calibration_save(payload: CalibrationSaveRequest) -> dict[str, Any]:
    return save_calibration_config(payload.model_dump())


@router.post("/calibration/clear")
def calibration_clear() -> dict[str, Any]:
    return clear_calibration_config()


@router.get("/calibration/margin-config")
def calibration_margin_config() -> dict[str, Any]:
    return {"ok": True, **get_margin_config()}


@router.post("/calibration/margin-config")
def save_calibration_margin_config(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"ok": True, **save_margin_config(payload)}
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/calibration/perspective", include_in_schema=False)
def calibration_perspective() -> dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail={
            "ok": False,
            "error_code": "LEGACY_CALIBRATION_ENDPOINT",
            "error_message": (
                "旧 perspective 标定接口已停用，请使用 "
                "/api/algorithm/calibration/capture 或 /api/algorithm/calibration。"
            ),
        },
    )
