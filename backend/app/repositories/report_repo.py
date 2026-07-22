from __future__ import annotations

from datetime import date

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.repositories.models import DetectionReport


def get_report(db: Session, report_id: int) -> DetectionReport | None:
    return db.get(DetectionReport, report_id)


def report_to_dict(r: DetectionReport) -> dict:
    return {
        "report_id": r.report_id,
        "report_date": r.report_date.isoformat() if r.report_date else None,
        "report_type": r.report_type,
        # video_id: 日报为 NULL，视频报表为具体 video_id。
        "video_id": r.video_id,
        "report_no": r.report_no,
        "report_name": r.report_name,
        "file_name": r.file_name,
        "file_path": r.file_path,
        "file_size": r.file_size,
        "total_video": r.total_video,
        "total_fish": r.total_fish,
        "abnormal_fish": r.abnormal_fish,
        "status": r.status,
        "error_message": r.error_message,
        "usb_backup_status": r.usb_backup_status,
        "usb_backup_path": r.usb_backup_path,
        "summary_json": r.summary_json,
        "daily_conclusion": r.daily_conclusion,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def list_reports(
    db: Session,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    report_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    stmt = select(DetectionReport)
    count_stmt = select(func.count()).select_from(DetectionReport)
    filters = []

    if start_date:
        try:
            filters.append(
                DetectionReport.report_date >= date.fromisoformat(start_date[:10])
            )
        except (ValueError, TypeError):
            pass
    if end_date:
        try:
            filters.append(
                DetectionReport.report_date <= date.fromisoformat(end_date[:10])
            )
        except (ValueError, TypeError):
            pass
    if status and status != "all":
        filters.append(DetectionReport.status == status)
    if keyword:
        like = f"%{keyword}%"
        filters.append(
            DetectionReport.report_name.like(like) | DetectionReport.report_no.like(like)
        )
    if report_type:
        # 区分日报 / 视频报表：
        # report_type='daily_fish_quality' → 日报
        # report_type='video_fish_quality' → 视频报表
        # 空 / None → 不过滤
        filters.append(DetectionReport.report_type == report_type)

    if filters:
        stmt = stmt.where(*filters)
        count_stmt = count_stmt.where(*filters)

    total = int(db.execute(count_stmt).scalar() or 0)
    rows = list(
        db.execute(
            stmt.order_by(DetectionReport.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars().all()
    )

    return {
        "items": [report_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def delete_report_record(db: Session, report_id: int) -> bool:
    obj = db.get(DetectionReport, report_id)
    if obj is None:
        return False
    db.delete(obj)
    db.commit()
    return True


def delete_report_record_with_file(db: Session, report_id: int) -> tuple[bool, str | None]:
    """删除 detection_report 记录。

    返回: (是否删除了记录, 本地 PDF 绝对路径或 None)

    注意：第一版不强制删除本地 PDF 文件，仅当文件确实存在时才删除。
    """
    from app.services.path_service import to_abs_data_path

    obj = db.get(DetectionReport, report_id)
    if obj is None:
        return False, None

    abs_path = to_abs_data_path(obj.file_path) if obj.file_path else None
    file_exists = abs_path is not None and abs_path.exists()

    db.delete(obj)
    db.commit()

    return True, str(abs_path) if file_exists else None
