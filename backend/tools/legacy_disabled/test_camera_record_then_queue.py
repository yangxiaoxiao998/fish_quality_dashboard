"""
test_camera_record_then_queue.py - 摄像头"开-录-关-入队"流程最小验证脚本

不在该脚本中：
    - 连接真实 PostgreSQL（用 sqlite 内存或 monkey-patch）
    - 启动 RQ worker（用 monkey-patch 替换 enqueue_stream_ingest）
    - 强依赖真实摄像头（mock state 直接调用 stop_stream）

测试目标：
    1. start 之后 video_task 存在、状态为 recording，stream_ingest 没有入队
    2. stop 时如果视频文件存在且 > 0 字节 -> enqueue_stream_ingest 被调用一次
    3. stop 时如果视频文件不存在或为 0 字节 -> enqueue_stream_ingest 不被调用

可以本地无摄像头直接跑：python tools/test_camera_record_then_queue.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# 用一个临时 DATA_DIR 跑测试，避免污染真实 data/
_TMP_DATA = tempfile.mkdtemp(prefix="fish_cam_test_", suffix="_data")
os.environ["FISH_DATA_ROOT"] = _TMP_DATA
os.environ["FISH_PROJECT_ROOT"] = str(PROJECT_ROOT)
os.environ["FISH_ALGO_ROOT"] = str(PROJECT_ROOT / "algorithms")
# 强制 mock 模式：start_stream 不会走真实算法
os.environ.setdefault("FISH_ALGO_MODE", "mock")


def _make_test_db_engine():
    """建一个 sqlite 内存 engine，并把 SQLAlchemy session 切到它上面。"""
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    # 建表
    from app.repositories.models import Base

    Base.metadata.create_all(engine)
    return engine


def _patch_session_local(engine):
    from sqlalchemy.orm import sessionmaker

    from app.repositories import db as db_module

    TestSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db_module.SessionLocal = TestSessionLocal
    return TestSessionLocal


def _new_video_id() -> str:
    from app.utils.time_utils import now_beijing
    import uuid

    now = now_beijing().strftime("%Y%m%d")
    return f"{now}_test_{uuid.uuid4().hex[:8]}"


def _setup_camera_state(service, video_id: str, video_path: str, frames_written: int = 10):
    """手动把 service 状态推到"运行中"，用于模拟 start 之后的效果。"""
    from app.services.camera_yzt.camera_yzt_service import CameraRuntimeState

    with service._lock:
        service.state = CameraRuntimeState.RUNNING
        service.is_active = True
        service._video_id = video_id
        service._video_path = video_path
        service._started_at = None
        service._frames_captured = 10
        # 设为 0 时 stop_stream 会跳过 ffmpeg 转码路径，便于本地无 ffmpeg 测试。
        service._frames_written = frames_written
        service._fish_detected = 0
        service._frame_idx = 10


def _create_video_task_record(video_id: str, source_rel: str) -> None:
    """在测试 DB 里建一个 video_task。"""
    from app.repositories.db import SessionLocal
    from app.repositories import session_repo

    db = SessionLocal()
    try:
        session_repo.create_video_task(
            db,
            video_id=video_id,
            source_type="rtsp",
            source_path=source_rel,
            rtsp_url="rtsp://mock/stream",
        )
        session_repo.update_video_task_status(
            db,
            video_id=video_id,
            status="recording",
        )
    finally:
        db.close()


def test_start_does_not_enqueue(camera_service) -> dict[str, Any]:
    """
    验证：start 阶段没有 enqueue_stream_ingest 调用。
    """
    enqueue_mock = MagicMock(return_value="job_id_mock_xxx")

    video_id = _new_video_id()
    plan_video_path = Path(_TMP_DATA) / "uploads" / "videos" / f"{video_id}.mp4"
    plan_video_path.parent.mkdir(parents=True, exist_ok=True)
    plan_video_path.write_bytes(b"\x00" * 1024)  # 假文件，让 to_data_relative 通过

    # 直接 patch start_stream 内部要用的 SessionLocal + enqueue
    with patch(
        "app.workers.queue.enqueue_stream_ingest", enqueue_mock
    ), patch(
        "app.services.camera_yzt.camera_yzt_service.SessionLocal",
    ):
        # 不用真的 start，而是手动建 video_task 模拟 start 已完成
        rel = str(plan_video_path.relative_to(_TMP_DATA))
        _create_video_task_record(video_id, rel)
        _setup_camera_state(camera_service, video_id, str(plan_video_path))

    # 核心断言：start 后 enqueue 未被调用
    assert enqueue_mock.call_count == 0, (
        f"start 阶段不应该 enqueue，但 enqueue 被调用了 {enqueue_mock.call_count} 次"
    )
    return {
        "video_id": video_id,
        "video_path": str(plan_video_path),
        "enqueue_called": enqueue_mock.call_count,
    }


def _make_completed_process(returncode: int = 0, stderr: str = ""):
    """构造一个类 subprocess.CompletedProcess 的对象，给 ffmpeg 模拟用。"""
    from subprocess import CompletedProcess

    return CompletedProcess(
        args=["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"],
        returncode=returncode,
        stdout="",
        stderr=stderr,
    )


def _make_rasp_response():
    """构造一个类 requests.Response 的对象（带 .status_code / .text），给树莓派 stop mock 用。"""
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "ok"
    return resp


def test_stop_with_video_enqueues(camera_service) -> dict[str, Any]:
    """
    验证：stop 阶段视频存在 -> enqueue_stream_ingest 被调用一次。
    """
    enqueue_mock = MagicMock(return_value="job_id_stop_xxx")

    video_id = _new_video_id()
    video_abs = Path(_TMP_DATA) / "uploads" / "videos" / f"{video_id}.mp4"
    video_abs.parent.mkdir(parents=True, exist_ok=True)
    # 写一个非空假视频文件
    video_abs.write_bytes(b"\x00\x01\x02" * 1024 + b"FAKE_MP4_DATA")
    rel = str(video_abs.relative_to(_TMP_DATA))

    _create_video_task_record(video_id, rel)
    # frames_written=0 让 stop_stream 跳过 ffmpeg 转码路径（避免本地无 ffmpeg 报错）
    _setup_camera_state(camera_service, video_id, str(video_abs), frames_written=0)

    with patch(
        "app.workers.queue.enqueue_stream_ingest", enqueue_mock
    ), patch(
        "app.services.camera_yzt.camera_yzt_service.requests.post",
        return_value=_make_rasp_response(),
    ):
        result = camera_service.stop_stream()

    # 断言
    assert result["status"] == "stopped", f"unexpected status: {result['status']!r}"
    assert result["queued"] is True, (
        f"stop 视频存在时应该 queued=True，实际 result={result!r}"
    )
    assert enqueue_mock.call_count == 1, (
        f"stop 视频存在时 enqueue 应该被调用 1 次，实际 {enqueue_mock.call_count} 次"
    )
    args, kwargs = enqueue_mock.call_args
    assert args[0] == video_id, f"enqueue 收到的 video_id 应是 {video_id!r}，实际 {args[0]!r}"

    return {
        "video_id": video_id,
        "queued": result["queued"],
        "job_id": result.get("job_id"),
        "queue_error": result.get("queue_error"),
    }


def test_stop_without_video_does_not_enqueue(camera_service) -> dict[str, Any]:
    """
    验证：stop 阶段视频不存在 -> 不 enqueue，queued=False。
    """
    enqueue_mock = MagicMock(return_value="job_id_should_not_xxx")

    video_id = _new_video_id()
    # 注意：这里**不**创建视频文件
    video_abs = Path(_TMP_DATA) / "uploads" / "videos" / f"{video_id}.mp4"
    rel = str(video_abs.relative_to(_TMP_DATA))

    _create_video_task_record(video_id, rel)
    _setup_camera_state(camera_service, video_id, str(video_abs), frames_written=0)

    with patch(
        "app.workers.queue.enqueue_stream_ingest", enqueue_mock
    ), patch(
        "app.services.camera_yzt.camera_yzt_service.requests.post",
        return_value=_make_rasp_response(),
    ):
        result = camera_service.stop_stream()

    assert result["queued"] is False, (
        f"stop 视频不存在时应该 queued=False，实际 result={result!r}"
    )
    assert enqueue_mock.call_count == 0, (
        f"stop 视频不存在时 enqueue 不应被调用，实际 {enqueue_mock.call_count} 次"
    )
    return {
        "video_id": video_id,
        "queued": result["queued"],
        "queue_message": result.get("queue_message"),
        "db_update_error": result.get("db_update_error"),
    }


def test_stop_zero_byte_video_does_not_enqueue(camera_service) -> dict[str, Any]:
    """
    验证：stop 阶段视频存在但大小 == 0 -> 不 enqueue，queued=False。
    """
    enqueue_mock = MagicMock(return_value="job_id_should_not_xxx")

    video_id = _new_video_id()
    video_abs = Path(_TMP_DATA) / "uploads" / "videos" / f"{video_id}.mp4"
    video_abs.parent.mkdir(parents=True, exist_ok=True)
    # 创建一个 0 字节文件
    video_abs.write_bytes(b"")
    rel = str(video_abs.relative_to(_TMP_DATA))

    _create_video_task_record(video_id, rel)
    _setup_camera_state(camera_service, video_id, str(video_abs), frames_written=0)

    with patch(
        "app.workers.queue.enqueue_stream_ingest", enqueue_mock
    ), patch(
        "app.services.camera_yzt.camera_yzt_service.requests.post",
        return_value=_make_rasp_response(),
    ):
        result = camera_service.stop_stream()

    assert result["queued"] is False, (
        f"0 字节视频应该 queued=False，实际 result={result!r}"
    )
    assert enqueue_mock.call_count == 0
    return {
        "video_id": video_id,
        "queued": result["queued"],
        "queue_message": result.get("queue_message"),
    }


def main() -> int:
    print("=" * 60)
    print(" test_camera_record_then_queue ")
    print(f" tmp DATA_DIR = {_TMP_DATA}")
    print("=" * 60)

    # 1) 准备测试 DB
    engine = _make_test_db_engine()
    _patch_session_local(engine)

    # 2) 拿到 camera_service 单例
    from app.services.camera_yzt.camera_yzt_service import camera_service_yzt

    failures: list[str] = []

    # 注意：每个测试用新 video_id，并 cleanup 旧 _video_id/_video_path

    # === Test 1 ===
    print("\n[Test 1] start 后 enqueue 不被调用")
    try:
        r = test_start_does_not_enqueue(camera_service_yzt)
        print(f"  PASS video_id={r['video_id']} enqueue_called={r['enqueue_called']}")
    except AssertionError as e:
        failures.append(f"Test 1: {e}")
        print(f"  FAIL: {e}")
    finally:
        # cleanup
        camera_service_yzt._video_id = None
        camera_service_yzt._video_path = None

    # === Test 2 ===
    print("\n[Test 2] stop 视频存在 -> enqueue")
    try:
        r = test_stop_with_video_enqueues(camera_service_yzt)
        print(
            f"  PASS video_id={r['video_id']} queued={r['queued']} "
            f"job_id={r['job_id']}"
        )
    except AssertionError as e:
        failures.append(f"Test 2: {e}")
        print(f"  FAIL: {e}")
    finally:
        camera_service_yzt._video_id = None
        camera_service_yzt._video_path = None

    # === Test 3 ===
    print("\n[Test 3] stop 视频不存在 -> enqueue 不被调用")
    try:
        r = test_stop_without_video_does_not_enqueue(camera_service_yzt)
        print(
            f"  PASS video_id={r['video_id']} queued={r['queued']} "
            f"queue_message={r['queue_message']!r}"
        )
    except AssertionError as e:
        failures.append(f"Test 3: {e}")
        print(f"  FAIL: {e}")
    finally:
        camera_service_yzt._video_id = None
        camera_service_yzt._video_path = None

    # === Test 4 ===
    print("\n[Test 4] stop 0 字节视频 -> enqueue 不被调用")
    try:
        r = test_stop_zero_byte_video_does_not_enqueue(camera_service_yzt)
        print(
            f"  PASS video_id={r['video_id']} queued={r['queued']} "
            f"queue_message={r['queue_message']!r}"
        )
    except AssertionError as e:
        failures.append(f"Test 4: {e}")
        print(f"  FAIL: {e}")
    finally:
        camera_service_yzt._video_id = None
        camera_service_yzt._video_path = None

    print("\n" + "=" * 60)
    if failures:
        print(f" {len(failures)} test(s) FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(" all 4 tests PASSED")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        # 清理临时 DATA_DIR
        try:
            shutil.rmtree(_TMP_DATA, ignore_errors=True)
        except Exception:
            pass
    sys.exit(rc)
