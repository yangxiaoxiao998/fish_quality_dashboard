from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.repositories.db import get_db
from app.repositories.operation_log_repo import list_operation_logs
from app.services.operation_log_service import log_operation

router = APIRouter(prefix="/operation-logs")


class OperationLogCreate(BaseModel):
    module: str = "系统操作"
    action: str = "user_action"
    target_type: str | None = None
    target_id: str | None = None
    message: str | None = None
    status: str | None = "success"


@router.get("")
def get_operation_logs(
    page: int = 1,
    page_size: int = 20,
    module: str | None = None,
    action: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    keyword: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    success = None
    if status == "success":
        success = True
    elif status == "failed":
        success = False
    return list_operation_logs(
        db,
        start_time=start_time,
        end_time=end_time,
        date_from=date_from,
        date_to=date_to,
        module=module,
        action=action,
        success=success,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


@router.post("")
def create_user_operation_log(
    payload: OperationLogCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    log_operation(
        db,
        request=request,
        module=payload.module,
        action=payload.action,
        action_label=payload.message or payload.action,
        success=payload.status != "failed",
        target_type=payload.target_type,
        target_id=payload.target_id,
        status_code=200,
    )
    return {"ok": True}
