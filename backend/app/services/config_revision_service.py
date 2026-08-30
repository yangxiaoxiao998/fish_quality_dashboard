from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.repositories.models import SystemConfigRevision
from app.services.object_storage_service import object_storage
from app.settings import settings


def _json_hash(content: dict[str, Any]) -> str:
    raw = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def revision_to_dict(row: SystemConfigRevision) -> dict[str, Any]:
    return {"revision_id": row.revision_id, "config_type": row.config_type,
            "config_key": row.config_key, "version": row.version,
            "content_json": row.content_json, "sha256": row.sha256,
            "is_active": row.is_active, "created_by": row.created_by,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "activated_at": row.activated_at.isoformat() if row.activated_at else None}


def list_revisions(db: Session, config_type: str, config_key: str) -> list[dict[str, Any]]:
    rows = db.execute(select(SystemConfigRevision).where(
        SystemConfigRevision.config_type == config_type,
        SystemConfigRevision.config_key == config_key,
    ).order_by(SystemConfigRevision.version.desc())).scalars().all()
    return [revision_to_dict(row) for row in rows]


def create_revision(db: Session, config_type: str, config_key: str,
                    content: dict[str, Any], created_by: str = "system") -> dict[str, Any]:
    latest = db.execute(select(SystemConfigRevision).where(
        SystemConfigRevision.config_type == config_type,
        SystemConfigRevision.config_key == config_key,
    ).order_by(SystemConfigRevision.version.desc())).scalars().first()
    row = SystemConfigRevision(config_type=config_type, config_key=config_key,
                               version=(latest.version + 1) if latest else 1,
                               content_json=content, sha256=_json_hash(content), created_by=created_by)
    db.add(row)
    db.flush()
    # Every revision gets an immutable JSON snapshot.  The database copy stays
    # queryable, but activation never depends on a mutable local JSON file.
    snapshot_key = f"config/{config_type}/{config_key}/{row.version}.json"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8") as snapshot:
        json.dump(content, snapshot, ensure_ascii=False, sort_keys=True, indent=2)
        snapshot.flush()
        result = object_storage.put_file(
            Path(snapshot.name), settings.minio_config_bucket, snapshot_key,
        )
    row.object_bucket = result.get("bucket") or settings.minio_config_bucket
    row.object_key = result["object_key"]
    row.sha256 = result["sha256"]
    db.commit()
    db.refresh(row)
    return revision_to_dict(row)


def activate_revision(db: Session, revision_id: int) -> dict[str, Any] | None:
    row = db.get(SystemConfigRevision, revision_id)
    if row is None:
        return None
    now = datetime.now(timezone.utc)
    db.execute(update(SystemConfigRevision).where(
        SystemConfigRevision.config_type == row.config_type,
        SystemConfigRevision.config_key == row.config_key,
    ).values(is_active=False, superseded_at=now))
    row.is_active = True
    row.activated_at = now
    db.commit()
    db.refresh(row)
    return revision_to_dict(row)


def get_active_revision(db: Session, config_type: str, config_key: str = "default") -> dict[str, Any] | None:
    row = db.execute(select(SystemConfigRevision).where(
        SystemConfigRevision.config_type == config_type,
        SystemConfigRevision.config_key == config_key,
        SystemConfigRevision.is_active.is_(True),
    ).order_by(SystemConfigRevision.version.desc())).scalars().first()
    return revision_to_dict(row) if row else None


def ensure_file_revision(db: Session, config_type: str, config_key: str, path: Path) -> None:
    """Bootstrap a local JSON cache once; never overwrite an existing revision."""
    if get_active_revision(db, config_type, config_key) or not path.is_file():
        return
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return
    if not isinstance(content, dict):
        return
    revision = create_revision(db, config_type, config_key, content, "local-bootstrap")
    activate_revision(db, int(revision["revision_id"]))
