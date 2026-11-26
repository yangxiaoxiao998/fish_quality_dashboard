"""Small deterministic smoke test for the reconnect state contract."""
from __future__ import annotations

from datetime import timedelta

from app.services.camera_yzt.camera_yzt_service import CameraRuntimeState, CameraServiceYzt
from app.utils.time_utils import now_beijing


def main() -> int:
    service = CameraServiceYzt()
    service._video_id = "video_new_stability_smoke"
    service.state = CameraRuntimeState.RUNNING
    service._stream_ready = True
    service._begin_reconnect_wait("smoke")
    assert service.state == CameraRuntimeState.RECONNECTING
    assert service._video_id == "video_new_stability_smoke"
    assert service._reconnect_started_at is not None
    service._mark_stream_recovered()
    assert service.state == CameraRuntimeState.RUNNING
    assert service._stream_ready is True
    assert service._video_id == "video_new_stability_smoke"

    # A reconnecting timed recording is still active. Once its deadline is
    # reached the watchdog must stop the session instead of exiting early.
    stop_calls: list[dict] = []
    service.state = CameraRuntimeState.RECONNECTING
    service._session_create_video_task = True
    service._scheduled_stop_at = now_beijing() - timedelta(seconds=1)
    service._watchdog_video_id = service._video_id
    service._watchdog_stop_event.clear()
    service.stop_stream = lambda **kwargs: stop_calls.append(kwargs)  # type: ignore[method-assign]
    service._timed_recording_watchdog_loop()
    assert stop_calls and stop_calls[0]["reason"] == "timed_recording_expired"
    print("reconnect_state_machine: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
