"""鱼品质检测日报生成入口（docx 模板 + libreoffice → PDF）。

历史：
    - 旧版本：直接 reportlab 画 A4 文本 PDF
    - 新版本：data/reports/templates/detection_daily_report_template.docx 模板
              + python-docx 替换占位符 + matplotlib 5 张图
              + libreoffice headless 转 PDF

保留同名函数 `generate_daily_fish_quality_pdf` 以便 router 不改，但内部全走新链路，
最终仍返回 {report_id, report_name, file_name, file_path, file_size, ...}。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.paths import DATA_DIR
from app.repositories.models import DetectionReport, VideoTask
from app.services.path_service import to_data_relative
from app.services.report_docx_service import (
    generate_daily_fish_quality_docx_and_pdf,
)
from app.services.report_stats_service import (
    build_daily_report_context,
)

logger = logging.getLogger("report_pdf")


def build_daily_report_dir(report_date: date) -> Path:
    folder = (
        DATA_DIR
        / "reports"
        / "daily"
        / f"{report_date.year:04d}"
        / f"{report_date.month:02d}"
        / f"{report_date.day:02d}"
    )
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def build_daily_report_pdf_path(report_date: date, file_name: str) -> Path:
    return build_daily_report_dir(report_date) / file_name


def generate_report_no(report_date: date, existing_count: int = 0) -> str:
    return f"R{report_date.strftime('%Y%m%d')}{existing_count + 1:04d}"


def generate_daily_fish_quality_pdf(
    db: Session,
    *,
    report_date: date,
    summary: dict,
    existing_report_id: int | None = None,
    save_history: bool = True,
) -> dict:
    """生成每日鱼品质检测报告（docx 模板 → PDF）。

    - 默认不写入 detection_report，日报只作为当前日期的文件生成。
    - save_history=True 时才写入/更新一条日报记录。
    - 如果传入 existing_report_id，则视为"重新生成"：覆盖同一记录的
      file_path / file_size / summary_json / status / daily_conclusion 等字段，
      保留 report_no 与主键不变，避免 unique 冲突。
    """
    date_str = report_date.strftime("%Y%m%d")
    file_stem = f"鱼质检测日报_{date_str}"
    file_name = f"{file_stem}.pdf"

    out_dir = build_daily_report_dir(report_date)

    # 1) 先构建 context（未含 file_name/file_path 时填占位）
    context = build_daily_report_context(
        db,
        report_date=report_date,
        file_name=file_name,
        file_path=None,  # 生成后回填
    )
    # 再用 summary 覆盖总览字段
    #   - 百分比类字段：summary 里存的是 0~1 原始小数，需要重新 format
    #   - 整数 / 字符串 / 浮点指标：直接覆盖
    _PERCENT_KEYS = {
        "surface_damage_rate",
        "metric_complete_rate",
        "abnormal_fish_rate",
        "task_success_rate",
    }
    for k, v in (summary or {}).items():
        if k not in context or v is None:
            continue
        if k in _PERCENT_KEYS:
            # 重新走 helper（context 里已是字符串）
            from app.services.report_format_utils import format_percent
            context[k] = format_percent(v)
        else:
            context[k] = v
    context["report_file_name"] = file_name
    context["report_file_path"] = str(out_dir / file_name)
    context["report_name"] = context.get("report_name") or f"鱼质检测日报_{date_str}"
    context["system_name"] = context["report_name"]

    # 2) docx + PDF
    result = generate_daily_fish_quality_docx_and_pdf(
        db,
        report_date=report_date,
        output_dir=out_dir,
        file_stem=file_stem,
        existing_context=context,
    )
    docx_path: Path = result["docx_path"]
    pdf_path: Path | None = result["pdf_path"]
    final_pdf_path = pdf_path if pdf_path else docx_path  # 没装 libreoffice → 兜底 docx
    rel_path = to_data_relative(final_pdf_path)
    file_name_final = final_pdf_path.name
    size = final_pdf_path.stat().st_size if final_pdf_path.exists() else 0

    # 3) 可选写 detection_report。日报当前页面不依赖历史表，因此默认不保存。
    if not save_history:
        return {
            "status": "generated",
            "report_id": None,
            "report_date": report_date.isoformat(),
            "report_name": f"鱼质检测日报_{date_str}",
            "file_name": file_name_final,
            "file_path": rel_path,
            "file_size": size,
            "docx_path": str(docx_path) if docx_path else None,
            "pdf_path": str(pdf_path) if pdf_path else None,
            "leftover_placeholders": result.get("leftover_placeholders") or [],
        }

    # 4) 写 detection_report（仅兼容需要保留历史的调用方）
    if existing_report_id is not None:
        obj = db.get(DetectionReport, existing_report_id)
        if obj is None:
            existing_report_id = None

    if existing_report_id is not None:
        obj = db.get(DetectionReport, existing_report_id)
        obj.file_name = file_name_final
        obj.file_path = rel_path
        obj.file_size = size
        obj.total_video = context.get("video_total", 0)
        obj.total_fish = context.get("fish_total", 0)
        obj.abnormal_fish = context.get("abnormal_fish_total", 0)
        obj.status = "generated"
        obj.error_message = None
        obj.summary_json = _ctx_to_summary(context)
        obj.daily_conclusion = context.get("daily_conclusion")
        db.commit()
        db.refresh(obj)
    else:
        existing = int(
            db.execute(
                select(func.count())
                .select_from(DetectionReport)
                .where(DetectionReport.report_date == report_date)
            ).scalar()
            or 0
        )
        obj = DetectionReport(
            report_date=report_date,
            report_type="daily_fish_quality",
            report_no=generate_report_no(report_date, existing),
            report_name=f"鱼质检测日报_{date_str}",
            file_name=file_name_final,
            file_path=rel_path,
            file_size=size,
            total_video=context.get("video_total", 0),
            total_fish=context.get("fish_total", 0),
            abnormal_fish=context.get("abnormal_fish_total", 0),
            status="generated",
            usb_backup_status="not_detected",
            summary_json=_ctx_to_summary(context),
            daily_conclusion=context.get("daily_conclusion"),
        )
        db.add(obj)
        db.commit()
        db.refresh(obj)

    return {
        "status": "generated",
        "report_id": obj.report_id,
        "report_date": obj.report_date.isoformat(),
        "report_name": obj.report_name,
        "file_name": obj.file_name,
        "file_path": obj.file_path,
        "file_size": obj.file_size,
        "docx_path": str(docx_path) if docx_path else None,
        "pdf_path": str(pdf_path) if pdf_path else None,
        "leftover_placeholders": result.get("leftover_placeholders") or [],
    }


def generate_video_report_no(video_id: str) -> str:
    """视频报表编号：按完整 video_id 生成，确保不同视频编号不冲突。"""
    normalized = (video_id or "video").strip()
    return f"VR{normalized}"


def _safe_video_report_date(video: VideoTask) -> date:
    """从 video_task 推导 report_date：
    优先 started_at → created_at → today。
    """
    for src in (getattr(video, "started_at", None), getattr(video, "created_at", None)):
        if src is None:
            continue
        if isinstance(src, datetime):
            return src.date()
        if isinstance(src, date):
            return src
    from app.utils.time_utils import now_beijing

    return now_beijing().date()


def generate_video_fish_quality_pdf(
    db: Session,
    video_id: str,
    summary: dict | None = None,
    existing_report_id: int | None = None,
) -> dict:
    """为单一 video_id 生成"视频报表"（report_type='video_fish_quality'）。

    行为：
      1. 必须存在 video_task；不存在则抛 HTTPException 404。
      2. 复用 video_report_pdf_service.generate_video_pdf_report 生成 PDF / docx。
      3. 写入 / 更新 detection_report 记录：
         - report_type='video_fish_quality'
         - video_id=<video_id>
         - report_date=<video.started_at/created_at 日期>
         - report_no 复用 existing；新建时用 VR<short>
         - 同 video_id 重复生成时由 existing_report_id 覆盖原记录。
      4. 写库失败时 video_report_pdf_service 的 PDF / docx 仍可能已生成；
         不会清理已生成文件（管理员可手动删），只返回失败。

    报表生成失败**不影响**算法任务 / 主流程。
    """
    from fastapi import HTTPException
    from app.services.video_report_pdf_service import generate_video_pdf_report

    video_id = (video_id or "").strip()
    if not video_id:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error": "video_id is empty",
                "video_id": video_id,
            },
        )

    video = db.get(VideoTask, video_id)
    if video is None:
        raise HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "error": "video_task not found",
                "video_id": video_id,
            },
        )

    # 1. 复用现有视频报告 PDF 生成（docx + pdf + 5 张图）
    try:
        pdf_res = generate_video_pdf_report(video_id)
    except Exception as exc:
        logger.exception(
            "[video_report_pdf] generate_video_pdf_report failed: %s", exc
        )
        raise HTTPException(
            status_code=500,
            detail={
                "ok": False,
                "error": f"generate_video_pdf_report failed: {exc}",
                "video_id": video_id,
            },
        )

    if not pdf_res.get("ok"):
        # 内部已记录原因；直接返回原 result
        return {
            "ok": False,
            "video_id": video_id,
            "error": pdf_res.get("error") or "video report generation failed",
            "report_result": pdf_res,
        }

    # 2. 准备 detection_report 字段
    report_date = _safe_video_report_date(video)
    pdf_rel_path: str = pdf_res.get("report_pdf") or pdf_res.get("report_docx") or ""
    docx_rel_path: str = pdf_res.get("report_docx") or ""
    # 优先 PDF；docx 在 PDF 不可用时作为兜底
    file_path_to_record = pdf_rel_path or docx_rel_path
    file_name_to_record = Path(file_path_to_record).name if file_path_to_record else ""

    # 文件大小
    file_size = 0
    if file_path_to_record:
        try:
            from app.services.path_service import to_abs_data_path
            abs_p = to_abs_data_path(file_path_to_record)
            if abs_p and abs_p.exists():
                file_size = abs_p.stat().st_size
        except Exception:
            pass

    # summary → 顶层字段
    total_video = 1
    total_fish = 0
    abnormal_fish = 0
    valid_fish = 0
    incomplete_fish = 0
    failed_fish = 0
    surface_damage_fish_total = 0
    if summary:
        try:
            total_fish = int(summary.get("total_fish") or 0)
            abnormal_fish = int(summary.get("abnormal_fish") or 0)
            valid_fish = int(summary.get("valid_fish") or 0)
            incomplete_fish = int(summary.get("incomplete_fish") or 0)
            failed_fish = int(summary.get("failed_fish") or 0)
            surface_damage_fish_total = int(
                summary.get("surface_damage_fish_total") or 0
            )
        except Exception:
            pass

    # 3. 覆盖 / 新建 detection_report
    if existing_report_id is None:
        existing_row = db.execute(
            select(DetectionReport)
            .where(DetectionReport.report_type == "video_fish_quality")
            .where(DetectionReport.video_id == video_id)
            .order_by(DetectionReport.created_at.desc())
        ).scalars().first()
        if existing_row is not None:
            existing_report_id = existing_row.report_id

    if existing_report_id is not None:
        obj = db.get(DetectionReport, existing_report_id)
        if obj is None:
            existing_report_id = None

    if existing_report_id is not None:
        obj = db.get(DetectionReport, existing_report_id)
        obj.report_type = "video_fish_quality"
        obj.video_id = video_id
        obj.report_date = report_date
        obj.file_name = file_name_to_record
        obj.file_path = file_path_to_record
        obj.file_size = file_size
        obj.total_video = total_video
        obj.total_fish = total_fish
        obj.abnormal_fish = abnormal_fish
        obj.status = "generated"
        obj.error_message = None
        obj.summary_json = {
            "video_id": video_id,
            "total_fish": total_fish,
            "valid_fish": valid_fish,
            "incomplete_fish": incomplete_fish,
            "failed_fish": failed_fish,
            "abnormal_fish": abnormal_fish,
            "surface_damage_fish_total": surface_damage_fish_total,
            "report_date": report_date.isoformat(),
        }
        db.commit()
        db.refresh(obj)
    else:
        report_no = generate_video_report_no(video_id)
        obj = DetectionReport(
            report_date=report_date,
            report_type="video_fish_quality",
            video_id=video_id,
            report_no=report_no,
            report_name=f"鱼质检测视频报表_{video_id}",
            file_name=file_name_to_record,
            file_path=file_path_to_record,
            file_size=file_size,
            total_video=total_video,
            total_fish=total_fish,
            abnormal_fish=abnormal_fish,
            status="generated",
            usb_backup_status="not_detected",
            summary_json={
                "video_id": video_id,
                "total_fish": total_fish,
                "valid_fish": valid_fish,
                "incomplete_fish": incomplete_fish,
                "failed_fish": failed_fish,
                "abnormal_fish": abnormal_fish,
                "surface_damage_fish_total": surface_damage_fish_total,
                "report_date": report_date.isoformat(),
            },
        )
        db.add(obj)
        db.commit()
        db.refresh(obj)

    return {
        "ok": True,
        "status": "generated",
        "video_id": video_id,
        "report_id": obj.report_id,
        "report_type": "video_fish_quality",
        "report_date": report_date.isoformat(),
        "report_no": obj.report_no,
        "file_name": file_name_to_record,
        "file_path": file_path_to_record,
        "file_size": file_size,
        "summary": obj.summary_json,
        "report_result": pdf_res,
    }


def _ctx_to_summary(ctx: dict) -> dict:
    """把 context 关键字段挑出来当 summary_json 存。"""
    keys = [
        "video_total", "upload_video_total", "rtsp_video_total",
        "done_video_total", "failed_video_total", "no_fish_video_total",
        "fish_total", "valid_fish_total", "partial_fish_total",
        "abnormal_fish_total", "surface_damage_fish_total",
        "surface_damage_rate", "metric_complete_rate", "abnormal_fish_rate",
        "avg_body_length", "min_body_length", "max_body_length",
        "avg_body_length_without_tail", "min_body_length_without_tail",
        "max_body_length_without_tail",
        "avg_body_height", "min_body_height", "max_body_height",
        "avg_body_weight", "min_body_weight", "max_body_weight",
        "avg_condition_factor", "min_condition_factor", "max_condition_factor",
        "total_video_duration", "avg_processing_seconds", "avg_fps",
        "task_success_rate",
    ]
    return {k: ctx.get(k) for k in keys if k in ctx}


def to_report_relative_path(path: Path) -> str:
    return path.relative_to(DATA_DIR).as_posix()
