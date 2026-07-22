# /repositories/models.py
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from sqlalchemy import (
    String,
    BigInteger,
    Integer,
    Float,
    DateTime,
    Date,
    Text,
    ForeignKey,
    UniqueConstraint,
    Boolean,
    JSON,
    Index,
    Column,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, declarative_base
from sqlalchemy.sql import func

from app.repositories.db import Base


def get_beijing_time() -> datetime:
    """获取北京时间 (UTC+8)"""
    return datetime.now(timezone(timedelta(hours=8)))

class VideoFrame(Base):
    __tablename__ = "video_frame"

    frame_id = Column(Integer, primary_key=True, index=True)
    video_id = Column(String(128), nullable=False, index=True)
    frame_index = Column(Integer, nullable=False, index=True)
    frame_time_ms = Column(Integer, nullable=True)
    frame_image_path = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("video_id", "frame_index", name="uq_video_frame_video_index"),
        Index("idx_video_frame_video_index", "video_id", "frame_index"),
    )


class VideoTask(Base):
    __tablename__ = "video_task"

    video_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    source_type: Mapped[str] = mapped_column(
        String(32), default="upload"
    )  # upload / rtsp
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    rtsp_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 旧字段，保留兼容
    status: Mapped[str] = mapped_column(
        String(32), default="queued"
    )  # queued/processing/done/failed/stopped
    total_fish: Mapped[int] = mapped_column(Integer, default=0)
    processed_fish: Mapped[int] = mapped_column(Integer, default=0)

    current_fish_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_algorithm: Mapped[str | None] = mapped_column(String(64), nullable=True)

    fps_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ============ 算法最新字段（第一阶段新增） ============
    # 视频相关
    original_input_video: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_video: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 算法阶段相关
    failed_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 算法输出路径（相对 data/ 的 POSIX 路径）
    result_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    pipeline_result_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_conclusion_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    grounded_video_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    measurement_csv_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    complete_match_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    incomplete_match_dir: Mapped[str | None] = mapped_column(Text, nullable=True)

    # summary 字段
    complete_fish: Mapped[int] = mapped_column(Integer, default=0)
    incomplete_fish: Mapped[int] = mapped_column(Integer, default=0)
    failed_fish: Mapped[int] = mapped_column(Integer, default=0)
    damaged_fish: Mapped[int] = mapped_column(Integer, default=0)
    avg_surface_damage_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_surface_damage_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ============ 定时录制字段（第一阶段新增） ============
    # 用户开始录制时选择的总时长（秒）；NULL 表示未设置定时录制。
    record_duration_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    # 计划自动停止时间（start_stream 时根据 started_at + duration 计算）。
    scheduled_stop_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 实际停止时间（手动 stop 或 watchdog 自动停止都会写）。
    actual_stop_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 停止原因：user / api / auto_watchdog / startup_failed / ...
    stop_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 停止来源：Dashboard.vue / CameraSettings.vue / api / watchdog / ...
    stop_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 是否由 watchdog 自动停止（手动停止为 False）。
    auto_stop_triggered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    fishes = relationship("Fish", back_populates="video_task")

    __table_args__ = (
        Index("idx_video_task_status_created", "status", "created_at"),
        Index("idx_video_task_scheduled_stop", "status", "scheduled_stop_at"),
    )


class FrameFishDetection(Base):
    __tablename__ = "frame_fish_detection"

    id = Column(Integer, primary_key=True, index=True)

    video_id = Column(String(128), nullable=False, index=True)
    frame_id = Column(Integer, nullable=True, index=True)
    frame_index = Column(Integer, nullable=False, index=True)
    frame_time_ms = Column(Integer, nullable=True)

    fish_id = Column(String(255), nullable=False, index=True)
    track_id = Column(String(128), nullable=True, index=True)

    bbox_x1 = Column(Float, nullable=False)
    bbox_y1 = Column(Float, nullable=False)
    bbox_x2 = Column(Float, nullable=False)
    bbox_y2 = Column(Float, nullable=False)

    center_x = Column(Float, nullable=False)
    center_y = Column(Float, nullable=False)

    confidence = Column(Float, nullable=True)

    crop_image_path = Column(Text, nullable=True)
    mask_image_path = Column(Text, nullable=True)
    corrected_image_path = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_frame_fish_video_frame", "video_id", "frame_index"),
        Index("idx_frame_fish_video_fish", "video_id", "fish_id"),
        Index("idx_frame_fish_track", "track_id"),
    )


class Fish(Base):
    __tablename__ = "fish"

    # 旧字段（保留兼容，新流程不再写入）
    fish_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    video_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("video_task.video_id", ondelete="CASCADE"), index=True
    )

    track_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)

    bbox_x1: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_y1: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_x2: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_y2: Mapped[float | None] = mapped_column(Float, nullable=True)

    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 旧图片路径字段（保留兼容，新流程不写）
    original_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    mask_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    wound_mask_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(32), default="processing"
    )  # processing/done/failed/incomplete
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 旧数值字段（保留兼容，新流程同时写 cm / pred_weight_g）
    body_length: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_length_without_tail: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_height: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    condition_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    surface_damage: Mapped[str | None] = mapped_column(Text, nullable=True)
    surface_damage_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ============ 算法最新字段（第一阶段新增） ============
    fish_uid: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    frame_index: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    crop_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_length_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_length_no_tail_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    pred_weight_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    video_task = relationship("VideoTask", back_populates="fishes")

    __table_args__ = (
        Index("idx_fish_video_track", "video_id", "track_id"),
        Index("idx_fish_video_uid", "video_id", "fish_uid"),
        Index("idx_fish_video_status", "video_id", "status"),
        Index("idx_fish_video_damage", "video_id", "surface_damage_ratio"),
    )


class Stats5Min(Base):
    __tablename__ = "stats_5min"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    video_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    bucket_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    count: Mapped[int] = mapped_column(Integer, default=0)

    sum_body_length: Mapped[float] = mapped_column(Float, default=0.0)
    sum_body_height: Mapped[float] = mapped_column(Float, default=0.0)
    sum_body_weight: Mapped[float] = mapped_column(Float, default=0.0)
    sum_condition_factor: Mapped[float] = mapped_column(Float, default=0.0)

    abnormal_count: Mapped[int] = mapped_column(Integer, default=0)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("video_id", "bucket_start", name="uq_stats5min_video_bucket"),
    )


class DetectionReport(Base):
    __tablename__ = "detection_report"

    report_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    report_type: Mapped[str] = mapped_column(String(64), nullable=False, default="daily_fish_quality")
    # 视频报表关联 video_id：日报 video_id=NULL，视频报表 video_id=<具体 video_id>。
    # 同一 video_id 重复生成时由应用层覆盖逻辑保证唯一 video_fish_quality 记录。
    video_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    report_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    report_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    total_video: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_fish: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    abnormal_fish: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="generated")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    usb_backup_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_detected")
    usb_backup_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    daily_conclusion: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )


class OperationLog(Base):
    __tablename__ = "operation_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    module: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    action_label: Mapped[str] = mapped_column(String(128), nullable=False)

    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    request_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    request_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    operator: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True
    )
