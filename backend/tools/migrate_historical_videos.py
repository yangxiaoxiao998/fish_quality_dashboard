"""Safely recover historical video files from another project data root.

The command is dry-run by default. It only copies a source when the database
path and the source filename agree with the video_id; it never deletes or
overwrites a destination file.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from sqlalchemy import select

from app.paths import DATA_DIR
from app.repositories.db import SessionLocal
from app.repositories.models import VideoTask
from app.services.path_service import to_abs_data_path
from app.services.object_storage_service import object_key_for_video, store_file_metadata
from app.settings import settings


def probe_duration(path: Path) -> float | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        value = float(result.stdout.strip())
        return round(value, 3) if result.returncode == 0 and value > 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True,
                        help="旧项目的 data 目录")
    parser.add_argument("--apply", action="store_true",
                        help="执行复制和时长写回；默认只预览")
    parser.add_argument("--upload", action="store_true",
                        help="将已找到的视频上传到配置的对象存储并登记元数据")
    args = parser.parse_args()
    source_root = args.source_root.expanduser().resolve()
    if not source_root.is_dir():
        parser.error(f"source root does not exist: {source_root}")

    db = SessionLocal()
    copied = skipped = missing = invalid = 0
    try:
        tasks = db.execute(select(VideoTask).order_by(VideoTask.video_id)).scalars().all()
        for task in tasks:
            relative = str(task.source_path or "").removeprefix("data/")
            if not relative:
                missing += 1
                print(f"MISSING {task.video_id} source_path is empty")
                continue
            destination = to_abs_data_path(relative)
            source = source_root / relative
            if destination is None or destination.name != source.name or task.video_id not in source.name:
                invalid += 1
                print(f"INVALID {task.video_id} {relative}")
                continue
            destination_exists = destination.is_file()
            if destination_exists:
                source = destination
            elif not source.is_file():
                missing += 1
                print(f"MISSING {task.video_id} {source}")
                continue
            else:
                source = source
            duration = probe_duration(source)
            if duration is None:
                invalid += 1
                print(f"INVALID_MEDIA {task.video_id} {source}")
                continue
            print(f"MATCH {task.video_id} {source} duration={duration}")
            if not args.apply and not args.upload:
                continue
            if args.apply and not destination.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(f".{destination.name}.migration.partial")
                shutil.copy2(source, temporary)
                temporary.replace(destination)
                source = destination
            task.video_duration_seconds = duration
            if args.upload:
                store_file_metadata(
                    db, owner_type="video_source", owner_id=task.video_id,
                    path=source, bucket=settings.minio_raw_bucket,
                    object_key=object_key_for_video(task.video_id, source.name),
                )
            copied += 1 if args.apply or args.upload else 0
        if args.apply or args.upload:
            db.commit()
        else:
            db.rollback()
    finally:
        db.close()
    print(f"SUMMARY copied={copied} skipped={skipped} missing={missing} invalid={invalid} apply={args.apply} upload={args.upload}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
