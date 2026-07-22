from __future__ import annotations

from datetime import date, datetime
from pydantic import BaseModel


class DetectionReportOut(BaseModel):
    report_id: int
    report_date: date
    report_type: str
    report_no: str
    report_name: str
    file_name: str | None = None
    file_path: str | None = None
    file_size: int | None = None
    total_video: int
    total_fish: int
    abnormal_fish: int
    status: str
    error_message: str | None = None
    usb_backup_status: str
    usb_backup_path: str | None = None
    summary_json: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
