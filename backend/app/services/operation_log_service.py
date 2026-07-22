from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.repositories.operation_log_repo import create_operation_log


def normalize_operation_action(action: str) -> str:
    return action.strip().lower()


def log_operation(
    db: Session,
    *,
    request: Request | None,
    module: str,
    action: str,
    action_label: str,
    success: bool = True,
    target_type: str | None = None,
    target_id: str | None = None,
    status_code: int | None = 200,
    error_message: str | None = None,
    request_params: dict[str, Any] | None = None,
    duration_ms: int | None = None,
    operator: str | None = None,
) -> None:
    """写一条 operation_log 记录。任何异常只 print，不向上抛。"""
    try:
        ip_address = None
        user_agent = None
        request_method = None
        request_path = None
        actual_params = request_params

        if request is not None:
            ip_address = request.client.host if request.client else None
            user_agent = request.headers.get("user-agent")
            request_method = request.method
            request_path = str(request.url.path)
            if actual_params is None:
                actual_params = dict(request.query_params)

        create_operation_log(
            db,
            module=module,
            action=normalize_operation_action(action),
            action_label=action_label,
            success=success,
            target_type=target_type,
            target_id=target_id,
            ip_address=ip_address,
            user_agent=user_agent,
            request_method=request_method,
            request_path=request_path,
            request_params=actual_params,
            status_code=status_code,
            error_message=error_message,
            duration_ms=duration_ms,
            operator=operator,
        )
    except Exception as e:
        print(f"[operation_log] failed to log operation: {e!r}")
