#!/usr/bin/env python
# backend/app/routers/system_router.py
#
# /api/system 路由
# - 数据备份：status / preview / run
# - 软件更新：status / upload / run

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.services import system_backup_service, system_update_service


router = APIRouter(prefix="/api/system", tags=["system"])


class BackupRunRequest(BaseModel):
    target_dir: Optional[str] = None


class UpdateRunRequest(BaseModel):
    package_path: str


# ===== 数据备份 =====
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
    # 备份失败 → 500 让前端走 error 路径，但保留 result 内容
    if not result.get("ok"):
        # 用 HTTPException 也好；这里用 200 + ok=false 保留完整 stdout/stderr 让前端展示
        # 因为备份失败可能是磁盘满、U 盘不可写等，前端要看 stderr
        return result
    return result


# ===== 软件更新 =====
@router.get("/update/status")
def update_status():
    return system_update_service.get_update_status()


@router.post("/update/upload")
async def update_upload(file: UploadFile = File(...)):
    """接收 zip 包，保存到 data/uploads/update_packages/。"""
    try:
        content = await file.read()
    finally:
        await file.close()

    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")

    try:
        result = system_update_service.save_update_package(
            file_bytes=content,
            original_filename=file.filename or "update.zip",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存失败: {e}")
    return result


@router.post("/update/run")
def update_run(req: UpdateRunRequest):
    result = system_update_service.run_update(req.package_path)
    if not result.get("ok"):
        return result
    return result
