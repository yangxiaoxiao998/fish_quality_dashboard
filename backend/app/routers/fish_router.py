# 作用：提供视频相关的 API 接口
from __future__ import annotations

import csv
import io
import os
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.repositories.db import get_db
from app.repositories import session_repo
from app.services.path_service import to_abs_data_path, to_data_relative, to_static_url
from app.utils.time_utils import to_beijing
from app.paths import DATA_DIR

router = APIRouter()


def _normalize_damage_ratio(value) -> float | None:
    """把 surface_damage_ratio 等字段清洗为 float。

    - None / 非数值 / 负数 → None
    - 0~1 / 0~100 都直接返回原值（由前端决定按 % 还是小数展示）
    """
    if value is None:
        return None
    try:
        x = float(value)
    except Exception:
        return None
    if x < 0:
        return None
    return x


def _choose_display_image(f) -> tuple[str | None, str]:
    """选择推荐展示图。

    优先级：
        1. corrected_image_path（矫正完成）
        2. original_image_path（矫正前原图）
        3. mask_image_path（裁剪鱼图）

    每层优先做文件存在性检查：
        - 路径为空 / 转 URL 失败 -> 跳过
        - 文件在 DATA_DIR 下存在 -> 选它
        - 文件不存在 -> 继续 fallback（数据迁移期间常见）

    Returns:
        (display_image_url, image_source_type)
        image_source_type ∈ {corrected, original, mask, none}
    """
    candidates = [
        ("corrected", getattr(f, "corrected_image_path", None)),
        ("original", getattr(f, "original_image_path", None)),
        ("mask", getattr(f, "mask_image_path", None)),
    ]

    # 第一轮：优先选文件确实存在的
    for source, rel in candidates:
        if not rel:
            continue
        url = to_static_url(rel)
        abs_path = to_abs_data_path(rel)
        if url and abs_path and abs_path.exists():
            return url, source

    # 第二轮：路径存在但文件缺失时，按字段 fallback（兼容迁移期）
    for source, rel in candidates:
        if not rel:
            continue
        url = to_static_url(rel)
        if url:
            return url, source

    return None, "none"


def _build_fish_display_id(
    video_id: str,
    fishes: list,
    target_fish: "object",
) -> str:
    """
    生成 fish_display_id：在同一个 video_id 内按 created_at 排序，给每条鱼一个
    客户友好的连续编号，例如 F000001 / F000002 / F000003。

    不写数据库，仅 API 展示用。
    """
    if target_fish is None:
        return ""
    same_video = [f for f in fishes if f.video_id == video_id]
    same_video_sorted = sorted(same_video, key=lambda f: (f.created_at, f.fish_id))
    for idx, f in enumerate(same_video_sorted, start=1):
        if f.fish_id == target_fish.fish_id:
            return f"F{idx:06d}"
    return f"F{len(same_video_sorted) + 1:06d}"


def _probe_video_duration_seconds(source_path: str | None) -> float | None:
    """通过 cv2 探测视频文件真实时长。失败返回 None。"""
    if not source_path:
        return None
    try:
        from app.services.path_service import to_abs_data_path
        abs_path = to_abs_data_path(source_path)
        if abs_path is None:
            return None
        import cv2
        cap = cv2.VideoCapture(str(abs_path))
        if not cap.isOpened():
            cap.release()
            return None
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        nframes = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        cap.release()
        if fps > 0 and nframes > 0:
            return round(float(nframes) / float(fps), 2)
    except Exception:
        return None
    return None


def _format_hms(seconds: float | int | None) -> str | None:
    if seconds is None:
        return None
    s = max(0, int(seconds))
    h = s // 3600
    m = (s % 3600) // 60
    ss = s % 60
    return f"{h:02d}:{m:02d}:{ss:02d}"


@router.get("/videos")
def list_videos(
    request: Request,
    status: Optional[str] = None,
    source_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    keyword: Optional[str] = None,
    only_algorithm_results: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    from app.repositories.models import VideoTask
    from sqlalchemy import select, func, or_

    stmt = select(VideoTask)
    count_stmt = select(func.count()).select_from(VideoTask)

    filters = []
    if status:
        filters.append(VideoTask.status == status)
    if source_type:
        filters.append(VideoTask.source_type == source_type)

    if keyword:
        kw = f"%{keyword.strip()}%"
        filters.append(VideoTask.video_id.ilike(kw))

    if only_algorithm_results:
        filters.append(VideoTask.pipeline_result_path.isnot(None))

    if start_date:
        try:
            start_dt = to_beijing(
                datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            )
            filters.append(VideoTask.created_at >= start_dt)
        except ValueError:
            pass

    if end_date:
        try:
            end_dt = to_beijing(datetime.fromisoformat(end_date.replace("Z", "+00:00")))
            filters.append(VideoTask.created_at <= end_dt)
        except ValueError:
            pass

    if filters:
        stmt = stmt.where(*filters)
        count_stmt = count_stmt.where(*filters)

    total = db.execute(count_stmt).scalar() or 0

    stmt = stmt.order_by(VideoTask.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    videos = db.execute(stmt).scalars().all()

    items = []
    for v in videos:
        playback_url = to_static_url(v.source_path)
        abs_video_path = to_abs_data_path(v.source_path)
        video_exists = abs_video_path.exists() if abs_video_path else False
        # 与 detail 端点保持一致：done 且文件存在才给 play_url
        play_url = (
            f"{str(request.base_url).rstrip('/')}/api/videos/{v.video_id}/play"
            if v.status == "done" and video_exists
            else None
        )

        created_at_bj = to_beijing(v.created_at)
        started_at_bj = to_beijing(v.started_at)
        finished_at_bj = to_beijing(v.finished_at)

        # 视频文件真实时长
        video_duration_seconds = _probe_video_duration_seconds(v.source_path)
        # 检测耗时 = finished - started
        duration_seconds: float | None = None
        if started_at_bj is not None:
            from app.utils.time_utils import now_beijing
            end_for_calc = finished_at_bj if finished_at_bj is not None else now_beijing()
            try:
                duration_seconds = round(
                    (end_for_calc - started_at_bj).total_seconds(), 2
                )
            except Exception:
                duration_seconds = None

        # 算法结果 URL（来自 pipeline_result_path / grounded_video_path）
        grounded_video_url = to_static_url(getattr(v, "grounded_video_path", None))
        measurement_csv_url = None
        if getattr(v, "measurement_csv_path", None):
            measurement_csv_url = to_static_url(v.measurement_csv_path)

        items.append(
            {
                "id": v.video_id,
                "video_id": v.video_id,
                "uploadTime": created_at_bj.strftime("%Y-%m-%d %H:%M")
                if created_at_bj
                else None,
                # 客户主显示：视频文件真实时长（不是检测耗时）
                "duration": _format_hms(video_duration_seconds),
                # 新增：检测耗时（独立字段）
                "duration_seconds": duration_seconds,
                "video_duration_seconds": video_duration_seconds,
                "fishCount": v.total_fish or 0,
                "status": v.status,
                "playback_url": playback_url,
                "play_url": play_url,
                "playable": v.status == "done" and video_exists,
                "video_exists": video_exists,
                "source_type": v.source_type,
                # 算法新字段
                "total_fish": v.total_fish or 0,
                "complete_fish": getattr(v, "complete_fish", None) or 0,
                "incomplete_fish": getattr(v, "incomplete_fish", None) or 0,
                "failed_fish": getattr(v, "failed_fish", None) or 0,
                "damaged_fish": getattr(v, "damaged_fish", None) or 0,
                "avg_surface_damage_ratio": getattr(v, "avg_surface_damage_ratio", None),
                "max_surface_damage_ratio": getattr(v, "max_surface_damage_ratio", None),
                "pipeline_result_path": getattr(v, "pipeline_result_path", None),
                "result_dir": getattr(v, "result_dir", None),
                "error_conclusion_path": getattr(v, "error_conclusion_path", None),
                "grounded_video_url": grounded_video_url,
                "measurement_csv_url": measurement_csv_url,
                "input_video": getattr(v, "input_video", None),
                "original_input_video": getattr(v, "original_input_video", None),
                "failed_stage": getattr(v, "failed_stage", None),
                "error_message": getattr(v, "error_message", None),
                "created_at": created_at_bj.isoformat() if created_at_bj else None,
                "started_at": started_at_bj.isoformat() if started_at_bj else None,
                "finished_at": finished_at_bj.isoformat() if finished_at_bj else None,
            }
        )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


@router.get("/videos/{video_id}")
def get_video(video_id: str, db: Session = Depends(get_db)) -> dict:
    obj = session_repo.get_video_task(db, video_id=video_id)
    if obj is None:
        return {"status": "not_found", "video_id": video_id}

    progress = None
    if obj.total_fish and obj.total_fish > 0:
        progress = round(obj.processed_fish / obj.total_fish, 2)

    playback_url = to_static_url(obj.source_path)

    created_at_bj = to_beijing(obj.created_at)
    started_at_bj = to_beijing(obj.started_at)
    finished_at_bj = to_beijing(obj.finished_at)

    # 鱼数量统一以 fish 表为准（鱼条数是"产出了多少条鱼"的事实）。
    from app.repositories.models import Fish
    from sqlalchemy import select, func
    fish_count_from_db = int(
        db.execute(
            select(func.count())
            .select_from(Fish)
            .where(Fish.video_id == video_id)
        ).scalar()
        or 0
    )
    if obj.total_fish != fish_count_from_db:
        # 数据不一致：把真值回写到 task，便于后续组件只用一处来源
        try:
            obj.total_fish = fish_count_from_db
            db.commit()
        except Exception:
            db.rollback()
        print(
            f"[videos/{video_id}] total_fish corrected: "
            f"task.total_fish={obj.total_fish} db_count={fish_count_from_db}"
        )

    # 视频文件真实时长（通过 cv2 probe，失败回退 None）
    video_duration_seconds = _probe_video_duration_seconds(obj.source_path)

    # 检测耗时：finished - started；进行中 = now - started
    from app.utils.time_utils import now_beijing
    duration_seconds: float | None = None
    if started_at_bj is not None:
        end_for_calc = finished_at_bj if finished_at_bj is not None else now_beijing()
        try:
            duration_seconds = round(
                (end_for_calc - started_at_bj).total_seconds(), 2
            )
        except Exception:
            duration_seconds = None

    return {
        "video_id": obj.video_id,
        "status": obj.status,
        "total_fish": fish_count_from_db,
        "processed_fish": obj.processed_fish,
        "fish_count_from_db": fish_count_from_db,
        "progress": progress,
        "current_fish_id": obj.current_fish_id,
        "current_algorithm": obj.current_algorithm,
        "fps": obj.fps_estimate,
        "fps_estimate": obj.fps_estimate,
        "last_error": obj.last_error,
        "created_at": created_at_bj.isoformat() if created_at_bj else None,
        "started_at": started_at_bj.isoformat() if started_at_bj else None,
        "finished_at": finished_at_bj.isoformat() if finished_at_bj else None,
        "duration_seconds": duration_seconds,
        "video_duration_seconds": video_duration_seconds,
        "playback_url": playback_url,
        "source_type": obj.source_type,
    }


@router.get("/videos/{video_id}/detail")
def get_video_detail(video_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    obj = session_repo.get_video_task(db, video_id=video_id)
    if obj is None:
        return {"status": "not_found", "video_id": video_id}

    base_url = str(request.base_url).rstrip("/")
    playback_url = to_static_url(obj.source_path)
    abs_video_path = to_abs_data_path(obj.source_path)
    video_exists = abs_video_path.exists() if abs_video_path else False

    created_at_bj = to_beijing(obj.created_at)
    started_at_bj = to_beijing(obj.started_at)
    finished_at_bj = to_beijing(obj.finished_at)

    video_duration_seconds = _probe_video_duration_seconds(obj.source_path)
    duration_seconds: float | None = None
    if started_at_bj is not None:
        from app.utils.time_utils import now_beijing
        end_for_calc = finished_at_bj if finished_at_bj is not None else now_beijing()
        try:
            duration_seconds = round(
                (end_for_calc - started_at_bj).total_seconds(), 2
            )
        except Exception:
            duration_seconds = None

    return {
        "video_id": obj.video_id,
        "status": obj.status,
        "source_type": obj.source_type,
        "total_fish": obj.total_fish,
        "processed_fish": obj.processed_fish,
        "created_at": created_at_bj.isoformat() if created_at_bj else None,
        "started_at": started_at_bj.isoformat() if started_at_bj else None,
        "finished_at": finished_at_bj.isoformat() if finished_at_bj else None,
        "video_duration_seconds": video_duration_seconds,
        "duration_seconds": duration_seconds,
        "playback_url": playback_url,
        "video_exists": video_exists,
        "playable": obj.status == "done" and video_exists,
        "play_url": f"{base_url}/api/videos/{video_id}/play" if obj.status == "done" and video_exists else None,
        "source_path": obj.source_path,
    }


@router.get("/videos/{video_id}/play")
def play_video(video_id: str, db: Session = Depends(get_db)):
    obj = session_repo.get_video_task(db, video_id=video_id)
    if not obj or not obj.source_path:
        return JSONResponse(content={"error": "Video not found"}, status_code=404)

    if obj.status != "done":
        return JSONResponse(content={"error": "视频仍在处理中，请等待完成后播放"}, status_code=400)

    abs_video_path = to_abs_data_path(obj.source_path)
    if not abs_video_path or not os.path.exists(abs_video_path):
        return JSONResponse(content={"error": "Video file not found"}, status_code=404)

    return FileResponse(str(abs_video_path), media_type="video/mp4", filename=f"{video_id}.mp4")


@router.get("/videos/{video_id}/playback")
def get_video_playback(video_id: str, db: Session = Depends(get_db)):
    """
    返回 video_id 对应的浏览器可播放 H.264 视频信息。

    返回：
        {
            "ok": True,
            "video_id": "...",
            "source_path": "data/uploads/videos/<video_id>.mp4",
            "source_url":  "/static/uploads/videos/<video_id>.mp4",
            "playable_path": "data/uploads/videos/playable/<video_id>_h264.mp4",
            "playable_url":  "/static/uploads/videos/playable/<video_id>_h264.mp4",
            "codec_name": "mpeg4" | "h264",
            "pix_fmt":    "yuv420p",
            "converted": True | False,
            "video_status": "done" | "recording" | ...,
        }

    行为：
      - 如果源就是 H.264 + yuv420p：直接返回 source_url。
      - 否则按需生成 playable H.264 副本（保存在 data/uploads/videos/playable/）。
      - 如果生成失败，返回 5xx + error_message。
      - 视频文件不存在则返回 404。
    """
    from app.services.video_playback_service import ensure_browser_playable_video

    # 优先看 video_task，但 video_task 不存在也不强制要求 source_path 有值
    obj = session_repo.get_video_task(db, video_id=video_id)
    video_status = obj.status if obj else None

    result = ensure_browser_playable_video(video_id)
    if not result.get("ok"):
        code = result.get("error_code") or "VIDEO_PLAYBACK_FAILED"
        if code == "VIDEO_FILE_NOT_FOUND":
            return JSONResponse(
                status_code=404,
                content={
                    "ok": False,
                    "video_id": video_id,
                    "error_code": code,
                    "error_message": result.get("error_message"),
                },
            )
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "video_id": video_id,
                "error_code": code,
                "error_message": result.get("error_message"),
            },
        )

    return {
        "ok": True,
        "video_id": video_id,
        "source_path": result.get("source_path"),
        "source_url": result.get("source_url"),
        "playable_path": result.get("playable_path"),
        "playable_url": result.get("playable_url"),
        "codec_name": result.get("codec_name"),
        "pix_fmt": result.get("pix_fmt"),
        "converted": bool(result.get("converted")),
        "cached": bool(result.get("cached")),
        "video_status": video_status,
    }


@router.post("/videos/export")
def export_videos(db: Session = Depends(get_db)):
    import os
    from app.repositories.models import VideoTask
    from sqlalchemy import select

    stmt = select(VideoTask).order_by(VideoTask.created_at.desc())
    videos = db.execute(stmt).scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["video_id", "source_type", "status", "total_fish", "created_at", "source_path"])

    for v in videos:
        created = to_beijing(v.created_at).strftime("%Y-%m-%d %H:%M") if v.created_at else ""
        writer.writerow([
            v.video_id,
            v.source_type or "",
            v.status or "",
            v.total_fish or 0,
            created,
            v.source_path or "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=video_export.csv"},
    )


@router.get("/videos/{video_id}/fishes")
def list_fishes(
    video_id: str,
    status: Optional[str] = None,
    track_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    """返回当前视频 fish[] 列表，分页。

    支持 status / track_id 过滤。
    返回 items + total + page + page_size。
    """
    from app.repositories.models import Fish
    from sqlalchemy import select, func
    from app.services.fish_id_service import (
        derive_fish_display_id,
        derive_fish_short_id,
        derive_video_display_id,
    )

    stmt = select(Fish).where(Fish.video_id == video_id)
    count_stmt = select(func.count()).select_from(Fish).where(Fish.video_id == video_id)

    filters = []
    if status:
        filters.append(Fish.status == status)
    if track_id is not None and str(track_id).strip() != "":
        try:
            tid = int(str(track_id))
            filters.append(Fish.track_id == tid)
        except Exception:
            filters.append(Fish.track_id == track_id)
    if filters:
        stmt = stmt.where(*filters)
        count_stmt = count_stmt.where(*filters)

    total = db.execute(count_stmt).scalar() or 0
    stmt = stmt.order_by(Fish.track_id.asc().nullslast(), Fish.fish_id.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    fishes = db.execute(stmt).scalars().all()

    out: list[dict] = []
    for idx, f in enumerate(fishes, start=1):
        fish_display_id = f"F{idx:06d}"
        out.append(
            {
                "fish_id": f.fish_id,
                "fish_display_id": fish_display_id,
                "fish_short_id": derive_fish_short_id(f.fish_id) or fish_display_id,
                "video_display_id": derive_video_display_id(f.video_id),
                "track_id": str(f.track_id) if f.track_id is not None else None,
                "status": f.status,
                "bbox": (
                    [f.bbox_x1, f.bbox_y1, f.bbox_x2, f.bbox_y2]
                    if f.bbox_x1 is not None
                    else None
                ),
                # 算法新字段
                "fish_uid": getattr(f, "fish_uid", None),
                "frame_index": getattr(f, "frame_index", None),
                "crop_image_path": getattr(f, "crop_image_path", None),
                "crop_image_url": to_static_url(getattr(f, "crop_image_path", None)),
                "body_length_cm": getattr(f, "body_length_cm", None),
                "body_length_no_tail_cm": getattr(f, "body_length_no_tail_cm", None),
                "body_height_cm": getattr(f, "body_height_cm", None),
                "pred_weight_g": getattr(f, "pred_weight_g", None),
                "condition_factor": getattr(f, "condition_factor", None),
                "surface_damage_ratio": getattr(f, "surface_damage_ratio", None),
                "error_message": getattr(f, "error_message", None) or getattr(f, "error_msg", None),
            }
        )
    return {
        "items": out,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/fishes")
def list_all_fishes(
    video_id: Optional[str] = None,
    fish_id: Optional[str] = None,
    min_length: Optional[float] = None,
    max_length: Optional[float] = None,
    min_weight: Optional[float] = None,
    max_weight: Optional[float] = None,
    min_height: Optional[float] = None,
    max_height: Optional[float] = None,
    min_condition: Optional[float] = None,
    max_condition: Optional[float] = None,
    surface_damage: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    from app.repositories.models import Fish
    from sqlalchemy import select, func, or_

    stmt = select(Fish)
    count_stmt = select(func.count()).select_from(Fish)

    filters = []
    if video_id:
        filters.append(Fish.video_id == video_id)
    if fish_id:
        filters.append(Fish.fish_id.like(f"%{fish_id}%"))
    if min_length is not None:
        filters.append(Fish.body_length >= min_length)
    if max_length is not None:
        filters.append(Fish.body_length <= max_length)
    if min_weight is not None:
        filters.append(Fish.body_weight >= min_weight)
    if max_weight is not None:
        filters.append(Fish.body_weight <= max_weight)
    if min_height is not None:
        filters.append(Fish.body_height >= min_height)
    if max_height is not None:
        filters.append(Fish.body_height <= max_height)
    if min_condition is not None:
        filters.append(Fish.condition_factor >= min_condition)
    if max_condition is not None:
        filters.append(Fish.condition_factor <= max_condition)
    if surface_damage:
        filters.append(Fish.surface_damage == surface_damage)

    if start_date:
        try:
            start_dt = to_beijing(datetime.fromisoformat(start_date.replace("Z", "+00:00")))
            filters.append(Fish.created_at >= start_dt)
        except ValueError:
            pass

    if end_date:
        try:
            end_dt = to_beijing(datetime.fromisoformat(end_date.replace("Z", "+00:00")))
            filters.append(Fish.created_at <= end_dt)
        except ValueError:
            pass

    if filters:
        stmt = stmt.where(*filters)
        count_stmt = count_stmt.where(*filters)

    total = db.execute(count_stmt).scalar() or 0

    stmt = stmt.order_by(Fish.created_at.desc(), Fish.fish_id.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    fishes = db.execute(stmt).scalars().all()

    # 计算每条鱼在其 video 内的 display_id 排名
    # 按 (video_id, created_at, fish_id) 升序排：第 1 条 -> F000001
    display_id_map: dict[str, str] = {}
    if fishes:
        fish_ids = [f.fish_id for f in fishes]
        all_stmt = (
            select(Fish)
            .where(Fish.video_id.in_({f.video_id for f in fishes}))
            .order_by(Fish.video_id, Fish.created_at, Fish.fish_id)
        )
        all_fishes = db.execute(all_stmt).scalars().all()
        per_video_counter: dict[str, int] = {}
        for af in all_fishes:
            per_video_counter[af.video_id] = per_video_counter.get(af.video_id, 0) + 1
            if af.fish_id in fish_ids:
                display_id_map[af.fish_id] = f"F{per_video_counter[af.video_id]:06d}"

    items = []
    for f in fishes:
        corrected_image_url = to_static_url(f.corrected_image_path)
        original_image_url = to_static_url(f.original_image_path)
        mask_image_url = to_static_url(getattr(f, "mask_image_path", None))
        display_image_url, image_source_type = _choose_display_image(f)

        # 损伤占比：优先 surface_damage_ratio；其次 wound_area_ratio / wound_ratio
        surface_damage_ratio = _normalize_damage_ratio(
            getattr(f, "surface_damage_ratio", None)
        )
        wound_area_ratio = _normalize_damage_ratio(
            getattr(f, "wound_area_ratio", None)
        )
        wound_ratio = _normalize_damage_ratio(
            getattr(f, "wound_ratio", None)
        )
        damage_ratio = (
            surface_damage_ratio
            if surface_damage_ratio is not None
            else (wound_area_ratio if wound_area_ratio is not None else wound_ratio)
        )

        created_at = None
        if f.created_at:
            created_at = to_beijing(f.created_at).strftime("%Y-%m-%d %H:%M")

        from app.services.fish_id_service import (
            derive_fish_short_id,
            derive_video_display_id,
        )
        fish_display_id = display_id_map.get(f.fish_id, "")
        items.append(
            {
                "fish_id": f.fish_id,
                "fish_display_id": fish_display_id,
                "fish_short_id": derive_fish_short_id(f.fish_id) or fish_display_id,
                "video_display_id": derive_video_display_id(f.video_id),
                "video_id": f.video_id,
                "track_id": str(f.track_id) if f.track_id is not None else None,
                # 图像字段
                "original_image_url": original_image_url,
                "corrected_image_url": corrected_image_url,
                "mask_image_url": mask_image_url,
                "display_image_url": display_image_url,
                "image_source_type": image_source_type,
                # 体征
                "body_length": f.body_length,
                "body_length_without_tail": f.body_length_without_tail,
                "body_height": f.body_height,
                "body_weight": f.body_weight,
                "condition_factor": f.condition_factor,
                # 损伤（数值占比 + 原始类型字符串保留）
                "surface_damage": f.surface_damage,
                "surface_damage_ratio": surface_damage_ratio,
                "wound_area_ratio": wound_area_ratio,
                "wound_ratio": wound_ratio,
                "damage_ratio": damage_ratio,
                "confidence": f.confidence,
                "created_at": created_at,
                "status": f.status,
            }
        )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


@router.get("/fishes/{fish_id}")
def fish_detail(fish_id: str, db: Session = Depends(get_db)) -> dict:
    from app.repositories.models import Fish
    from sqlalchemy import select

    stmt = select(Fish).where(Fish.fish_id == fish_id)
    f = db.execute(stmt).scalar_one_or_none()

    if f is None:
        return {"status": "not_found", "fish_id": fish_id}

    # 计算 fish_display_id：同 video 内排序位置
    display_id = ""
    rank_stmt = (
        select(Fish)
        .where(Fish.video_id == f.video_id)
        .order_by(Fish.created_at, Fish.fish_id)
    )
    same_video = db.execute(rank_stmt).scalars().all()
    for idx, sf in enumerate(same_video, start=1):
        if sf.fish_id == f.fish_id:
            display_id = f"F{idx:06d}"
            break

    corrected_image_url = to_static_url(f.corrected_image_path)
    original_image_url = to_static_url(f.original_image_path)
    mask_image_url = to_static_url(getattr(f, "mask_image_path", None))
    display_image_url, image_source_type = _choose_display_image(f)

    surface_damage_ratio = _normalize_damage_ratio(
        getattr(f, "surface_damage_ratio", None)
    )
    wound_area_ratio = _normalize_damage_ratio(
        getattr(f, "wound_area_ratio", None)
    )
    wound_ratio = _normalize_damage_ratio(
        getattr(f, "wound_ratio", None)
    )
    damage_ratio = (
        surface_damage_ratio
        if surface_damage_ratio is not None
        else (wound_area_ratio if wound_area_ratio is not None else wound_ratio)
    )

    from app.services.fish_id_service import (
        derive_fish_short_id,
        derive_video_display_id,
    )
    fish_short_id = derive_fish_short_id(f.fish_id) or display_id
    video_display_id = derive_video_display_id(f.video_id)

    created_at = None
    if f.created_at:
        created_at = to_beijing(f.created_at).strftime("%Y-%m-%d %H:%M")

    return {
        "fish_id": f.fish_id,
        "fish_display_id": display_id,
        "fish_short_id": fish_short_id,
        "video_display_id": video_display_id,
        "video_id": f.video_id,
        "track_id": str(f.track_id) if f.track_id is not None else None,
        "status": f.status,
        "original_image_url": original_image_url,
        "corrected_image_url": corrected_image_url,
        "mask_image_url": mask_image_url,
        "display_image_url": display_image_url,
        "image_source_type": image_source_type,
        "bbox": [f.bbox_x1, f.bbox_y1, f.bbox_x2, f.bbox_y2]
        if f.bbox_x1 is not None
        else None,
        "body_length": f.body_length,
        "body_length_without_tail": f.body_length_without_tail,
        "body_height": f.body_height,
        "body_weight": f.body_weight,
        "condition_factor": f.condition_factor,
        "surface_damage": f.surface_damage,
        "surface_damage_ratio": surface_damage_ratio,
        "wound_area_ratio": wound_area_ratio,
        "wound_ratio": wound_ratio,
        "damage_ratio": damage_ratio,
        "confidence": f.confidence,
        "created_at": created_at,
        "detection_id": str(f.track_id) if f.track_id is not None else None,
    }


@router.delete("/fishes/{fish_id}")
def delete_fish(fish_id: str, db: Session = Depends(get_db)) -> dict:
    from app.repositories.models import Fish
    from sqlalchemy import select, delete

    stmt = select(Fish).where(Fish.fish_id == fish_id)
    f = db.execute(stmt).scalar_one_or_none()
    if f is None:
        return {"status": "not_found", "fish_id": fish_id}

    db.execute(delete(Fish).where(Fish.fish_id == fish_id))
    db.commit()
    return {"status": "deleted", "fish_id": fish_id}


@router.post("/fishes/batch-delete")
def batch_delete_fishes(body: dict, db: Session = Depends(get_db)) -> dict:
    from app.repositories.models import Fish
    from sqlalchemy import delete

    fish_ids = body.get("fish_ids", [])
    if not fish_ids:
        return {"status": "error", "message": "fish_ids is required"}

    result = db.execute(delete(Fish).where(Fish.fish_id.in_(fish_ids)))
    db.commit()
    return {"status": "deleted", "count": result.rowcount}


def _build_image_list_items(
    fishes,
    *,
    url_field: str,
) -> list[dict]:
    """构造图片列表项（带 fish_short_id / fish_display_id / video_display_id）"""
    from app.services.fish_id_service import (
        derive_fish_display_id,
        derive_fish_short_id,
        derive_video_display_id,
    )

    items: list[dict] = []
    for f in fishes:
        image_url = to_static_url(getattr(f, url_field))

        created_at = None
        if f.created_at:
            created_at = to_beijing(f.created_at).strftime("%Y-%m-%d %H:%M")

        fish_display_id = derive_fish_display_id(f.fish_id)
        fish_short_id = derive_fish_short_id(f.fish_id) or fish_display_id
        video_display_id = derive_video_display_id(f.video_id)

        # 客户主显示名：短 ID（去掉了 video 哈希部分）
        display_name = fish_short_id

        items.append(
            {
                "id": f.fish_id,
                "url": image_url,
                "name": display_name,
                "time": created_at,
                "fish_id": f.fish_id,
                "fish_display_id": fish_display_id,
                "fish_short_id": fish_short_id,
                "video_id": f.video_id,
                "video_display_id": video_display_id,
            }
        )
    return items


@router.get("/images/original")
def list_original_images(
    video_id: Optional[str] = None,
    fish_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    from app.repositories.models import Fish
    from sqlalchemy import select, func

    stmt = select(Fish).where(Fish.original_image_path != None)
    count_stmt = (
        select(func.count()).select_from(Fish).where(Fish.original_image_path != None)
    )

    filters = [Fish.original_image_path != None]
    if video_id:
        filters.append(Fish.video_id == video_id)
    if fish_id:
        filters.append(Fish.fish_id.like(f"%{fish_id}%"))

    stmt = stmt.where(*filters)
    count_stmt = count_stmt.where(*filters)

    total = db.execute(count_stmt).scalar() or 0

    stmt = stmt.order_by(Fish.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    fishes = db.execute(stmt).scalars().all()
    items = _build_image_list_items(fishes, url_field="original_image_path")

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


@router.get("/images/corrected")
def list_corrected_images(
    video_id: Optional[str] = None,
    fish_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    from app.repositories.models import Fish
    from sqlalchemy import select, func

    stmt = select(Fish).where(Fish.corrected_image_path != None)
    count_stmt = (
        select(func.count()).select_from(Fish).where(Fish.corrected_image_path != None)
    )

    filters = [Fish.corrected_image_path != None]
    if video_id:
        filters.append(Fish.video_id == video_id)
    if fish_id:
        filters.append(Fish.fish_id.like(f"%{fish_id}%"))

    stmt = stmt.where(*filters)
    count_stmt = count_stmt.where(*filters)

    total = db.execute(count_stmt).scalar() or 0

    stmt = stmt.order_by(Fish.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    fishes = db.execute(stmt).scalars().all()
    items = _build_image_list_items(fishes, url_field="corrected_image_path")

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


@router.post("/fishes/export")
def export_fishes(body: dict, db: Session = Depends(get_db)):
    from app.repositories.models import Fish
    from sqlalchemy import select

    fields = body.get("fields", [])
    if not fields:
        fields = ["fish_id", "video_id", "body_length", "body_height", "body_weight", "condition_factor", "surface_damage", "confidence", "created_at"]

    stmt = select(Fish).order_by(Fish.created_at.desc())
    fishes = db.execute(stmt).scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)

    header = []
    for f in fields:
        if f == "confidence":
            header.append("confidence")
        elif f == "created_at":
            header.append("created_at")
        else:
            header.append(f)
    writer.writerow(header)

    for f in fishes:
        row = []
        for field in fields:
            val = getattr(f, field, None)
            if field == "confidence" and val is not None:
                val = round(val * 100, 1)
            elif field == "created_at" and val is not None:
                val = to_beijing(val).strftime("%Y-%m-%d %H:%M")
            row.append(val if val is not None else "")
        writer.writerow(row)

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=fish_export.csv"},
    )


@router.post("/images/original/export")
def export_original_images(body: dict, db: Session = Depends(get_db)):
    from app.repositories.models import Fish
    from sqlalchemy import select

    fish_ids = body.get("fish_ids", [])
    video_id = body.get("video_id")
    fish_id_filter = body.get("fish_id")

    stmt = select(Fish).where(Fish.original_image_path != None)
    if fish_ids:
        stmt = stmt.where(Fish.fish_id.in_(fish_ids))
    if video_id:
        stmt = stmt.where(Fish.video_id == video_id)
    if fish_id_filter:
        stmt = stmt.where(Fish.fish_id.like(f"%{fish_id_filter}%"))

    fishes = db.execute(stmt.order_by(Fish.created_at.desc())).scalars().all()

    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as zf:
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(["fish_id", "video_id", "original_image_path", "created_at"])
        for f in fishes:
            created = to_beijing(f.created_at).strftime("%Y-%m-%d %H:%M") if f.created_at else ""
            writer.writerow([f.fish_id, f.video_id, f.original_image_path or "", created])
        zf.writestr("original_images.csv", csv_buffer.getvalue().encode("utf-8"))

        for f in fishes:
            if f.original_image_path:
                img_path = to_abs_data_path(f.original_image_path)
                if img_path and img_path.exists():
                    zf.write(str(img_path), f.original_image_path)

    memory_file.seek(0)
    return StreamingResponse(
        memory_file,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=original_images_export.zip"},
    )


@router.post("/images/corrected/export")
def export_corrected_images(body: dict, db: Session = Depends(get_db)):
    from app.repositories.models import Fish
    from sqlalchemy import select

    fish_ids = body.get("fish_ids", [])
    video_id = body.get("video_id")
    fish_id_filter = body.get("fish_id")

    stmt = select(Fish).where(Fish.corrected_image_path != None)
    if fish_ids:
        stmt = stmt.where(Fish.fish_id.in_(fish_ids))
    if video_id:
        stmt = stmt.where(Fish.video_id == video_id)
    if fish_id_filter:
        stmt = stmt.where(Fish.fish_id.like(f"%{fish_id_filter}%"))

    fishes = db.execute(stmt.order_by(Fish.created_at.desc())).scalars().all()

    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as zf:
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        writer.writerow(["fish_id", "video_id", "corrected_image_path", "created_at"])
        for f in fishes:
            created = to_beijing(f.created_at).strftime("%Y-%m-%d %H:%M") if f.created_at else ""
            writer.writerow([f.fish_id, f.video_id, f.corrected_image_path or "", created])
        zf.writestr("corrected_images.csv", csv_buffer.getvalue().encode("utf-8"))

        for f in fishes:
            if f.corrected_image_path:
                img_path = to_abs_data_path(f.corrected_image_path)
                if img_path and img_path.exists():
                    zf.write(str(img_path), f.corrected_image_path)

    memory_file.seek(0)
    return StreamingResponse(
        memory_file,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=corrected_images_export.zip"},
    )
