from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.platform_usage_service import (
    get_platform_failures,
    get_platform_usage_charts,
    get_platform_usage_summary,
)


def test_platform_usage_charts_uses_task_count_for_camera_distribution() -> None:
    tasks = [
        SimpleNamespace(
            created_at=datetime(2026, 10, 26, 10, 0),
            total_fish=3,
            status="done",
            started_at=None,
            finished_at=None,
            processing_duration_seconds=None,
        ),
        SimpleNamespace(
            created_at=datetime(2026, 10, 26, 11, 0),
            total_fish=1,
            status="failed",
            started_at=None,
            finished_at=None,
            processing_duration_seconds=None,
        ),
    ]
    db = MagicMock()
    db.execute.return_value.scalars.return_value.all.return_value = tasks

    result = get_platform_usage_charts(db, "all")

    assert result["camera_task_distribution"] == [
        {"label": "摄像头", "count": 2}
    ]
    assert result["daily_video_task_trend"] == [
        {"date": "2026-10-26", "count": 2}
    ]
    assert result["daily_abnormal_task_trend"] == [
        {"date": "2026-10-26", "count": 1}
    ]


def test_platform_usage_summary_exposes_interrupted_and_missing_fps() -> None:
    tasks = [
        SimpleNamespace(
            status="done", total_fish=2, fps_estimate=None,
            processing_duration_seconds=10, video_duration_seconds=20,
            started_at=None, finished_at=None, source_path=None,
        ),
        SimpleNamespace(
            status="interrupted", total_fish=0, fps_estimate=None,
            processing_duration_seconds=None, video_duration_seconds=None,
            started_at=None, finished_at=None, source_path=None,
        ),
    ]
    db = MagicMock()
    db.execute.return_value.scalars.return_value.all.return_value = tasks

    result = get_platform_usage_summary(db, "all")

    assert result["interrupted_task_count"] == 1
    assert result["avg_fps"] is None
    assert result["processing_duration_sample_count"] == 1
    assert result["avg_processing_duration"] == 10
    assert result["max_processing_duration"] == 10


def test_platform_usage_charts_abnormal_trend_includes_zero_days() -> None:
    tasks = [
        SimpleNamespace(
            created_at=datetime(2026, 10, 25, 10, 0), total_fish=1,
            status="done", started_at=None, finished_at=None,
            processing_duration_seconds=None,
        ),
        SimpleNamespace(
            created_at=datetime(2026, 10, 26, 10, 0), total_fish=0,
            status="interrupted", started_at=None, finished_at=None,
            processing_duration_seconds=None,
        ),
    ]
    db = MagicMock()
    db.execute.return_value.scalars.return_value.all.return_value = tasks

    result = get_platform_usage_charts(db, "all")

    assert result["daily_abnormal_task_trend"] == [
        {"date": "2026-10-25", "count": 0},
        {"date": "2026-10-26", "count": 1},
    ]


def test_platform_failures_returns_task_detail_fields() -> None:
    created = datetime(2026, 10, 26, 10, 0)
    task = SimpleNamespace(
        video_id="20261026_0001", failed_stage=None,
        error_message=None,
        last_error="任务状态：失败\n失败阶段：video_to_frames\n错误原因：文件为空",
        created_at=created, updated_at=created, finished_at=created,
    )
    db = MagicMock()
    db.execute.return_value.scalars.return_value.all.return_value = [task]

    result = get_platform_failures(db, "all")

    assert result["items"] == [{
        "video_id": "20261026_0001",
        "stage": "video_to_frames",
        "reason": "文件为空",
        "created_at": created,
    }]


def test_platform_failures_summarizes_missing_error_reason() -> None:
    created = datetime(2026, 10, 26, 10, 0)
    task = SimpleNamespace(
        video_id="20261026_0002", failed_stage=None, error_message=None,
        last_error="失败阶段：video_to_frames\n错误原因：（未提供）",
        created_at=created, updated_at=created, finished_at=created,
    )
    db = MagicMock()
    db.execute.return_value.scalars.return_value.all.return_value = [task]

    item = get_platform_failures(db, "all")["items"][0]

    assert item["stage"] == "video_to_frames"
    assert item["reason"] == "video_to_frames 阶段失败，未提供具体错误，请查看任务日志。"
