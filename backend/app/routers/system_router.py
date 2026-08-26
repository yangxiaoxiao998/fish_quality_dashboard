#!/usr/bin/env python
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.services import system_backup_service
from app.services.backup import rsync_backup_service
from app.services.update import system_update_service


router = APIRouter(prefix="/api/system", tags=["system"])


class BackupRunRequest(BaseModel):
    target_dir: Optional[str] = None


class UpdateRunRequest(BaseModel):
    package_path: str


class RsyncBackupSettingsRequest(BaseModel):
    source_dir: str
    target_dir: str
    interval_seconds: int = 3600
    enabled: bool = False


@router.get("/backup/status")
def backup_status():
    return system_backup_service.get_backup_status()


@router.post("/backup/preview")
def backup_preview(req: Optional[BackupRunRequest] = None):
    target = req.target_dir if req else None
    return system_backup_service.preview_backup(target)


@router.post("/backup/run")
def backup_run(req: Optional[BackupRunRequest] = None):
    target = req.target_dir if req else None
    result = system_backup_service.run_backup(target)
    if not result.get("ok"):
        return result
    return result


@router.get("/backup/rsync/status")
def rsync_backup_status():
    return rsync_backup_service.get_status()


@router.put("/backup/rsync/settings")
def rsync_backup_settings(req: RsyncBackupSettingsRequest):
    try:
        return rsync_backup_service.save_settings(req.source_dir, req.target_dir, req.interval_seconds, req.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/backup/rsync/run")
def rsync_backup_run():
    try:
        return rsync_backup_service.start_async()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/backup/rsync/eject")
def rsync_backup_eject():
    return rsync_backup_service.eject_target()


@router.get("/update/summary")
def update_summary():
    return system_update_service.get_update_summary()


@router.get("/update/manager-info")
def update_manager_info():
    return system_update_service.get_update_manager_info()


@router.get("/update/status")
def update_status():
    return system_update_service.get_update_status()


@router.post("/update/upload")
async def update_upload(file: UploadFile = File(...)):
    try:
        content = await file.read()
    finally:
        await file.close()

    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")

    try:
        return system_update_service.save_update_package(
            file_bytes=content,
            original_filename=file.filename or "update_package.tar.xz",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"保存失败: {exc}")


@router.post("/update/run")
def update_run(req: UpdateRunRequest):
    result = system_update_service.run_update(req.package_path)
    if not result.get("ok"):
        return result
    return result
