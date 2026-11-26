from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.services import ops_algorithm_task_service as service


def test_timing_entry_preserves_zero_duration() -> None:
    value = {
        "started_at": "2026-10-27T10:00:00+08:00",
        "finished_at": "2026-10-27T10:00:00+08:00",
        "elapsed_seconds": 0.0,
    }

    timing = service._timing_entry(value)

    assert timing["elapsed_seconds"] == 0.0
    assert timing["timing_source"] == "recorded"


def test_worker_log_prefers_runtime_directory(monkeypatch, tmp_path: Path) -> None:
    runtime_log = tmp_path / "logs" / "runtime" / "worker.log"
    runtime_log.parent.mkdir(parents=True)
    runtime_log.write_text("video_id=video-1 finished\n", encoding="utf-8")
    monkeypatch.setattr(service, "DATA_DIR", tmp_path)

    result = service.find_worker_log_tail("video-1")

    assert result["exists"] is True
    assert result["path"] == str(runtime_log)
    assert "video-1" in result["tail"]


def test_stage_timeline_uses_recorded_stage_timings() -> None:
    started = datetime(2026, 10, 27, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    task = SimpleNamespace(
        video_id="video-1",
        status="done",
        started_at=started,
        finished_at=started + timedelta(seconds=9),
        last_error=None,
    )
    pipeline_state = {
        "status": "done",
        "completed_stages": [key for key, _ in service.STAGES],
        "stage_timings": {
            "calibrate": {
                "started_at": started.isoformat(),
                "finished_at": (started + timedelta(seconds=2)).isoformat(),
                "elapsed_seconds": 2.0,
            }
        },
    }

    timeline = service._build_stage_timeline(
        {"status": "done"}, pipeline_state, "", None, task
    )

    calibrate = next(item for item in timeline if item["key"] == "calibrate")
    assert calibrate["elapsed_seconds"] == 2.0
    assert calibrate["timing_source"] == "recorded"
    assert calibrate["started_at"] is not None
