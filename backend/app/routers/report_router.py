from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.repositories import report_repo
from app.repositories.db import get_db
from app.repositories.operation_log_repo import list_operation_logs
from app.services import platform_usage_service, report_stats_service
from app.services.operation_log_service import log_operation
from app.services.path_service import to_abs_data_path

router = APIRouter(prefix="/reports")


@router.get("/health")
def report_health() -> dict:
    return {"status": "ok", "module": "reports"}


# ----------------- 鱼品质检测报表 -----------------

@router.get("/fish-quality/summary")
def fish_quality_summary(
    request: Request,
    report_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    video_id: str | None = None,
    db: Session = Depends(get_db),
):
    summary = report_stats_service.get_fish_quality_summary(
        db,
        report_date=report_date,
        start_date=start_date,
        end_date=end_date,
        video_id=video_id,
    )

    log_operation(
        db,
        request=request,
        module="数据报表",
        action="query_fish_quality_summary",
        action_label="查询鱼品质检测报表汇总",
        target_type="report",
        target_id=str(video_id or report_date or start_date or ""),
        success=True,
    )
    return summary


@router.get("/fish-quality/charts")
def fish_quality_charts(
    report_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    video_id: str | None = None,
    db: Session = Depends(get_db),
):
    return report_stats_service.get_fish_quality_charts(
        db,
        report_date=report_date,
        start_date=start_date,
        end_date=end_date,
        video_id=video_id,
    )


@router.get("/fish-quality/fishes")
def fish_quality_fishes(
    report_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    video_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    return report_stats_service.get_fish_quality_fishes(
        db,
        report_date=report_date,
        start_date=start_date,
        end_date=end_date,
        video_id=video_id,
        page=page,
        page_size=page_size,
    )


@router.get("/fish-quality/videos")
def fish_quality_videos(
    report_date: str,
    db: Session = Depends(get_db),
):
    return {
        "items": report_stats_service.get_videos_by_date(
            db, report_date=report_date
        )
    }


@router.post("/fish-quality/daily-pdf/generate")
def generate_daily_pdf(
    body: dict,
    request: Request,
    db: Session = Depends(get_db),
):
    report_date_str = body.get("report_date")
    if not report_date_str:
        raise HTTPException(status_code=400, detail="report_date is required")

    try:
        report_date = date.fromisoformat(report_date_str[:10])
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="report_date 格式错误，应为 YYYY-MM-DD")

    summary = report_stats_service.get_fish_quality_summary(
        db, report_date=report_date.isoformat()
    )
    if summary["total_fish"] <= 0:
        raise HTTPException(
            status_code=400,
            detail="当前选择日期无检测数据，无法生成报告",
        )

    from app.services.report_pdf_service import generate_daily_fish_quality_pdf

    # 日报覆盖：同 report_date + report_type='daily_fish_quality' 已存在时
    # 传 existing_report_id 给 service，让其更新原记录而不是新增一条。
    # 避免同一天多次点"生成日报"出现重复多条 detection_report 记录。
    existing_id: int | None = None
    try:
        from sqlalchemy import select as _select
        from app.repositories.models import DetectionReport as _DR
        existing_row = db.execute(
            _select(_DR)
            .where(_DR.report_date == report_date)
            .where(_DR.report_type == "daily_fish_quality")
            .order_by(_DR.created_at.desc())
        ).scalars().first()
        if existing_row is not None:
            existing_id = existing_row.report_id
    except Exception:
        # 查不到 existing 不影响生成；让 service 走新建分支
        existing_id = None

    try:
        result = generate_daily_fish_quality_pdf(
            db,
            report_date=report_date,
            summary=summary,
            existing_report_id=existing_id,
        )
    except Exception as e:
        log_operation(
            db,
            request=request,
            module="数据报表",
            action="generate_daily_report",
            action_label="生成当日检测报告",
            target_type="report",
            target_id=report_date.isoformat(),
            success=False,
            error_message=str(e),
        )
        raise HTTPException(status_code=500, detail=f"生成 PDF 失败: {e!s}")

    log_operation(
        db,
        request=request,
        module="数据报表",
        action="generate_daily_report",
        action_label="生成当日检测报告",
        target_type="report",
        target_id=str(result.get("report_id")),
        success=True,
    )
    return result


# ----------------- 视频报表（每个 video 一份 report_type='video_fish_quality'） -----------------


@router.post("/fish-quality/video-pdf/generate")
def generate_video_pdf(body: dict, request: Request, db: Session = Depends(get_db)):
    """手动生成单个视频的视频报表。

    body: { "video_id": "..." }
    行为：
      - 若 detection_report 中已有 report_type='video_fish_quality' + video_id 的记录，
        覆盖（更新 file_path / file_size / summary_json / status / updated_at，
        保留 report_id / report_no）。
      - 否则新建。
      - video_task 不存在 → 404。
      - 该视频暂无 fish（total_fish=0）也允许生成，summary.total_fish=0。
    """
    video_id = str(body.get("video_id") or "").strip()
    if not video_id:
        raise HTTPException(
            status_code=400,
            detail={"ok": False, "error": "video_id is required", "video_id": ""},
        )

    # 查 existing
    from sqlalchemy import select as _select
    from app.repositories.models import DetectionReport as _DR

    existing = db.execute(
        _select(_DR)
        .where(_DR.report_type == "video_fish_quality")
        .where(_DR.video_id == video_id)
        .order_by(_DR.created_at.desc())
    ).scalars().first()
    existing_id = existing.report_id if existing else None

    # 拿 summary（按 video_id）
    summary = report_stats_service.get_fish_quality_summary(
        db, video_id=video_id
    )

    from app.services.report_pdf_service import (
        generate_video_fish_quality_pdf as _gen_video_report,
    )

    try:
        result = _gen_video_report(
            db,
            video_id=video_id,
            summary=summary,
            existing_report_id=existing_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        log_operation(
            db,
            request=request,
            module="数据报表",
            action="generate_video_report",
            action_label="生成视频报表",
            target_type="video",
            target_id=video_id,
            success=False,
            error_message=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail={
                "ok": False,
                "error": f"生成视频报表失败: {e!s}",
                "video_id": video_id,
            },
        )

    log_operation(
        db,
        request=request,
        module="数据报表",
        action="generate_video_report",
        action_label="生成视频报表",
        target_type="video",
        target_id=video_id,
        success=bool(result.get("ok")),
    )

    # 顺手附 download_url（与日报一致体验）
    if result.get("ok") and result.get("report_id"):
        result["download_url"] = (
            f"/api/reports/history/{result['report_id']}/download"
        )
    return result


# ----------------- 历史日报 -----------------

@router.get("/history")
def report_history(
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    report_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    return report_repo.list_reports(
        db,
        start_date=start_date,
        end_date=end_date,
        status=status,
        keyword=keyword,
        report_type=report_type,
        page=page,
        page_size=page_size,
    )


@router.get("/history/{report_id}")
def report_detail(report_id: int, db: Session = Depends(get_db)):
    obj = report_repo.get_report(db, report_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="report not found")
    return report_repo.report_to_dict(obj)


@router.get("/history/{report_id}/preview")
def report_preview(report_id: int, request: Request, db: Session = Depends(get_db)):
    obj = report_repo.get_report(db, report_id)
    if obj is None or not obj.file_path:
        raise HTTPException(status_code=404, detail="report file not found")

    abs_path = to_abs_data_path(obj.file_path)
    if abs_path is None or not abs_path.exists():
        raise HTTPException(status_code=404, detail="pdf file not found")

    log_operation(
        db,
        request=request,
        module="数据报表",
        action="preview_daily_report",
        action_label="预览日报 PDF",
        target_type="report",
        target_id=str(report_id),
        success=True,
    )
    return FileResponse(str(abs_path), media_type="application/pdf")


@router.get("/history/{report_id}/download")
def report_download(report_id: int, request: Request, db: Session = Depends(get_db)):
    obj = report_repo.get_report(db, report_id)
    if obj is None or not obj.file_path:
        raise HTTPException(status_code=404, detail="report file not found")

    abs_path = to_abs_data_path(obj.file_path)
    if abs_path is None or not abs_path.exists():
        raise HTTPException(status_code=404, detail="pdf file not found")

    log_operation(
        db,
        request=request,
        module="数据报表",
        action="download_daily_report",
        action_label="下载日报 PDF",
        target_type="report",
        target_id=str(report_id),
        success=True,
    )
    return FileResponse(
        str(abs_path),
        media_type="application/pdf",
        filename=obj.file_name or f"report_{report_id}.pdf",
    )


@router.post("/history/{report_id}/regenerate")
def report_regenerate(report_id: int, request: Request, db: Session = Depends(get_db)):
    obj = report_repo.get_report(db, report_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="report not found")

    summary = report_stats_service.get_fish_quality_summary(
        db, report_date=obj.report_date.isoformat()
    )
    if summary["total_fish"] <= 0:
        raise HTTPException(
            status_code=400,
            detail="当前选择日期无检测数据，无法生成报告",
        )

    from app.services.report_pdf_service import generate_daily_fish_quality_pdf
    try:
        result = generate_daily_fish_quality_pdf(
            db,
            report_date=obj.report_date,
            summary=summary,
            existing_report_id=obj.report_id,
        )
    except Exception as e:
        log_operation(
            db,
            request=request,
            module="数据报表",
            action="regenerate_daily_report",
            action_label="重新生成日报",
            target_type="report",
            target_id=str(report_id),
            success=False,
            error_message=str(e),
        )
        raise HTTPException(status_code=500, detail=f"重新生成失败: {e!s}")

    log_operation(
        db,
        request=request,
        module="数据报表",
        action="regenerate_daily_report",
        action_label="重新生成日报",
        target_type="report",
        target_id=str(report_id),
        success=True,
    )
    return result


@router.delete("/history/{report_id}")
def report_delete(report_id: int, request: Request, db: Session = Depends(get_db)):
    from pathlib import Path

    ok, abs_path_str = report_repo.delete_report_record_with_file(db, report_id)
    if not ok:
        raise HTTPException(status_code=404, detail="report not found")

    file_deleted = False
    if abs_path_str:
        try:
            file_deleted = Path(abs_path_str).unlink(missing_ok=False)
        except FileNotFoundError:
            file_deleted = False
        except OSError as e:
            log_operation(
                db,
                request=request,
                module="数据报表",
                action="delete_daily_report",
                action_label="删除日报",
                target_type="report",
                target_id=str(report_id),
                success=True,
                error_message=f"删除本地 PDF 失败: {e!s}",
            )

    log_operation(
        db,
        request=request,
        module="数据报表",
        action="delete_daily_report",
        action_label="删除日报",
        target_type="report",
        target_id=str(report_id),
        success=True,
    )
    return {
        "status": "deleted",
        "report_id": report_id,
        "file_deleted": bool(file_deleted),
    }


# ----------------- 平台使用分析 -----------------

@router.get("/platform-usage/summary")
def platform_usage_summary(
    range: str = "7d",
    db: Session = Depends(get_db),
):
    return platform_usage_service.get_platform_usage_summary(db, range)


@router.get("/platform-usage/charts")
def platform_usage_charts(
    range: str = "7d",
    db: Session = Depends(get_db),
):
    return platform_usage_service.get_platform_usage_charts(db, range)


@router.get("/platform-usage/failures")
def platform_usage_failures(
    range: str = "7d",
    db: Session = Depends(get_db),
):
    return platform_usage_service.get_platform_failures(db, range)


# ----------------- 操作日志 -----------------

@router.get("/operation-logs")
def operation_logs(
    start_time: str | None = None,
    end_time: str | None = None,
    ip: str | None = None,
    module: str | None = None,
    action: str | None = None,
    success: bool | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    return list_operation_logs(
        db,
        start_time=start_time,
        end_time=end_time,
        ip=ip,
        module=module,
        action=action,
        success=success,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
