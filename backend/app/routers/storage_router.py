from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.repositories.db import get_db
from app.services import config_revision_service
from app.services.object_storage_service import object_storage
from app.repositories.models import StoredObject
from sqlalchemy import select

router = APIRouter(prefix="/storage")


@router.get("/health")
def storage_health() -> dict:
    if object_storage.backend == "local":
        return {"ok": True, "backend": "local", "minio_enabled": False}
    try:
        object_storage._minio().list_buckets()
        return {"ok": True, "backend": "minio", "minio_enabled": True}
    except Exception as exc:
        return {"ok": False, "backend": "minio", "minio_enabled": True, "error": str(exc)}


@router.get("/config-revisions")
def get_config_revisions(config_type: str, config_key: str = "default", db: Session = Depends(get_db)) -> dict:
    return {"items": config_revision_service.list_revisions(db, config_type, config_key)}


@router.get("/objects")
def list_stored_objects(owner_type: str | None = None, owner_id: str | None = None,
                        status: str | None = None, db: Session = Depends(get_db)) -> dict:
    query = select(StoredObject).order_by(StoredObject.created_at.desc())
    if owner_type:
        query = query.where(StoredObject.owner_type == owner_type)
    if owner_id:
        query = query.where(StoredObject.owner_id == owner_id)
    if status:
        query = query.where(StoredObject.status == status)
    rows = db.execute(query.limit(500)).scalars().all()
    return {"ok": True, "items": [{
        "object_id": row.object_id, "owner_type": row.owner_type,
        "owner_id": row.owner_id, "backend": row.backend, "bucket": row.bucket,
        "object_key": row.object_key, "content_type": row.content_type,
        "size_bytes": row.size_bytes, "sha256": row.sha256, "status": row.status,
        "source_local_path": row.source_local_path,
        "last_error": row.last_error,
    } for row in rows]}


@router.get("/config-revisions/active")
def get_active_config_revision(config_type: str, config_key: str = "default", db: Session = Depends(get_db)) -> dict:
    row = config_revision_service.get_active_revision(db, config_type, config_key)
    return {"ok": True, "item": row}


@router.post("/config-revisions")
def create_config_revision(body: dict, db: Session = Depends(get_db)) -> dict:
    config_type = str(body.get("config_type") or "").strip()
    config_key = str(body.get("config_key") or "default").strip()
    content = body.get("content_json")
    if not config_type or not isinstance(content, dict):
        raise HTTPException(status_code=422, detail="config_type and object content_json are required")
    return config_revision_service.create_revision(db, config_type, config_key, content,
                                                   str(body.get("created_by") or "system"))


@router.post("/config-revisions/{revision_id}/activate")
def activate_config_revision(revision_id: int, db: Session = Depends(get_db)) -> dict:
    row = config_revision_service.activate_revision(db, revision_id)
    if row is None:
        raise HTTPException(status_code=404, detail="config revision not found")
    return row
