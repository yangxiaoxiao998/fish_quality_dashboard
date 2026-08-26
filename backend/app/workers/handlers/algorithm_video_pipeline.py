"""
algorithm_video_pipeline.py - RQ handler：整视频算法 CLI pipeline

调用 algorithm_cli_service.run_video_pipeline_cli，拿到结果后导入数据库。
"""
from __future__ import annotations

from sqlalchemy import select
import logging
from pathlib import Path
from typing import Any

from app.repositories.db import SessionLocal
from app.repositories import session_repo
from app.services import algorithm_cli_service, algorithm_result_service

logger = logging.getLogger("algorithm_video_pipeline")


def _generation_key(video_id: str) -> str:
    return f"fish:algorithm-task:generation:{video_id}"


def _cancel_key(video_id: str) -> str:
    return f"fish:algorithm-task:cancel:{video_id}"


def _is_cancelled(video_id: str | None, task_token: str | None = None) -> bool:
    if not video_id:
        return False
    try:
        from app.infra.redis_client import get_redis
        redis_client = get_redis()
        if redis_client.exists(_cancel_key(video_id)):
            return True
        current = redis_client.get(_generation_key(video_id))
        if current is None or task_token is None:
            return False
        current_value = current.decode() if isinstance(current, bytes) else current
        return current_value != task_token
    except Exception:
        logger.exception("check algorithm cancellation failed: %s", video_id)
        return False


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


def _clear_partial_task_progress(video_id: str | None) -> None:
    """Clear counters from a failed/requeued run so stale progress is not shown."""
    if not video_id:
        return
    try:
        db = SessionLocal()
        try:
            task = session_repo.get_video_task(db, video_id=video_id)
            if task is not None:
                task.total_fish = 0
                task.processed_fish = 0
                task.complete_fish = 0
                task.incomplete_fish = 0
                task.failed_fish = 0
                task.damaged_fish = 0
                task.avg_surface_damage_ratio = None
                task.max_surface_damage_ratio = None
                task.current_fish_id = None
                task.current_algorithm = None
                db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("clear partial video_task progress failed: %s", video_id)


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


def _canonicalize_source_video(video_id: str | None, input_video: str) -> str:
    if not video_id:
        return input_video
    try:
        from app.services.video_playback_service import ensure_browser_playable_video
        from app.services.path_service import to_abs_data_path

        result = ensure_browser_playable_video(video_id)
        playable = to_abs_data_path(result.get("playable_path")) if result.get("ok") else None
        if playable and playable.is_file():
            return str(playable)
    except Exception:
        logger.exception("canonicalize source video failed: %s", video_id)
    return input_video


def _cleanup_video_copies(video_id: str | None) -> None:
    if not video_id:
        return
    try:
        from app.paths import DATA_DIR, JOBS_DIR, RESULTS_DIR

        canonical = DATA_DIR / "uploads" / "videos" / "playable" / f"{video_id}_h264.mp4"
        candidates = [
            DATA_DIR / "uploads" / "videos" / f"{video_id}.mp4",
            DATA_DIR / "uploads" / "videos" / f"{video_id}_h264.mp4",
            JOBS_DIR / video_id / "input" / f"{video_id}_source_video.mp4",
            RESULTS_DIR / video_id / "input" / f"{video_id}_source_video.mp4",
        ]
        for path in candidates:
            if path != canonical:
                path.unlink(missing_ok=True)
    except Exception:
        logger.exception("cleanup source copies failed: %s", video_id)


def _generate_video_report_after_import(video_id: str | None) -> dict[str, Any]:
    """算法入库成功后，单独生成视频鱼品质报告（DOCX + PDF + detection_report 记录）。

    设计约束：
    - video_id 为空时直接返回 skipped=True（不入库也不报错）。
    - 内部新建 SessionLocal，try / finally 关闭。
    - 复用现有 generate_video_fish_quality_pdf（其内部已写 detection_report + LibreOffice 转换）。
    - 不再额外调用 generate_video_pdf_report，避免重复生成。
    - 报告生成失败 **不会** 把 video_task 标记为 failed —— 算法已经入库，
      video_task 必须继续保持 done（让前端能区分"算法完成 / 报告生成失败"）。
    - 所有异常 logger.exception，并返回 ok=False + error 字段。
    """
    if not video_id:
        return {
            "ok": False,
            "skipped": True,
            "error": "video_id is empty",
        }

    # 延迟导入，避免循环依赖
    from app.services.report_pdf_service import generate_video_fish_quality_pdf
    from app.services.report_stats_service import get_fish_quality_summary
    from app.repositories.db import SessionLocal

    db = SessionLocal()
    try:
        try:
            summary = get_fish_quality_summary(db, video_id=video_id)
            report_result = generate_video_fish_quality_pdf(
                db,
                video_id=video_id,
                summary=summary,
            )
            return {
                "ok": True,
                "video_id": video_id,
                "status": "generated",
                "report_result": report_result,
            }
        except Exception as exc:
            logger.exception(
                "[algorithm_video_pipeline] generate video report failed: %s", video_id
            )
            return {
                "ok": False,
                "video_id": video_id,
                "error": f"{type(exc).__name__}: {exc}",
            }
    finally:
        db.close()


def _generate_daily_report_after_task(video_id: str | None) -> dict[str, Any]:
    """更新视频完成日期的日报；日报记录按日期覆盖，始终反映最新任务。"""
    if not video_id:
        return {"ok": False, "skipped": True, "error": "video_id is empty"}
    from app.repositories.db import SessionLocal
    from app.repositories.models import VideoTask, DetectionReport
    from app.services.report_pdf_service import generate_daily_fish_quality_pdf
    from app.services.report_stats_service import get_fish_quality_summary
    from app.utils.time_utils import now_beijing

    db = SessionLocal()
    try:
        video = db.get(VideoTask, video_id)
        if video is None:
            return {"ok": False, "error": f"video task not found: {video_id}"}
        task_date = (video.created_at or now_beijing()).date()
        summary = get_fish_quality_summary(db, report_date=task_date.isoformat())
        existing = db.execute(
            select(DetectionReport)
            .where(DetectionReport.report_date == task_date)
            .where(DetectionReport.report_type == "daily_fish_quality")
            .order_by(DetectionReport.created_at.desc())
        ).scalars().first()
        result = generate_daily_fish_quality_pdf(
            db,
            report_date=task_date,
            summary=summary,
            existing_report_id=existing.report_id if existing else None,
            save_history=True,
        )
        return {"ok": True, "status": "generated", **result}
    except Exception as exc:
        logger.exception("generate daily report failed after task: %s", video_id)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        db.close()


def run_algorithm_video_pipeline(
    *,
    input_video: str,
    upload_video_id: str | None = None,
    camera_id: str = "default",
    device: str = "cuda",
    video_fps: int = 25,
    overwrite: bool = True,
    task_token: str | None = None,
    force_all_complete: bool = False,
) -> dict[str, Any]:
    """
    入口函数：

    1. 标记 video_task=processing
    2. 调用 algorithm_cli_service.run_video_pipeline_cli(input_video=..., video_id=upload_video_id)
    3. CLI 失败 → 标记 video_task=failed + last_error，保留现场，立刻返回 ok=False，**不要** import
    4. CLI 成功 → 读 pipeline_result.json，调用 import_pipeline_result_payload_to_db
    5. 更新 video_task 状态（done / failed）
    6. 入库成功 + video_task=done 后 → 单独调用 _generate_video_report_after_import 生成 DOCX/PDF
       （失败不破坏 video_task done 状态）；全部流程结束后才清理视频副本
    """
    abs_input = str(Path(input_video).expanduser().resolve()) if input_video else ""
    # retry 传入的路径可能来自 dist_release 或旧数据库记录；按 video_id
    # 重新解析本项目的 playable 文件，避免跨部署目录污染算法输入。
    if upload_video_id:
        canonical = _canonicalize_source_video(upload_video_id, abs_input)
        if canonical:
            abs_input = canonical

    if not abs_input or not Path(abs_input).is_file():
        err_summary = f"算法输入视频不存在：{abs_input or '(empty)'}"
        _mark_video_task_failed(upload_video_id, err_summary)
        return {
            "ok": False,
            "upload_video_id": upload_video_id,
            "status": "failed",
            "error_message": err_summary,
        }

    if _is_cancelled(upload_video_id, task_token):
        return {"ok": False, "upload_video_id": upload_video_id, "status": "failed", "cancelled": True}

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
        force_all_complete=force_all_complete,
        cancel_check=lambda: _is_cancelled(upload_video_id, task_token),
    )

    cli_ok = bool(result.get("ok"))
    cli_returncode = result.get("returncode")
    cli_log_path = result.get("log_path")
    cli_error_message = result.get("error_message")

    if result.get("cancelled") or _is_cancelled(upload_video_id, task_token):
        return {
            "ok": False,
            "upload_video_id": upload_video_id,
            "status": "failed",
            "cancelled": True,
            "log_path": cli_log_path,
            "returncode": cli_returncode,
        }

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
        _clear_partial_task_progress(upload_video_id)
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
    if _is_cancelled(upload_video_id, task_token):
        return {"ok": False, "upload_video_id": upload_video_id, "status": "failed", "cancelled": True}
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

    # 算法已成功入库 + status=done + video_id 非空 → 自动生成视频鱼品质报告。
    # 报告生成失败不会修改 video_task 状态（保持 done），仅记录到 report_result。
    report_result: dict[str, Any] = {"ok": False, "skipped": True, "error": "skipped"}
    if import_result and import_result.get("ok") and status == "done" and video_id:
        report_result = _generate_video_report_after_import(video_id)
        daily_report_result = _generate_daily_report_after_task(video_id)
        # 报告生成也结束后才清理输入副本，保证算法及其后处理不再需要源文件。
        _cleanup_video_copies(upload_video_id or video_id)
    else:
        daily_report_result = {"ok": False, "skipped": True, "error": "task not done"}

    return {
        "ok": bool(import_result and import_result.get("ok")),
        "upload_video_id": upload_video_id,
        "video_id": video_id,
        "status": status,
        "import_result": import_result,
        "log_path": cli_log_path,
        "returncode": cli_returncode,
        "report_result": report_result,
        "daily_report_result": daily_report_result,
        "report_ready": bool(
            report_result
            and report_result.get("ok")
            and report_result.get("status") == "generated"
        ),
    }
