from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.services.update.upgrade_manager import service
from app.services.update.upgrade_manager.models import (
    CreateUpdateTaskRequest,
    ImportPackageRequest,
    PackagePrecheckRequest,
    RollbackTaskRequest,
)


router = APIRouter(prefix="/api/upgrade", tags=["upgrade-manager"])


@router.get("/health")
def health():
    return service.get_manager_health()


@router.get("/status")
def status():
    return service.get_manager_status()


@router.get("/packages")
def packages():
    return {"ok": True, "items": service.list_packages()}


@router.get("/packages/browse")
def packages_browse(path: str | None = None):
    try:
        return service.browse_importable_package_files(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to browse local packages: {exc}")


@router.post("/packages/upload")
async def packages_upload(file: UploadFile = File(...)):
    try:
        return service.upload_update_package_stream(file.file, file.filename or "update_package.tar.xz")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to save uploaded package: {exc}")
    finally:
        await file.close()


@router.post("/packages/import")
def packages_import(req: ImportPackageRequest):
    try:
        return service.import_update_package_from_path(req.source_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to import update package: {exc}")


@router.post("/packages/precheck")
def packages_precheck(req: PackagePrecheckRequest):
    try:
        return service.precheck_package(req.package_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/tasks")
def tasks_create(req: CreateUpdateTaskRequest):
    try:
        task = service.create_update_task(req.package_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True, "task": task}


@router.get("/tasks/{task_id}")
def task_status(task_id: str):
    try:
        task = service.get_task_status(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True, "task": task}


@router.get("/tasks/{task_id}/log")
def task_log(task_id: str):
    try:
        return service.get_task_log(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/backups")
def backups():
    return {"ok": True, "items": service.list_backups()}


@router.post("/rollback")
def rollback(req: RollbackTaskRequest):
    try:
        task = service.create_rollback_task(req.backup_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True, "task": task}
