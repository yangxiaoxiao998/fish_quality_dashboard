from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


TaskStatus = Literal[
    "uploaded",
    "prechecking",
    "precheck_failed",
    "queued",
    "running",
    "backing_up",
    "applying",
    "verifying",
    "success",
    "rollbacking",
    "rolled_back",
    "failed",
]

TaskType = Literal["update", "rollback"]

FINAL_TASK_STATUSES = {"success", "rolled_back", "failed"}


class PackagePrecheckRequest(BaseModel):
    package_path: str


class ImportPackageRequest(BaseModel):
    source_path: str


class CreateUpdateTaskRequest(BaseModel):
    package_path: str


class RollbackTaskRequest(BaseModel):
    backup_dir: str


class UpgradeTaskState(BaseModel):
    task_id: str
    task_type: TaskType
    package_path: Optional[str] = None
    backup_dir: Optional[str] = None
    current_version: Optional[str] = None
    target_version: Optional[str] = None
    status: TaskStatus
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    updated_targets: list[str] = Field(default_factory=list)
    error_message: Optional[str] = None
    message: Optional[str] = None
    runner_pid: Optional[int] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
