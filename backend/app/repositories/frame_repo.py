# 作用：数据库操作的封装
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.repositories.models import FrameFishDetection, VideoFrame
from app.services.path_service import to_data_relative

# 视频帧检测相关的数据库操作
def upsert_video_frame(
    db: Session,
    *,
    video_id: str,
    frame_index: int,
    frame_time_ms: int | None = None,
    frame_image_path: str | None = None,
) -> VideoFrame:
    obj = (
        db.query(VideoFrame)
        .filter(
            VideoFrame.video_id == video_id,
            VideoFrame.frame_index == frame_index,
        )
        .first()
    )

    rel_frame_image_path = (
        to_data_relative(frame_image_path) if frame_image_path else None
    )

    if obj is None:
        obj = VideoFrame(
            video_id=video_id,
            frame_index=frame_index,
            frame_time_ms=frame_time_ms,
            frame_image_path=rel_frame_image_path,
        )
        db.add(obj)
    else:
        obj.frame_time_ms = frame_time_ms
        obj.frame_image_path = rel_frame_image_path

    db.flush()
    return obj

# 视频帧检测结果相关的数据库操作
# 详细描述：
# 创建一个名为 create_frame_fish_detection 的函数，用于创建视频帧检测结果。
def create_frame_fish_detection(
    db: Session,
    *,
    video_id: str,
    frame_index: int,
    fish_id: str,
    track_id: int | str | None,
    bbox_x1: float,
    bbox_y1: float,
    bbox_x2: float,
    bbox_y2: float,
    confidence: float | None = None,
    frame_id: int | None = None,
    frame_time_ms: int | None = None,
    crop_image_path: str | None = None,
    mask_image_path: str | None = None,
    corrected_image_path: str | None = None,
) -> FrameFishDetection:
    center_x = (float(bbox_x1) + float(bbox_x2)) / 2.0
    center_y = (float(bbox_y1) + float(bbox_y2)) / 2.0

    obj = FrameFishDetection(
        video_id=video_id,
        frame_id=frame_id,
        frame_index=frame_index,
        frame_time_ms=frame_time_ms,
        fish_id=fish_id,
        track_id=None if track_id is None else str(track_id),
        bbox_x1=float(bbox_x1),
        bbox_y1=float(bbox_y1),
        bbox_x2=float(bbox_x2),
        bbox_y2=float(bbox_y2),
        center_x=center_x,
        center_y=center_y,
        confidence=confidence,
        crop_image_path=to_data_relative(crop_image_path) if crop_image_path else None,
        mask_image_path=to_data_relative(mask_image_path) if mask_image_path else None,
        corrected_image_path=to_data_relative(corrected_image_path)
        if corrected_image_path
        else None,
    )

    db.add(obj)
    db.flush()
    return obj

# 获取视频帧的检测结果
def list_frame_fish_detections(
    db: Session,
    *,
    video_id: str,
    frame_index: int,
) -> list[FrameFishDetection]:
    return (
        db.query(FrameFishDetection)
        .filter(
            FrameFishDetection.video_id == video_id,
            FrameFishDetection.frame_index == frame_index,
        )
        .order_by(FrameFishDetection.id.asc())
        .all()
    )

# 创建视频帧和帧的检测结果
def create_frame_with_detections(
    db: Session,
    *,
    video_id: str,
    frame_index: int,
    frame_time_ms: int | None = None,
    frame_image_path: str | None = None,
    detections: list[dict[str, Any]],
) -> tuple[VideoFrame, list[FrameFishDetection]]:
    frame = upsert_video_frame(
        db,
        video_id=video_id,
        frame_index=frame_index,
        frame_time_ms=frame_time_ms,
        frame_image_path=frame_image_path,
    )

    items: list[FrameFishDetection] = []
    for det in detections:
        item = create_frame_fish_detection(
            db,
            video_id=video_id,
            frame_id=frame.frame_id,
            frame_index=frame_index,
            frame_time_ms=frame_time_ms,
            fish_id=det["fish_id"],
            track_id=det.get("track_id"),
            bbox_x1=det["bbox_x1"],
            bbox_y1=det["bbox_y1"],
            bbox_x2=det["bbox_x2"],
            bbox_y2=det["bbox_y2"],
            confidence=det.get("confidence"),
            crop_image_path=det.get("crop_image_path"),
            mask_image_path=det.get("mask_image_path"),
            corrected_image_path=det.get("corrected_image_path"),
        )
        items.append(item)

    return frame, items