from __future__ import annotations

from datetime import date
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.repositories import report_repo
from app.repositories.db import get_db
from app.repositories.operation_log_repo import list_operation_logs
from app.services import platform_usage_service, report_stats_service
from app.services.operation_log_service import log_operation
from app.services.path_service import to_abs_data_path

router = APIRouter(prefix="/reports")


def _safe_report_download_name(name: str | None, fallback: str, suffix: str = ".pdf") -> str:
    import re

    value = str(name or fallback).strip()
    value = re.sub(r'[\\/:*?"<>|\r\n]+', "_", value).strip(" .") or fallback
    if not value.lower().endswith(suffix):
        value += suffix
    return value


@router.post("/history/batch-download")
def batch_download_reports(body: dict, db: Session = Depends(get_db)):
    """按报表名称下载一个或多个已生成报表。多个报表打包为 ZIP。"""
    import io
    import zipfile
    from urllib.parse import quote

    raw_ids = body.get("report_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="report_ids must be a non-empty list")
    report_ids = list(dict.fromkeys(int(value) for value in raw_ids if str(value).isdigit()))
    if not report_ids:
        raise HTTPException(status_code=400, detail="report_ids must contain valid report IDs")

    reports = [report_repo.get_report(db, report_id) for report_id in report_ids]
    reports = [report for report in reports if report is not None and report.file_path]
    if len(reports) != len(report_ids):
        raise HTTPException(status_code=404, detail="one or more reports not found")

    files: list[tuple[str, object]] = []
    used_names: set[str] = set()
    for report in reports:
        path = to_abs_data_path(report.file_path)
        if path is None or not path.exists():
            raise HTTPException(status_code=404, detail=f"report file not found: {report.report_id}")
        name = _safe_report_download_name(report.report_name, f"report_{report.report_id}")
        stem, suffix = name.rsplit(".", 1)
        candidate = name
        index = 2
        while candidate in used_names:
            candidate = f"{stem}_{index}.{suffix}"
            index += 1
        used_names.add(candidate)
        files.append((candidate, path))

    if len(files) == 1:
        name, path = files[0]
        return FileResponse(
            str(path),
            media_type="application/pdf",
            filename=name,
        )

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for name, path in files:
            output.write(path, arcname=name)
    archive.seek(0)
    download_name = "fish_quality_reports.zip"
    return Response(
        content=archive.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=reports.zip; filename*=UTF-8''{quote(download_name)}",
        },
    )


@router.post("/custom-pdf/generate")
def generate_custom_pdf(body: dict, db: Session = Depends(get_db)) -> dict:
    """生成所选视频的只读合并报表 PDF。"""
    from datetime import datetime
    import uuid
    from app.paths import DATA_DIR
    from app.repositories.models import DetectionReport, VideoTask

    raw_ids = body.get("video_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="video_ids must be a non-empty list")
    video_ids = list(dict.fromkeys(str(v).strip() for v in raw_ids if str(v).strip()))
    report_name = str(body.get("report_name") or "").strip()
    if not report_name:
        raise HTTPException(status_code=400, detail="report_name is required")
    from app.services.video_report_pdf_service import build_combined_report_data
    try:
        report_data = build_combined_report_data(db, video_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    rows = [item["task"] for item in report_data["video_rows"]]

    from app.services.video_report_pdf_service import generate_video_pdf_report
    from app.services.path_service import to_abs_data_path
    if len(rows) == 1:
        result = generate_video_pdf_report(rows[0].video_id, report_name=report_name, db=db)
        if not result.get("ok"):
            raise HTTPException(status_code=500, detail=result.get("error") or "report generation failed")
        report = db.query(DetectionReport).filter(
            DetectionReport.report_type == "video_fish_quality",
            DetectionReport.video_id == rows[0].video_id,
        ).order_by(DetectionReport.created_at.desc()).first()
        if report is not None:
            report.report_name = report_name
            db.commit()
        result["report_name"] = report_name
        return result

    report_dir = DATA_DIR / "reports" / "custom"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        from app.services.video_report_pdf_service import generate_combined_video_pdf_report
        result = generate_combined_video_pdf_report(video_ids, report_dir / stamp, report_name, report_data=report_data)
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "合并报表生成失败")
        pdf_path = to_abs_data_path(result.get("file_path"))
        if pdf_path is None or not pdf_path.exists():
            raise RuntimeError("合并报表 PDF 未生成")
        file_name = pdf_path.name
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"生成合并 PDF 失败：{exc}") from exc

    report_date = min((row.created_at.date() for row in rows if row.created_at), default=datetime.now().date())
    from app.repositories.models import DetectionReport
    snapshot = {
        "video_ids": report_data["video_ids"],
        "summary": report_data["summary"],
        "video_rows": [
            {"video_id": row["video_id"], "duration_seconds": row["duration_seconds"],
             "processing_seconds": row["processing_seconds"], "fish_count": row["fish_count"]}
            for row in report_data["video_rows"]
        ],
        "fish_rows": report_data["fish_rows"],
    }
    snapshot_text = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    report = DetectionReport(
        report_date=report_date,
        report_type="custom_video_fish_quality",
        report_no=f"CUSTOM-{stamp}-{uuid.uuid4().hex[:8].upper()}",
        report_name=report_name,
        file_name=file_name,
        file_path=f"data/{pdf_path.relative_to(DATA_DIR).as_posix()}",
        file_size=pdf_path.stat().st_size,
        total_video=len(rows),
        total_fish=int(report_data["summary"].get("total_fish") or 0),
        status="generated",
        summary_json={**snapshot, "snapshot_sha256": hashlib.sha256(snapshot_text.encode("utf-8")).hexdigest(), "report_data": report_data["summary"],
                      "video_rows": [{"video_id": row["video_id"], "duration_seconds": row["duration_seconds"],
                                      "fish_count": row["fish_count"]} for row in report_data["video_rows"]]},
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return {
        "ok": True,
        "report_id": report.report_id,
        "file_name": file_name,
        "preview_url": f"/api/reports/history/{report.report_id}/preview",
        "download_url": f"/api/reports/history/{report.report_id}/download",
    }


@router.get("/video-overview/export")
def export_video_overview(
    start_date: str | None = None,
    end_date: str | None = None,
    db: Session = Depends(get_db),
):
    """导出视频报表列表为 Excel 可直接打开的 UTF-8 CSV。"""
    import csv
    import io
    from fastapi.responses import StreamingResponse
    from app.repositories.models import VideoTask
    from sqlalchemy import select

    stmt = select(VideoTask).order_by(VideoTask.created_at.desc())
    if start_date:
        stmt = stmt.where(VideoTask.created_at >= date.fromisoformat(start_date[:10]))
    if end_date:
        stmt = stmt.where(VideoTask.created_at < date.fromordinal(date.fromisoformat(end_date[:10]).toordinal() + 1))
    rows = db.execute(stmt).scalars().all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["序号", "视频编号", "养鱼仓", "状态", "总鱼数", "完整鱼数", "不完整鱼数", "创建时间"])
    for index, row in enumerate(rows, 1):
        writer.writerow([
            index, row.video_id, row.aquaculture_bin or "", row.status,
            row.total_fish or 0, row.complete_fish or 0, row.incomplete_fish or 0,
            row.created_at.isoformat() if row.created_at else "",
        ])
    content = "\ufeff" + output.getvalue()
    return StreamingResponse(
        iter([content.encode("utf-8")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=video_reports.csv"},
    )


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

    # 当天日报采用单条记录覆盖，保证任务完成后前端能读到最新版本。
    existing_id: int | None = None
    existing_rows = []
    try:
        from sqlalchemy import select as _select
        from app.repositories.models import DetectionReport as _DR
        existing_rows = list(db.execute(
            _select(_DR)
            .where(_DR.report_date == report_date)
            .where(_DR.report_type == "daily_fish_quality")
            .order_by(_DR.created_at.desc())
        ).scalars().all())
        existing_id = existing_rows[0].report_id if existing_rows else None
    except Exception:
        # 查不到 existing 不影响生成；让 service 走新建分支
        existing_id = None

    try:
        result = generate_daily_fish_quality_pdf(
            db,
            report_date=report_date,
            summary=summary,
            existing_report_id=existing_id,
            save_history=True,
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


@router.patch("/history/{report_id}/name")
def report_rename(report_id: int, body: dict, db: Session = Depends(get_db)):
    name = str(body.get("report_name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="report_name is required")
    obj = report_repo.get_report(db, report_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="report not found")
    obj.report_name = name
    db.commit()
    db.refresh(obj)
    if obj.report_type == "custom_video_fish_quality":
        video_ids = (obj.summary_json or {}).get("video_ids", [])
        if isinstance(video_ids, list) and video_ids:
            from app.paths import DATA_DIR
            from app.services.video_report_pdf_service import generate_combined_video_pdf_report
            refreshed = generate_combined_video_pdf_report(
                [str(value) for value in video_ids],
                DATA_DIR / "reports" / "custom" / f"rename_{obj.report_id}",
                name,
            )
            if refreshed.get("ok") and refreshed.get("file_path"):
                obj.file_path = refreshed["file_path"]
                obj.file_name = refreshed.get("file_name")
                abs_path = to_abs_data_path(obj.file_path)
                obj.file_size = abs_path.stat().st_size if abs_path and abs_path.exists() else obj.file_size
                db.commit()
                db.refresh(obj)
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
