"""Stage-two storage contract with a safe local default and optional MinIO backend."""
from __future__ import annotations

import hashlib
import mimetypes
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.paths import DATA_DIR
from app.services.path_service import to_abs_data_path, to_data_relative, to_static_url
from app.settings import settings


class ObjectStorageError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ObjectStorage:
    """Local/MinIO adapter. Local remains the default until migration is approved."""

    def __init__(self) -> None:
        self.backend = settings.storage_backend
        self._client: Any = None

    def _minio(self) -> Any:
        if self._client is None:
            try:
                from minio import Minio
            except ImportError as exc:
                raise ObjectStorageError("MinIO backend requires the minio package") from exc
            if not settings.minio_access_key or not settings.minio_secret_key:
                raise ObjectStorageError("MINIO_ACCESS_KEY and MINIO_SECRET_KEY are required")
            self._client = Minio(settings.minio_endpoint, access_key=settings.minio_access_key,
                                 secret_key=settings.minio_secret_key, secure=settings.minio_secure,
                                 region=settings.minio_region)
        return self._client

    def put_file(self, path: Path, bucket: str, object_key: str) -> dict[str, Any]:
        key = object_key.strip().replace("\\", "/")
        if not key or key.startswith("/") or ".." in Path(key).parts:
            raise ObjectStorageError("object_key must be a safe relative key")
        if not path.is_file() or path.stat().st_size <= 0:
            raise ObjectStorageError(f"source file missing or empty: {path}")
        digest = sha256_file(path)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if self.backend == "local":
            # Local mode uses the same logical key namespace, while preserving
            # the legacy data/uploads layout through the copied object cache.
            target = DATA_DIR / key
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.partial")
            with path.open("rb") as source, temporary.open("wb") as target_stream:
                shutil.copyfileobj(source, target_stream, length=1024 * 1024)
            temporary.replace(target)
            return {"backend": "local", "bucket": None, "object_key": key,
                    "size_bytes": target.stat().st_size, "sha256": digest,
                    "content_type": content_type, "status": "ready"}
        client = self._minio()
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
        result = client.fput_object(bucket, key, str(path), content_type=content_type)
        return {"backend": "minio", "bucket": bucket, "object_key": key,
                "size_bytes": path.stat().st_size, "etag": result.etag,
                "sha256": digest, "content_type": content_type, "status": "ready"}

    def exists(self, bucket: str | None, object_key: str, source_local_path: str | None = None) -> bool:
        if not object_key or object_key.startswith("/") or ".." in Path(object_key).parts:
            return False
        if self.backend == "local":
            path = to_abs_data_path(source_local_path or object_key)
            return bool(path and path.is_file())
        try:
            self._minio().stat_object(bucket or "", object_key)
            return True
        except Exception:
            return False

    def presigned_url(self, bucket: str, object_key: str, expires_seconds: int = 900) -> str:
        if not object_key or object_key.startswith("/") or ".." in Path(object_key).parts:
            raise ObjectStorageError("object_key must be a safe relative key")
        if self.backend == "local":
            rel = to_data_relative(DATA_DIR / object_key) or object_key
            return to_static_url(rel) or f"/static/{rel}"
        return self._minio().presigned_get_object(bucket, object_key,
                                                   expires=timedelta(seconds=expires_seconds))

    def remove(self, bucket: str | None, object_key: str) -> None:
        """Remove a stored object; local mode removes its logical cache."""
        if not object_key or object_key.startswith("/") or ".." in Path(object_key).parts:
            raise ObjectStorageError("object_key must be a safe relative key")
        if self.backend == "local":
            path = DATA_DIR / object_key
            path.unlink(missing_ok=True)
            return
        self._minio().remove_object(bucket or "", object_key)

    def get_file(self, bucket: str | None, object_key: str, destination: Path) -> dict[str, Any]:
        """Materialize an object locally before media or algorithm processing."""
        if not object_key or object_key.startswith("/") or ".." in Path(object_key).parts:
            raise ObjectStorageError("object_key must be a safe relative key")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.partial")
        try:
            if self.backend == "local":
                source = to_abs_data_path(object_key)
                if source is None or not source.is_file():
                    raise ObjectStorageError(f"object missing: {object_key}")
                with source.open("rb") as source_stream, temporary.open("wb") as target_stream:
                    shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
            else:
                response = self._minio().get_object(bucket or "", object_key)
                try:
                    with temporary.open("wb") as target_stream:
                        shutil.copyfileobj(response, target_stream, length=1024 * 1024)
                finally:
                    response.close()
                    response.release_conn()
            temporary.replace(destination)
            return {"path": str(destination), "size_bytes": destination.stat().st_size,
                    "sha256": sha256_file(destination), "status": "ready"}
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


def object_key_for_video(video_id: str, filename: str, *, kind: str = "source") -> str:
    """Return a stable key without exposing a machine-local path."""
    from datetime import datetime

    try:
        day = datetime.strptime(video_id[:8], "%Y%m%d")
        prefix = day.strftime("%Y/%m/%d")
    except ValueError:
        prefix = "unknown/unknown/unknown"
    return f"videos/{prefix}/{video_id}/{kind}{Path(filename).suffix.lower() or '.bin'}"


object_storage = ObjectStorage()


def download_owned_object(db: Any, *, owner_type: str, owner_id: str,
                          destination: Path) -> dict[str, Any]:
    """Download the newest ready object for an owner into a local cache."""
    from sqlalchemy import select
    from app.repositories.models import StoredObject

    row = db.execute(select(StoredObject).where(
        StoredObject.owner_type == owner_type,
        StoredObject.owner_id == owner_id,
        StoredObject.status == "ready",
    ).order_by(StoredObject.created_at.desc())).scalars().first()
    if row is None:
        raise ObjectStorageError(f"ready object not found: {owner_type}/{owner_id}")
    result = object_storage.get_file(row.bucket, row.object_key, destination)
    if row.sha256 and result["sha256"] != row.sha256:
        destination.unlink(missing_ok=True)
        raise ObjectStorageError(f"object checksum mismatch: {row.object_key}")
    return {"object_id": row.object_id, "bucket": row.bucket,
            "object_key": row.object_key, **result}


def store_file_metadata(db: Any, *, owner_type: str, owner_id: str,
                        path: Path, bucket: str, object_key: str) -> dict[str, Any]:
    """Upload one file and upsert its metadata, making retries idempotent."""
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.repositories.models import StoredObject

    result = object_storage.put_file(path, bucket, object_key)
    row = db.execute(select(StoredObject).where(
        StoredObject.owner_type == owner_type,
        StoredObject.owner_id == owner_id,
        StoredObject.object_key == object_key,
    )).scalars().first()
    if row is None:
        row = StoredObject(owner_type=owner_type, owner_id=owner_id,
                           object_key=object_key)
        db.add(row)
    row.backend = result["backend"]
    row.bucket = result.get("bucket") or bucket
    row.content_type = result.get("content_type")
    row.size_bytes = result.get("size_bytes")
    row.etag = result.get("etag")
    row.sha256 = result.get("sha256")
    row.status = "ready"
    row.source_local_path = to_data_relative(path)
    row.last_error = None
    row.uploaded_at = datetime.now(timezone.utc)
    row.verified_at = row.uploaded_at
    return {"object_id": row.object_id, **result}
