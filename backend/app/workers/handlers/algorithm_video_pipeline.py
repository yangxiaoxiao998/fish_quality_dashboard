"""
algorithm_video_pipeline.py - RQ handler：整视频算法 CLI pipeline

调用 algorithm_cli_service.run_video_pipeline_cli，拿到结果后导入数据库。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.repositories.db import SessionLocal
from app.repositories import session_repo
from app.services import algorithm_cli_service, algorithm_result_service

logger = logging.getLogger("algorithm_video_pipeline")


def _mark_video_task_failed(video_id: str | None, last_error: str) -> None:
    """把 video_task 标记为 failed，写入 last_error（限长 2000 字符）。"""
    if not video_id:
        return
    try:
        db = SessionLocal()
        try:
            session_repo.update_video_task_status(
                db,
                video_id=video_id,
                status="failed",
                last_error=(last_error or "algorithm pipeline failed")[:2000],
            )
        finally:
            db.close()
    except Exception:
        logger.exception("mark video_task failed: %s", video_id)


def _mark_video_task_processing(video_id: str | None) -> None:
    """把 video_task 标记为 processing。"""
    if not video_id:
        return
    try:
        db = SessionLocal()
        try:
            session_repo.update_video_task_status(
                db, video_id=video_id, status="processing"
            )
            db.commit()
        except Exception:
            logger.exception("update video_task processing failed: %s", video_id)
            db.rollback()
        finally:
            db.close()
    except Exception:
        logger.exception("mark processing outer failed: %s", video_id)


def run_algorithm_video_pipeline(
    *,
    input_video: str,
    upload_video_id: str | None = None,
    camera_id: str = "default",
    device: str = "cuda",
    video_fps: int = 25,
    overwrite: bool = True,
) -> dict[str, Any]:
    """
    入口函数：

    1. 标记 video_task=processing
    2. 调用 algorithm_cli_service.run_video_pipeline_cli(input_video=..., video_id=upload_video_id)
    3. CLI 失败 → 标记 video_task=failed + last_error，立刻返回 ok=False，**不要** import
    4. CLI 成功 → 读 pipeline_result.json，调用 import_pipeline_result_payload_to_db
    5. 更新 video_task 状态（done / failed）
    """
    abs_input = str(Path(input_video).expanduser().resolve()) if input_video else ""

    _mark_video_task_processing(upload_video_id)

    result = algorithm_cli_service.run_video_pipeline_cli(
        input_video=abs_input,
        camera_id=camera_id,
        device=device,
        video_fps=video_fps,
        overwrite=overwrite,
        # 摄像头录制时已经生成了 video_id，必须让算法 CLI 沿用同一 video_id，
        # 避免算法结果目录落在 data/results/<随机 video_id>/ 而非 data/results/<摄像头 video_id>/
        video_id=upload_video_id,
    )

    cli_ok = bool(result.get("ok"))
    cli_returncode = result.get("returncode")
    cli_log_path = result.get("log_path")
    cli_error_message = result.get("error_message")

    # CLI 失败：先把 video_task 标记 failed，写 last_error，然后立刻返回 ok=False
    # 注意：不要 import，避免把半成品 / 错误的 pipeline_result 写进数据库。
    if not cli_ok:
        err_summary = (
            cli_error_message
            or (result.get("stderr") or "")
            or (result.get("stdout") or "")
            or "algorithm pipeline failed"
        )
        logger.error(
            "[algorithm_video_pipeline] CLI failed: "
            "upload_video_id=%s returncode=%s log_path=%s",
            upload_video_id,
            cli_returncode,
            cli_log_path,
        )
        _mark_video_task_failed(upload_video_id, err_summary)
        # 算法 CLI 失败，但视频数据已生成（playable 等），前端应能看到 status=failed
        return {
            "ok": False,
            "upload_video_id": upload_video_id,
            "video_id": result.get("video_id") or upload_video_id,
            "status": "failed",
            "error_message": err_summary[:2000],
            "log_path": cli_log_path,
            "returncode": cli_returncode,
        }

    # CLI 成功：读 pipeline_result.json + 入库
    video_id = result.get("video_id") or upload_video_id
    pipeline_result = result.get("pipeline_result") or {}
    status = result.get("status") or (
        pipeline_result.get("status") if isinstance(pipeline_result, dict) else None
    )

    # 兜底：如果上游漏写 video_id 到 pipeline_result，用已知 video_id 补
    if isinstance(pipeline_result, dict) and not pipeline_result.get("video_id"):
        pipeline_result["video_id"] = video_id

    # 兜底 video_id 标注（CLI 跑出来的 video_id 与上传 video_id 不一致时记录到 last_error）
    if upload_video_id and video_id and video_id != upload_video_id:
        try:
            db = SessionLocal()
            try:
                upload_task = session_repo.get_video_task(db, video_id=upload_video_id)
                if upload_task is not None and (not upload_task.last_error):
                    upload_task.last_error = (
                        f"算法返回 video_id={video_id}（与上传 video_id 不同），"
                        f"主结果以算法 video_id 为准。"
                    )
                    db.commit()
            finally:
                db.close()
        except Exception:
            logger.exception("annotate upload video_task: %s", upload_video_id)

    if not video_id or not isinstance(pipeline_result, dict) or not pipeline_result.get("video_id"):
        # 算法 CLI 返回 ok=True 但 pipeline_result 仍无效：也按失败处理
        err_summary = (
            "algorithm pipeline succeeded (ok=True) but pipeline_result is missing video_id"
        )
        _mark_video_task_failed(upload_video_id, err_summary)
        return {
            "ok": False,
            "upload_video_id": upload_video_id,
            "video_id": video_id,
            "status": status,
            "error_message": err_summary,
            "log_path": cli_log_path,
            "returncode": cli_returncode,
        }

    import_result: dict[str, Any] | None = None
    db = SessionLocal()
    try:
        import_result = algorithm_result_service.import_pipeline_result_payload_to_db(
            db, pipeline_result, commit=True,
        )
    except Exception as exc:
        logger.exception("import pipeline_result failed: %s", video_id)
        import_result = {
            "ok": False,
            "video_id": video_id,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        db.close()

    if not (import_result and import_result.get("ok")):
        # 入库失败：video_task 标记 failed，把异常信息写 last_error
        _mark_video_task_failed(
            upload_video_id or video_id,
            (import_result or {}).get("error")
            or "import pipeline_result to db failed",
        )

    return {
        "ok": bool(import_result and import_result.get("ok")),
        "upload_video_id": upload_video_id,
        "video_id": video_id,
        "status": status,
        "import_result": import_result,
        "log_path": cli_log_path,
        "returncode": cli_returncode,
    }
