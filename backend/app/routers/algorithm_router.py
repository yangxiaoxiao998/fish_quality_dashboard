"""
algorithm_router.py - 算法 CLI 对接接口

提供 /api/algorithm/* 接口：
- POST /api/algorithm/calibration
- GET  /api/algorithm/calibration/status
- POST /api/algorithm/videos
- GET  /api/algorithm/videos/{video_id}/result
- GET  /api/algorithm/videos/{video_id}/error-conclusion
- POST /api/algorithm/import-result/{video_id}

所有真实算法执行都委托给
- app.services.algorithm_cli_service
- app.services.algorithm_result_service
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.repositories.db import SessionLocal, get_db
from app.repositories import session_repo
from app.services import algorithm_cli_service, algorithm_result_service
from app.paths import DATA_DIR
from app.services import algorithm_calibration_capture_service
from app.services.video_report_pdf_service import generate_video_pdf_report

router = APIRouter()
logger = logging.getLogger("algorithm_router")
CALIBRATION_CONFIG_JSON = DATA_DIR / "calibration" / "calibration_config.json"


class CalibrationConfigRequest(BaseModel):
    real_length_mm: float = Field(..., gt=0)


def _read_calibration_config() -> dict[str, Any]:
    if not CALIBRATION_CONFIG_JSON.exists():
        return {"real_length_mm": 1376.0}
    try:
        data = json.loads(CALIBRATION_CONFIG_JSON.read_text(encoding="utf-8"))
        return {"real_length_mm": float(data.get("real_length_mm", 1376.0))}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"real_length_mm": 1376.0}


# ---------------------------------------------------------------------------
# Request / Response Schemas
# ---------------------------------------------------------------------------

class CalibrationRequest(BaseModel):
    source_image: str = Field(..., description="标定图片绝对路径或相对项目根路径")
    camera_id: str = "default"
    device: str = "cuda"
    overwrite: bool = True
    real_length_mm: Optional[float] = Field(None, gt=0)


class CaptureCalibrationRequest(BaseModel):
    camera_id: str = "default"
    device: str = "cuda"
    overwrite: bool = True
    save_only: bool = False

    # 新增：复用"摄像头输入"按钮当前配置
    stream_url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[int] = None

    # 新增：标定拍摄后自动停止摄像头（不污染视频历史）
    stop_after_capture: bool = True
    real_length_mm: Optional[float] = Field(None, gt=0)


class BatchImportResultsRequest(BaseModel):
    scan_all: bool = True
    video_ids: list[str] = []
    overwrite: bool = True


class VideoPipelineRequest(BaseModel):
    input_video: str = Field(..., description="输入视频绝对路径或相对项目根路径")
    camera_id: str = "default"
    device: str = "cuda"
    video_fps: int = 25
    overwrite: bool = True
    import_to_db: bool = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _path_for_response(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value)
    return s if s else None


def _strip_path_summary(data: dict[str, Any]) -> dict[str, Any]:
    """去掉返回里包含绝对路径的字段，避免暴露给前端。"""
    blocked = {"original_input_video", "input_video", "absolute_paths"}
    return {k: v for k, v in data.items() if k not in blocked}


# ---------------------------------------------------------------------------
# 5.1 POST /api/algorithm/calibration
# ---------------------------------------------------------------------------

@router.post("/algorithm/calibration")
def post_calibration(payload: CalibrationRequest) -> dict:
    """
    调用算法工程师提供的 adjust_calibration CLI。

    真实命令：
        python -m algorithms.core.adjust_calibration \
            --source-image <path> --camera-id default \
            --device cuda --overwrite

    输出：data/calibration/current_calibration.json
    """
    result = algorithm_cli_service.run_calibration_cli(
        source_image=payload.source_image,
        camera_id=payload.camera_id,
        device=payload.device,
        overwrite=payload.overwrite,
        real_length_mm=payload.real_length_mm,
    )

    if result.get("ok"):
        response: dict[str, Any] = {
            "ok": True,
            "calibration_json": _path_for_response(result.get("calibration_json")),
            "message": "标定完成",
            "returncode": result.get("returncode"),
            "log_path": _path_for_response(result.get("log_path")),
        }
        # 算法成功 → 同步把最新 current_calibration.json 内容也回填，
        # 避免前端还要再请求一次 status。
        try:
            status_payload = _build_calibration_status_payload()
            if status_payload.get("data"):
                response["data"] = status_payload["data"]
                response["status_payload"] = status_payload
        except Exception as exc:
            logger.warning("merge calibration status failed: %s", exc)
        return response

    raise HTTPException(
        status_code=500,
        detail={
            "ok": False,
            "error_message": _path_for_response(result.get("error_message")) or "标定失败",
            "warning_lines": result.get("warning_lines", 0) or 0,
            "runner_error": _path_for_response(result.get("runner_error")),
            "log_path": _path_for_response(result.get("log_path")),
            "returncode": result.get("returncode"),
        },
    )


# ---------------------------------------------------------------------------
# 5.6 GET /api/algorithm/calibration/status
# ---------------------------------------------------------------------------

@router.post("/algorithm/calibration/capture")
def post_capture_and_calibrate(payload: CaptureCalibrationRequest) -> dict:
    """
    从摄像头当前最新帧拍摄一张 JPEG，调用算法标定 CLI。

    真实流程：
        1. camera_service_yzt.get_latest_frame()
        2. 保存到 data/uploads/calibration/YYYY/MM/DD/
        3. python -m algorithms.core.adjust_calibration
        4. 生成 data/calibration/current_calibration.json
        5. 扫描 data/calibration/sessions/<id>/ 找矫正预览图

    返回字段包含：
        - source_image_url / source_image_path
        - calibration_json / calibration_url
        - adjusted_preview_url / adjusted_preview_path
        - log_path / returncode
    """
    from app.services.camera_yzt.camera_yzt_service import camera_service_yzt

    camera_settings = camera_service_yzt.get_saved_settings()
    result = algorithm_calibration_capture_service.run_capture_and_calibrate(
        camera_id=payload.camera_id,
        device=payload.device,
        overwrite=payload.overwrite,
        save_only=payload.save_only,
        stream_url=payload.stream_url,
        width=payload.width or camera_settings["width"],
        height=payload.height or camera_settings["height"],
        fps=payload.fps or camera_settings["fps"],
        stop_after_capture=payload.stop_after_capture,
        real_length_mm=payload.real_length_mm,
    )

    if result.get("error_message") and not result.get("calibrated") and not result.get("saved"):
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "saved": False,
                "calibrated": False,
                "error_code": result.get("error_code") or "CALIBRATION_CAPTURE_FAILED",
                "error_message": result.get("error_message")
                or "摄像头画面获取失败，请稍后重试。",
            },
        )

    # 视觉矫正执行成功后，把 status 接口的最新完整数据也合并进返回结果，
    # 避免前端只能拿到一个空 status 字段。
    if result.get("calibrated"):
        try:
            status_payload = _build_calibration_status_payload()
            if status_payload.get("data"):
                result["data"] = status_payload["data"]
                result["status_payload"] = status_payload
        except Exception as exc:
            logger.warning("merge calibration status failed: %s", exc)

    return result


def _normalize_calibration_field_aliases(raw: dict[str, Any] | None) -> dict[str, Any]:
    """把算法产出的字段名归一化为前端使用的字段名。"""
    if not isinstance(raw, dict):
        return {}

    def _first(*keys: str) -> Any:
        for k in keys:
            if k in raw and raw.get(k) not in (None, ""):
                return raw.get(k)
        return None

    matrix = _first(
        "transform_matrix",
        "perspective_matrix",
        "matrix",
        "homography",
    )

    source_path = _first("source_image_path", "source_image", "source_path", "image_path", "input_image_path")
    mask_path = _first("belt_mask_path", "belt_mask", "mask_path", "belt_rectified_mask_path")
    preview_path = _first(
        "rectified_preview_path",
        "adjusted_preview_path",
        "preview_path",
        "corrected_preview_path",
        "rectified_image_path",
        "adjusted_preview",
    )

    normalized: dict[str, Any] = {
        "enabled": bool(raw.get("enabled", True)) if raw else False,
        "camera_id": raw.get("camera_id") or "default",
        "calibration_id": raw.get("calibration_id") or raw.get("calibrationId") or None,
        "source_image_path": source_path,
        "source_image_url": _first("source_image_url", "image_url") or None,
        "belt_mask_path": mask_path,
        "belt_mask_url": _first("belt_mask_url", "mask_url") or None,
        "rectified_preview_path": preview_path,
        "rectified_preview_url": _first(
            "rectified_preview_url",
            "adjusted_preview_url",
            "preview_url",
            "corrected_preview_url",
        ) or None,
        "transform_matrix": matrix,
        "vertical_distance": raw.get("vertical_distance") or raw.get("verticalDistance"),
        "pixel_length": raw.get("pixel_length") or raw.get("pixelLength"),
        "mm_per_pixel": raw.get("mm_per_pixel") or raw.get("mmPerPixel"),
        "image_width": raw.get("image_width"),
        "image_height": raw.get("image_height"),
        "note": raw.get("note") or "",
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at") or raw.get("created_at"),
    }
    return {k: v for k, v in normalized.items() if v is not None}


def _build_calibration_status_payload() -> dict[str, Any]:
    """读取 current_calibration.json 并组装完整状态返回。"""
    import json as _json

    from app.services.algorithm_cli_service import CALIBRATION_JSON
    from app.services.path_service import to_static_url

    exists = CALIBRATION_JSON.exists()
    rel_json = "data/calibration/current_calibration.json"
    json_url = algorithm_cli_service.algorithm_path_to_static_url(rel_json)

    payload: dict[str, Any] = {
        "ok": True,
        "exists": exists,
        "calibration_json": rel_json if exists else None,
        "calibration_url": json_url if exists else None,
        "data": None,
    }

    if not exists:
        return payload

    try:
        raw = _json.loads(CALIBRATION_JSON.read_text(encoding="utf-8"))
    except Exception as exc:
        payload["exists"] = False
        payload["error"] = f"{type(exc).__name__}: {exc}"
        return payload

    if not isinstance(raw, dict):
        return payload

    data = _normalize_calibration_field_aliases(raw)

    # 优先使用 JSON 已有的 *_url 字段；若为空，则按 *_path 转成 /static/...
    for path_field, url_field in (
        ("source_image_path", "source_image_url"),
        ("belt_mask_path", "belt_mask_url"),
        ("rectified_preview_path", "rectified_preview_url"),
    ):
        url_value = data.get(url_field)
        if not url_value:
            url_value = to_static_url(data.get(path_field))
            if url_value:
                data[url_field] = url_value

    payload["data"] = data
    payload["camera_id"] = data.get("camera_id") or "default"
    payload["updated_at"] = data.get("updated_at") or data.get("created_at")
    payload["source_image_url"] = data.get("source_image_url")
    payload["belt_mask_url"] = data.get("belt_mask_url")
    payload["rectified_preview_url"] = data.get("rectified_preview_url")
    payload["transform_matrix"] = data.get("transform_matrix")
    payload["vertical_distance"] = data.get("vertical_distance")
    payload["pixel_length"] = data.get("pixel_length")
    payload["note"] = data.get("note") or ""

    return payload


@router.get("/algorithm/calibration/status")
def get_calibration_status() -> dict:
    payload = _build_calibration_status_payload()
    payload["real_length_mm"] = _read_calibration_config()["real_length_mm"]
    return payload


@router.get("/algorithm/calibration/config")
def get_calibration_config() -> dict[str, Any]:
    return {"ok": True, **_read_calibration_config()}


@router.post("/algorithm/calibration/config")
def save_calibration_config(payload: CalibrationConfigRequest) -> dict[str, Any]:
    CALIBRATION_CONFIG_JSON.parent.mkdir(parents=True, exist_ok=True)
    data = {"real_length_mm": float(payload.real_length_mm)}
    CALIBRATION_CONFIG_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"ok": True, **data}


# ---------------------------------------------------------------------------
# 独立抓帧：只抓一张标定图，不创建 video_task，不影响数据大屏录制状态
# ---------------------------------------------------------------------------

class CaptureFrameRequest(BaseModel):
    stream_url: Optional[str] = None
    timeout: Optional[float] = 8.0
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[int] = None


@router.post("/algorithm/calibration/capture-frame")
def post_capture_calibration_frame(payload: CaptureFrameRequest) -> dict:
    """
    从 fish_camera RTMP 流独立抓一帧 JPG，保存到 data/uploads/calibration/YYYY/MM/DD/。

    与 /api/algorithm/calibration/capture 的区别：
      - 不启动 camera_service_yzt 录制管线
      - 不创建任何 video_task
      - 不写视频历史
      - 不影响数据大屏的"摄像头输入"状态
      - 即使后端正在录制业务视频，本接口仍然可以独立抓帧
    """
    from app.services.camera_yzt.camera_yzt_service import camera_service_yzt

    settings = camera_service_yzt.get_saved_settings()
    result = algorithm_calibration_capture_service.capture_frame_via_camera_yzt(
        stream_url=payload.stream_url,
        width=payload.width or settings["width"],
        height=payload.height or settings["height"],
        fps=payload.fps or settings["fps"],
        timeout_seconds=max(8.0, float(payload.timeout or 8.0) * 3),
        stop_after_capture=True,
    )
    if not result.get("ok"):
        code = result.get("error_code") or "CAPTURE_FAILED"
        # 用户友好错误：bootstrap_service 返回的 code 已经翻译成中文 user_message，
        # 这里直接透传，不再追加任何 RTMP / 127.0.0.1 / fish_camera 等技术字串。
        user_message = result.get("error_message") or "标定失败，请检查摄像头画面是否正常。"

        # 状态码：根据错误类型
        if code in {
            "CAMERA_DEVICE_NOT_CONFIGURED",
            "CAMERA_DEVICE_START_FAILED",
            "CAMERA_STREAM_TIMEOUT",
            "FISH_CAMERA_STREAM_NOT_READY",
        }:
            status_code = 409
        elif code == "FFMPEG_NOT_FOUND":
            status_code = 500
        elif code in {"CAPTURE_TIMEOUT", "CAPTURE_FAILED"}:
            status_code = 500
        else:
            status_code = 500

        raise HTTPException(
            status_code=status_code,
            detail={
                "ok": False,
                "error_code": code,
                "error_message": user_message,
                # debug 字段仅后端日志使用，不返回前端
            },
        )

    return {
        "ok": True,
        "saved": True,
        "source_image_path": result.get("source_image_path"),
        "source_image_url": result.get("source_image_url"),
        "stream_url": result.get("stream_url"),
        "message": "抓帧成功",
    }


# ---------------------------------------------------------------------------
# 5.2 POST /api/algorithm/videos
# ---------------------------------------------------------------------------

@router.post("/algorithm/videos")
def post_run_video_pipeline(
    payload: VideoPipelineRequest,
    db: Session = Depends(get_db),
) -> dict:
    """
    调用算法工程师提供的 run_video_pipeline CLI，并把结果导入数据库。

    真实命令：
        python -m algorithms.tools.run_video_pipeline \
            --input-video <path> --camera-id default \
            --device cuda --video-fps 25 --overwrite

    显式禁止 --calibration-source-image / --frame-start / --frame-end /
              --device cpu / --disable-wound / --video-id / --keep-* / --no-publish

    输出：data/results/<video_id>/reports/pipeline_result.json
          data/results/<video_id>/reports/error_conclusion.txt
    """
    result = algorithm_cli_service.run_video_pipeline_cli(
        input_video=payload.input_video,
        camera_id=payload.camera_id,
        device=payload.device,
        video_fps=payload.video_fps,
        overwrite=payload.overwrite,
    )

    video_id = _path_for_response(result.get("video_id"))

    payload_resp: dict[str, Any] = {
        "ok": bool(result.get("ok")),
        "video_id": video_id,
        "status": _path_for_response(result.get("status")),
        "result_dir": _path_for_response(result.get("result_dir")),
        "pipeline_result_path": _path_for_response(result.get("pipeline_result_path")),
        "error_conclusion_path": _path_for_response(result.get("error_conclusion_path")),
        "log_path": _path_for_response(result.get("log_path")),
        "returncode": result.get("returncode"),
        "summary": None,
        "import_result": None,
    }

    pipeline_result = result.get("pipeline_result") or {}
    if isinstance(pipeline_result, dict):
        summary = pipeline_result.get("summary")
        if isinstance(summary, dict):
            payload_resp["summary"] = summary

    if not payload_resp["ok"]:
        payload_resp["failed_stage"] = (
            pipeline_result.get("failed_stage") if isinstance(pipeline_result, dict) else None
        )
        payload_resp["error_message"] = _path_for_response(result.get("error_message"))
        payload_resp["error_conclusion"] = _path_for_response(
            result.get("error_conclusion_text")
        )

    if (
        payload.import_to_db
        and payload_resp["ok"]
        and video_id
        and isinstance(pipeline_result, dict)
        and pipeline_result.get("status") == "done"
    ):
        try:
            import_result = algorithm_result_service.import_pipeline_result_payload_to_db(
                db, pipeline_result, commit=True,
            )
            payload_resp["import_result"] = import_result
        except Exception as exc:
            logger.exception("import pipeline_result failed: %s", exc)
            payload_resp["import_result"] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

        # 算法完成且入库成功后，自动生成 DOCX/PDF 报表
        try:
            if bool(payload_resp["import_result"].get("ok")):
                report_res = generate_video_pdf_report(video_id)
                payload_resp["report_result"] = report_res
        except Exception as exc:
            logger.exception("generate video pdf report failed: %s", exc)
            payload_resp["report_result"] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "video_id": video_id,
            }

    return payload_resp


# ---------------------------------------------------------------------------
# 5.3 GET /api/algorithm/videos/{video_id}/result
# ---------------------------------------------------------------------------

@router.get("/algorithm/videos/{video_id}/result")
def get_pipeline_result(video_id: str, db: Session = Depends(get_db)) -> dict:
    """读取算法任务结果。

    优先级：
        1. data/results/<video_id>/reports/pipeline_result.json
        2. 数据库 video_task / fish 表作为 fallback
    """
    payload: dict | None = None
    try:
        payload = algorithm_cli_service.read_pipeline_result(video_id)
    except FileNotFoundError:
        payload = None
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "video_id": video_id,
            },
        )

    if payload is None:
        raise HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "error": "pipeline_result.json not found",
                "video_id": video_id,
            },
        )

    resp = algorithm_result_service.pipeline_result_to_response(payload)
    return {"ok": True, "data": resp, "_source": "pipeline_result.json"}

    resp = algorithm_result_service.pipeline_result_to_response(payload)
    return {"ok": True, "data": resp}


# ---------------------------------------------------------------------------
# 5.4 GET /api/algorithm/videos/{video_id}/error-conclusion
# ---------------------------------------------------------------------------

@router.get("/algorithm/videos/{video_id}/error-conclusion")
def get_error_conclusion(video_id: str) -> dict:
    try:
        text = algorithm_cli_service.read_error_conclusion(video_id)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "error": "error_conclusion.txt not found",
                "video_id": video_id,
            },
        )

    if not text:
        text = "（error_conclusion.txt 为空）"

    return {"ok": True, "video_id": video_id, "text": text}


# ---------------------------------------------------------------------------
# 5.5 POST /api/algorithm/import-result/{video_id}
# ---------------------------------------------------------------------------

def _list_pipeline_result_dirs() -> list[dict[str, Any]]:
    """扫描 DATA_DIR/results 下所有 <video_id>/reports/pipeline_result.json。"""
    from app.services.algorithm_cli_service import RESULTS_DIR

    items: list[dict[str, Any]] = []
    if not RESULTS_DIR.exists():
        return items
    for child in RESULTS_DIR.iterdir():
        if not child.is_dir() or child.name.endswith(".partial"):
            continue
        report = child / "reports" / "pipeline_result.json"
        if not report.exists():
            continue
        items.append({
            "video_id": child.name,
            "result_dir": f"data/results/{child.name}",
            "pipeline_result_path": f"data/results/{child.name}/reports/pipeline_result.json",
            "pipeline_result_abs": str(report.resolve()),
        })
    items.sort(key=lambda x: x["video_id"])
    return items


def _read_pipeline_summary(path: Path) -> dict[str, Any]:
    """轻量读取 pipeline_result.json 的 summary / fish / status。"""
    import json as _json
    try:
        with open(path, "r", encoding="utf-8") as fp:
            payload = _json.load(fp)
    except Exception as exc:
        return {"_read_error": str(exc)}
    return {
        "status": payload.get("status"),
        "summary": payload.get("summary") or {},
        "fish_count": len(payload.get("fish") or []),
    }


@router.get("/algorithm/results/discover")
def get_results_discover(db: Session = Depends(get_db)) -> dict:
    """
    扫描 data/results 下所有 pipeline_result.json，不导入数据库。
    用于数据管理页面"发现但未导入"功能。
    """
    from app.repositories.models import VideoTask

    existing_ids: set[str] = set(
        db.query(VideoTask.video_id).all()
        and [row[0] for row in db.query(VideoTask.video_id).all()]
        or []
    )

    items_out: list[dict[str, Any]] = []
    for entry in _list_pipeline_result_dirs():
        meta = _read_pipeline_summary(Path(entry["pipeline_result_abs"]))
        items_out.append({
            "video_id": entry["video_id"],
            "pipeline_result_path": entry["pipeline_result_path"],
            "status": meta.get("status"),
            "summary": meta.get("summary"),
            "fish_count": meta.get("fish_count"),
            "already_imported": entry["video_id"] in existing_ids,
        })

    return {
        "ok": True,
        "total": len(items_out),
        "items": items_out,
    }


@router.post("/algorithm/import-results/batch")
def post_import_results_batch(
    payload: BatchImportResultsRequest,
    db: Session = Depends(get_db),
) -> dict:
    """
    批量导入 data/results 下所有 / 指定 video_id 的算法结果到数据库。
    单个失败不影响其他视频。
    """
    if payload.scan_all:
        entries = _list_pipeline_result_dirs()
    else:
        # 只保留 data/results 下确实存在的
        all_entries = {e["video_id"]: e for e in _list_pipeline_result_dirs()}
        entries = [all_entries[v] for v in payload.video_ids if v in all_entries]

    items_out: list[dict[str, Any]] = []
    success = 0
    failed = 0

    for entry in entries:
        try:
            result = algorithm_result_service.import_pipeline_result_to_db(
                db, entry["video_id"], commit=True,
            )
            ok = bool(result.get("ok"))
            if ok:
                success += 1
                # 单个 video 导入成功时尝试自动生成报表
                try:
                    report_res = generate_video_pdf_report(entry["video_id"])
                    result["report_result"] = report_res
                except Exception as exc:
                    logger.exception(
                        "generate video pdf report failed: %s", exc,
                    )
                    result["report_result"] = {
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "video_id": entry["video_id"],
                    }
                # 第五阶段：入库成功后自动写一条 detection_report 视频报表记录
                try:
                    from app.services.report_pdf_service import (
                        generate_video_fish_quality_pdf as _persist_video_report,
                    )
                    from app.services.report_stats_service import (
                        get_fish_quality_summary as _summary_for_video,
                    )
                    _summary = _summary_for_video(
                        db, video_id=entry["video_id"]
                    )
                    persist_res = _persist_video_report(
                        db,
                        video_id=entry["video_id"],
                        summary=_summary,
                        existing_report_id=None,
                    )
                    result["video_detection_report"] = persist_res
                except Exception as exc:
                    logger.exception(
                        "batch persist video detection_report failed: %s", exc,
                    )
                    result["video_detection_report"] = {
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "video_id": entry["video_id"],
                    }
            else:
                failed += 1
            items_out.append({"video_id": entry["video_id"], **result})
        except Exception as exc:
            failed += 1
            items_out.append({
                "video_id": entry["video_id"],
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            })

    return {
        "ok": True,
        "total": len(items_out),
        "success": success,
        "failed": failed,
        "items": items_out,
    }


@router.post("/algorithm/import-result/{video_id}")
def post_import_result(
    video_id: str,
    db: Session = Depends(get_db),
) -> dict:
    try:
        result = algorithm_result_service.import_pipeline_result_to_db(
            db, video_id, commit=True,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "error": "pipeline_result.json not found",
                "video_id": video_id,
            },
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "video_id": video_id,
            },
        )

    # 导入成功后自动补一次 DOCX/PDF 报表（失败不影响导入主流程）
    try:
        if bool(result.get("ok")):
            report_res = generate_video_pdf_report(video_id)
            result["report_result"] = report_res
    except Exception as exc:
        logger.exception("generate video pdf report failed: %s", exc)
        result["report_result"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "video_id": video_id,
        }

    # 第五阶段：算法结果入库成功后，自动写一条 detection_report 视频报表记录
    # （与 report_router 手动接口共用同一份 persistence 逻辑）。失败不抛错。
    try:
        if bool(result.get("ok")):
            from app.services.report_pdf_service import (
                generate_video_fish_quality_pdf as _persist_video_report,
            )
            from app.services.report_stats_service import (
                get_fish_quality_summary as _summary_for_video,
            )
            _summary = _summary_for_video(db, video_id=video_id)
            persist_res = _persist_video_report(
                db,
                video_id=video_id,
                summary=_summary,
                existing_report_id=None,
            )
            result["video_detection_report"] = persist_res
    except Exception as exc:
        logger.exception(
            "persist video detection_report failed: %s", exc
        )
        result["video_detection_report"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "video_id": video_id,
        }

    return result
