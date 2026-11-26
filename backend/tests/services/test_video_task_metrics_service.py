from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from app.repositories import session_repo
from app.utils.time_utils import now_beijing
from app.services.video_task_metrics_service import (
    effective_finished_at,
    processing_duration_seconds,
    recording_finished_at,
    recording_started_at,
    video_duration_seconds,
)


def test_processing_duration_uses_valid_timestamps_and_rejects_negative() -> None:
    start = now_beijing()
    valid = SimpleNamespace(
        processing_duration_seconds=None,
        started_at=start,
        finished_at=start + timedelta(seconds=12.5),
        status="done",
    )
    invalid = SimpleNamespace(
        processing_duration_seconds=-3,
        started_at=start,
        finished_at=start - timedelta(seconds=1),
        status="done",
    )

    assert processing_duration_seconds(valid) == 12.5
    assert processing_duration_seconds(invalid) is None


def test_processing_duration_recovers_the_known_legacy_timestamp_ordering() -> None:
    start = now_beijing()
    task = SimpleNamespace(
        processing_duration_seconds=None,
        started_at=start,
        finished_at=start - timedelta(milliseconds=100),
        updated_at=start + timedelta(seconds=56),
        status="done",
    )

    assert processing_duration_seconds(task) == 56.0


def test_processing_duration_rejects_stale_legacy_updated_at() -> None:
    start = now_beijing()
    task = SimpleNamespace(
        processing_duration_seconds=None,
        started_at=start,
        finished_at=start - timedelta(milliseconds=100),
        updated_at=start + timedelta(days=2),
        status="done",
    )

    assert processing_duration_seconds(task) is None


def test_long_video_legacy_timestamp_recovery_uses_media_duration_window() -> None:
    start = now_beijing()
    task = SimpleNamespace(
        processing_duration_seconds=None,
        video_duration_seconds=6.5 * 60 * 60,
        started_at=start,
        finished_at=start - timedelta(milliseconds=100),
        updated_at=start + timedelta(hours=4, minutes=5),
        status="done",
    )

    assert processing_duration_seconds(task) == 4 * 60 * 60 + 5 * 60
    assert effective_finished_at(task) == start + timedelta(hours=4, minutes=5)


def test_media_duration_keeps_only_a_valid_stored_value() -> None:
    assert video_duration_seconds(SimpleNamespace(video_duration_seconds=52.36)) == 52.36
    # Empty/invalid historical values may probe a file, but a task with no
    # retained source must still return a clean null instead of NaN/negative.
    assert video_duration_seconds(
        SimpleNamespace(video_id="missing", source_path=None, video_duration_seconds=-1)
    ) is None


def test_recording_timestamps_never_use_algorithm_timestamps() -> None:
    created = now_beijing()
    algorithm_start = created + timedelta(minutes=5)
    first_frame = created + timedelta(seconds=2)
    stop = created + timedelta(minutes=1, seconds=2)
    task = SimpleNamespace(
        status="done",
        created_at=created,
        recording_started_at=first_frame,
        actual_stop_at=stop,
        started_at=algorithm_start,
        finished_at=algorithm_start + timedelta(seconds=30),
        video_duration_seconds=60,
    )

    assert recording_started_at(task) == first_frame
    assert recording_finished_at(task) == stop


def test_historical_recording_timestamps_are_reconstructed_from_media() -> None:
    stop = now_beijing()
    task = SimpleNamespace(
        status="done",
        created_at=stop - timedelta(minutes=3),
        recording_started_at=None,
        actual_stop_at=stop,
        video_duration_seconds=60,
    )

    start = recording_started_at(task)
    assert start == stop - timedelta(seconds=60)
    assert recording_finished_at(task, start=start) == stop


def test_active_recording_has_no_finish_without_actual_stop() -> None:
    start = now_beijing()
    task = SimpleNamespace(
        status="recording",
        created_at=start,
        recording_started_at=start,
        actual_stop_at=None,
        video_duration_seconds=None,
    )

    assert recording_finished_at(task, start=start) is None


def test_active_recording_waits_for_first_frame_before_showing_start() -> None:
    task = SimpleNamespace(
        status="recording",
        created_at=now_beijing(),
        recording_started_at=None,
        actual_stop_at=None,
        video_duration_seconds=None,
    )

    assert recording_started_at(task) is None


def test_marking_done_persists_elapsed_processing_time(monkeypatch) -> None:
    start = now_beijing()
    finish = start + timedelta(seconds=18.25)
    task = SimpleNamespace(
        status="processing",
        last_error=None,
        started_at=start,
        finished_at=None,
        processing_duration_seconds=None,
        total_fish=0,
        processed_fish=0,
    )

    class FakeSession:
        def get(self, _model, video_id):
            assert video_id == "video-1"
            return task

        def commit(self):
            return None

    monkeypatch.setattr(session_repo, "now_beijing", lambda: finish)
    session_repo.update_video_task_status(FakeSession(), video_id="video-1", status="done")

    assert task.status == "done"
    assert task.finished_at == finish
    assert task.processing_duration_seconds == 18.25
