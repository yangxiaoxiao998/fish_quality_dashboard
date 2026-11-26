"""Shared, defensive metrics for video tasks.

The recording duration and the algorithm processing duration are different
measurements.  Keeping their validation and fallback rules here prevents the
video list, daily report and platform report from showing contradictory data.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite
from typing import Any


TERMINAL_TASK_STATUSES = {"done", "failed", "stopped", "no_fish_detected", "interrupted"}
# Only used for the known old ``finished_at < started_at`` race.  Normal task
# lifecycle timestamps are not capped; a long-running real job still remains
# measurable.  In this legacy case a duration beyond two hours means
# ``updated_at`` was changed by a later operation, not by algorithm completion.
MAX_LEGACY_TIMESTAMP_RECOVERY_SECONDS = 2 * 60 * 60
MAX_LEGACY_MEDIA_MULTIPLIER = 2.0
MAX_LEGACY_ABSOLUTE_RECOVERY_SECONDS = 24 * 60 * 60


def _legacy_recovery_limit_seconds(task: Any) -> float:
    """Return a conservative legacy recovery window for this task.

    Long recordings can legitimately take more than two hours to process.
    For those rows, allow up to twice the retained media duration while still
    capping the fallback at one day so a much later metadata edit is not
    mistaken for the original algorithm completion time.
    """
    media_duration = finite_positive(getattr(task, "video_duration_seconds", None))
    media_based = (media_duration or 0.0) * MAX_LEGACY_MEDIA_MULTIPLIER
    return min(
        MAX_LEGACY_ABSOLUTE_RECOVERY_SECONDS,
        max(float(MAX_LEGACY_TIMESTAMP_RECOVERY_SECONDS), media_based),
    )


def finite_nonnegative(value: Any) -> float | None:
    """Return a finite duration greater than or equal to zero, else ``None``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) and number >= 0 else None


def finite_positive(value: Any) -> float | None:
    """Return a finite media duration greater than zero, else ``None``."""
    number = finite_nonnegative(value)
    return number if number is not None and number > 0 else None


def processing_duration_seconds(
    task: Any,
    *,
    now: datetime | None = None,
    include_active: bool = False,
) -> float | None:
    """Get a trustworthy processing duration without accepting negative data.

    Completed tasks may use ``finished_at - started_at`` when an old row has
    no persisted duration.  Active tasks only use the current time when the
    caller explicitly asks for a live display value.
    """
    start = getattr(task, "started_at", None)
    finished = getattr(task, "finished_at", None)
    stored = finite_nonnegative(getattr(task, "processing_duration_seconds", None))
    if stored is not None:
        try:
            legacy_reversed = finished is not None and start is not None and finished < start
        except TypeError:
            legacy_reversed = False
        if legacy_reversed and stored > _legacy_recovery_limit_seconds(task):
            return None
        return stored

    if start is None:
        return None
    end = finished
    if end is None and include_active and str(getattr(task, "status", "")).lower() not in TERMINAL_TASK_STATUSES:
        end = now
    if end is None:
        return None
    try:
        elapsed = finite_nonnegative((end - start).total_seconds())
    except (TypeError, ValueError, AttributeError):
        return None
    if elapsed is not None:
        return elapsed

    # Older camera tasks could write ``finished_at`` while stopping the
    # recording, then have the worker write ``started_at`` a moment later.
    # In that known ordering bug, SQLAlchemy's updated_at is the import finish
    # time.  It is a safe legacy fallback only when the recorded timestamps
    # are explicitly reversed; it is not used for ordinary missing data.
    updated = getattr(task, "updated_at", None)
    try:
        recovered = finite_nonnegative((updated - start).total_seconds())
        if recovered is None or recovered > _legacy_recovery_limit_seconds(task):
            return None
        return recovered
    except (TypeError, ValueError, AttributeError):
        return None


def effective_finished_at(task: Any) -> datetime | None:
    """Return the display-safe task finish timestamp.

    Normal rows keep their persisted ``finished_at``.  For the known legacy
    camera ordering race, reconstruct the finish from the recovered processing
    duration so detail pages never show an end before the start.
    """
    start = getattr(task, "started_at", None)
    finished = getattr(task, "finished_at", None)
    if start is None:
        return finished
    try:
        if finished is not None and finished >= start:
            return finished
    except TypeError:
        return finished

    duration = processing_duration_seconds(task)
    if duration is None:
        return finished if finished is not None else None
    try:
        return start + timedelta(seconds=duration)
    except (TypeError, ValueError, OverflowError):
        return finished


def recording_started_at(
    task: Any,
    *,
    media_duration_seconds: float | None = None,
) -> datetime | None:
    """Return the recording start, never the algorithm start timestamp.

    New camera tasks persist the first captured-frame time. For historical
    tasks, an actual stop plus media duration is the best reconstruction;
    otherwise task creation is the only available recording-side timestamp.
    """
    persisted = getattr(task, "recording_started_at", None)
    if persisted is not None:
        return persisted

    status = str(getattr(task, "status", "") or "").lower()
    if status in {"recording", "running"}:
        # The camera has not delivered its first frame yet. Task creation is
        # only a request timestamp and must not start the recording clock.
        return None

    stop = getattr(task, "actual_stop_at", None)
    duration = finite_positive(media_duration_seconds)
    if duration is None:
        duration = finite_positive(getattr(task, "video_duration_seconds", None))
    if stop is not None and duration is not None:
        try:
            return stop - timedelta(seconds=duration)
        except (TypeError, ValueError, OverflowError):
            pass
    return getattr(task, "created_at", None)


def recording_finished_at(
    task: Any,
    *,
    start: datetime | None = None,
    media_duration_seconds: float | None = None,
) -> datetime | None:
    """Return the recording stop, never the algorithm finish timestamp."""
    stop = getattr(task, "actual_stop_at", None)
    if stop is not None:
        return stop

    status = str(getattr(task, "status", "") or "").lower()
    if status in {"recording", "running"}:
        return None

    duration = finite_positive(media_duration_seconds)
    if duration is None:
        duration = finite_positive(getattr(task, "video_duration_seconds", None))
    recording_start = start or recording_started_at(
        task,
        media_duration_seconds=duration,
    )
    if recording_start is None or duration is None:
        return None
    try:
        return recording_start + timedelta(seconds=duration)
    except (TypeError, ValueError, OverflowError):
        return None


def video_duration_seconds(task: Any, *, stored_path: str | None = None) -> float | None:
    """Get the recorded media duration, probing the retained runtime video once.

    The database value is preferred.  Historical rows which missed the normal
    recording-finalization path fall back to the canonical playable/source
    file.  Callers may assign the returned value back to the ORM task and
    commit when durable backfill is desired.
    """
    stored = finite_positive(getattr(task, "video_duration_seconds", None))
    if stored is not None:
        return stored

    try:
        from app.services.video_playback_service import (
            probe_media_duration_seconds,
            resolve_canonical_video_path,
        )

        path = resolve_canonical_video_path(
            str(getattr(task, "video_id", "") or ""),
            stored_path if stored_path is not None else getattr(task, "source_path", None),
        )
        if path is None:
            return None
        return finite_positive(probe_media_duration_seconds(path))
    except Exception:
        # A missing historical file must not make an entire report fail.
        return None
