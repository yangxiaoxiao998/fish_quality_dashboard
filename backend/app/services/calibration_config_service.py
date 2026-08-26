# 摄像头视角标定配置服务
# 提供接口：
# GET  /api/calibration/status  - 获取当前标定状态和配置  200 OK
# POST /api/calibration/save  - 保存当前标定配置  200 OK
# POST /api/calibration/clear  - 清空当前标定配置  200 OK
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.utils.timezone import format_china_datetime


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = Path(os.getenv("FISH_DATA_ROOT", str(PROJECT_ROOT / "data")))

CALIBRATION_DIR = DATA_ROOT / "config" / "calibration"
CALIBRATION_FILE = CALIBRATION_DIR / "current_camera_calibration.json"
PROJECT_MARGIN_FILE = PROJECT_ROOT / "algorithms" / "config" / "adjust_calibration_margin.json"
MARGIN_DEFAULTS = {
    "side_margin_ratio": 0.13,
    "vertical_margin_ratio": 0.05,
    "tracking_start_ratio": 0.4,
    "crop_save_ratio": 0.7,
}


def _now_str() -> str:
    return format_china_datetime()


def ensure_calibration_dir() -> None:
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)


def get_calibration_status() -> dict[str, Any]:
    ensure_calibration_dir()

    if not CALIBRATION_FILE.exists():
        return {
            "exists": False,
            "enabled": False,
            "message": "当前尚未进行摄像头视角标定",
            "config_path": str(CALIBRATION_FILE),
            "data": None,
        }

    try:
        with CALIBRATION_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        return {
            "exists": True,
            "enabled": bool(data.get("enabled", False)),
            "message": "已读取摄像头视角标定配置",
            "config_path": str(CALIBRATION_FILE),
            "data": data,
        }
    except Exception as exc:
        return {
            "exists": True,
            "enabled": False,
            "message": f"摄像头视角标定配置读取失败: {exc}",
            "config_path": str(CALIBRATION_FILE),
            "data": None,
        }


def save_calibration_config(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_calibration_dir()

    old_data: dict[str, Any] = {}
    if CALIBRATION_FILE.exists():
        try:
            with CALIBRATION_FILE.open("r", encoding="utf-8") as f:
                old_data = json.load(f)
        except Exception:
            old_data = {}

    now = _now_str()

    data = {
        "enabled": bool(payload.get("enabled", True)),
        "camera_id": payload.get("camera_id", "default"),
        "created_at": old_data.get("created_at") or now,
        "updated_at": now,
        "source_image_path": payload.get("source_image_path"),
        "belt_mask_path": payload.get("belt_mask_path"),
        "rectified_preview_path": payload.get("rectified_preview_path"),
        "transform_matrix": payload.get("transform_matrix"),
        "vertical_distance": payload.get("vertical_distance"),
        "pixel_length": payload.get("pixel_length"),
        "note": payload.get("note", "camera conveyor belt perspective calibration"),
    }

    with CALIBRATION_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return {
        "saved": True,
        "message": "摄像头视角标定配置已保存",
        "config_path": str(CALIBRATION_FILE),
        "data": data,
    }


def clear_calibration_config() -> dict[str, Any]:
    ensure_calibration_dir()

    if CALIBRATION_FILE.exists():
        CALIBRATION_FILE.unlink()

    return {
        "cleared": True,
        "exists": False,
        "enabled": False,
        "message": "摄像头视角标定配置已清除",
        "config_path": str(CALIBRATION_FILE),
    }


def load_transform_matrix() -> list[list[float]] | None:
    status = get_calibration_status()
    data = status.get("data")

    if not status.get("enabled") or not isinstance(data, dict):
        return None

    matrix = data.get("transform_matrix")
    if not matrix:
        return None

    return matrix


def get_margin_config() -> dict[str, float]:
    data = dict(MARGIN_DEFAULTS)
    try:
        if PROJECT_MARGIN_FILE.exists():
            raw = json.loads(PROJECT_MARGIN_FILE.read_text(encoding="utf-8"))
            for key in data:
                if key in raw:
                    data[key] = float(raw[key])
    except (OSError, ValueError, TypeError):
        pass
    return data


def save_margin_config(payload: dict[str, Any]) -> dict[str, float]:
    values = get_margin_config()
    for key in values:
        if key in payload:
            values[key] = float(payload[key])
    if not 0 <= values["side_margin_ratio"] <= 2 or not 0 <= values["vertical_margin_ratio"] <= 2:
        raise ValueError("边缘比例必须在 0 到 2 之间")
    if not 0 <= values["tracking_start_ratio"] < values["crop_save_ratio"] <= 1:
        raise ValueError("跟踪开始比例必须小于裁剪保存比例，且两者在 0 到 1 之间")
    PROJECT_MARGIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROJECT_MARGIN_FILE.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return values
