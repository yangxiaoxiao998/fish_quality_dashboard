from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.repositories.models import OperationLog


def create_operation_log(
    db: Session,
    *,
    module: str,
    action: str,
    action_label: str,
    success: bool = True,
    target_type: str | None = None,
    target_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    request_method: str | None = None,
    request_path: str | None = None,
    request_params: dict | None = None,
    status_code: int | None = None,
    error_message: str | None = None,
    duration_ms: int | None = None,
    operator: str | None = None,
) -> OperationLog:
    obj = OperationLog(
        module=module,
        action=action,
        action_label=action_label,
        success=success,
        target_type=target_type,
        target_id=target_id,
        ip_address=ip_address,
        user_agent=user_agent,
        request_method=request_method,
        request_path=request_path,
        request_params=request_params,
        status_code=status_code,
        error_message=error_message,
        duration_ms=duration_ms,
        operator=operator,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def list_operation_logs(
    db: Session,
    *,
    start_time: str | None = None,
    end_time: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    ip: str | None = None,
    module: str | None = None,
    action: str | None = None,
    success: bool | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    stmt = select(OperationLog)
    count_stmt = select(func.count()).select_from(OperationLog)
    filters = []

    if date_from and not start_time:
        start_time = date_from
    if date_to and not end_time:
        end_time = date_to

    if start_time:
        try:
            filters.append(
                OperationLog.created_at
                >= datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            )
        except (ValueError, TypeError):
            pass
    if end_time:
        try:
            filters.append(
                OperationLog.created_at
                <= datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            )
        except (ValueError, TypeError):
            pass
    if ip:
        filters.append(OperationLog.ip_address.like(f"%{ip}%"))
    if module:
        filters.append(OperationLog.module == module)
    if action:
        filters.append(OperationLog.action == action)
    if success is not None:
        filters.append(OperationLog.success == success)
    if keyword:
        like = f"%{keyword}%"
        filters.append(
            OperationLog.action_label.like(like)
            | OperationLog.request_path.like(like)
            | OperationLog.target_id.like(like)
        )

    if filters:
        stmt = stmt.where(*filters)
        count_stmt = count_stmt.where(*filters)

    total = int(db.execute(count_stmt).scalar() or 0)
    rows = list(
        db.execute(
            stmt.order_by(OperationLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars().all()
    )

    return {
        "items": [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "operator": r.operator or "系统用户",
                "username": r.operator or "系统用户",
                "ip": r.ip_address,
                "ip_address": r.ip_address,
                "user_agent": r.user_agent,
                "module": r.module,
                "action": r.action,
                "action_label": r.action_label,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "request_method": r.request_method,
                "method": r.request_method,
                "request_path": r.request_path,
                "path": r.request_path,
                "request_params": r.request_params,
                "status_code": r.status_code,
                "status": "success" if r.success else "failed",
                "success": r.success,
                "duration_ms": r.duration_ms,
                "message": r.error_message or r.action_label,
                "error_message": r.error_message,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
