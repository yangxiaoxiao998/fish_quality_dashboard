"""历史数据库、结果 JSON 和文件路径迁移。

路径迁移必须把“可转换”和“需要人工处理”分开：只有确认文件位于当前
``data/`` 且文件仍存在时才更新记录。项目外绝对路径、越界路径和缺失文件
只写入报告，不猜测旧机器上的目录结构，也不把原值静默清空。

命令行入口：

    python backend/tools/migrate_legacy_database.py
    python backend/tools/migrate_legacy_database.py --apply

启动时只调用 ``migrate_paths_to_relative`` 做数据库内的幂等路径规范化；
完整 JSON 扫描、孤儿记录和摘要对账由命令行工具显式执行。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.paths import DATA_DIR, RESULTS_DIR
from app.repositories.db import SessionLocal, engine
from app.repositories.models import (
    DetectionReport,
    Fish,
    FrameFishDetection,
    Stats5Min,
    VideoFrame,
    VideoTask,
)
from app.services.path_service import to_abs_data_path

logger = logging.getLogger("path_migration")


# Only file-like fields are included.  URLs, RTSP addresses, log messages and
# request parameters are not storage paths and must not be rewritten.
PATH_FIELDS: dict[type, tuple[str, ...]] = {
    VideoTask: (
        "source_path",
        "original_input_video",
        "input_video",
        "result_dir",
        "pipeline_result_path",
        "error_conclusion_path",
        "grounded_video_path",
        "measurement_csv_path",
        "complete_match_dir",
        "incomplete_match_dir",
    ),
    Fish: (
        "original_image_path",
        "corrected_image_path",
        "mask_image_path",
        "wound_mask_path",
        "crop_image_path",
    ),
    VideoFrame: ("frame_image_path",),
    FrameFishDetection: (
        "crop_image_path",
        "mask_image_path",
        "corrected_image_path",
    ),
    DetectionReport: ("file_path", "usb_backup_path"),
}

MEASUREMENT_PAIRS: tuple[tuple[str, str], ...] = (
    ("body_length", "body_length_cm"),
    ("body_length_without_tail", "body_length_no_tail_cm"),
    ("body_height", "body_height_cm"),
    ("body_weight", "pred_weight_g"),
)

JSON_PATH_KEYS = {
    "input_video",
    "original_input_video",
    "result_dir",
    "pipeline_result_path",
    "error_conclusion",
    "error_conclusion_path",
    "grounded_video",
    "grounded_video_path",
    "measurement_csv",
    "measurement_csv_path",
    "complete_match_dir",
    "incomplete_match_dir",
    "crop_image_path",
    "original_image_path",
    "corrected_image_path",
    "mask_image_path",
    "wound_mask_path",
    "fish_condition_image_path",
    "arched_back_image_path",
    "report_docx",
    "report_pdf",
    "report_charts",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_identity(obj: Any) -> str:
    for field in ("video_id", "fish_id", "frame_id", "report_id", "id"):
        value = getattr(obj, field, None)
        if value is not None:
            return str(value)
    return "unknown"


def _path_issue(
    *,
    table: str,
    row_id: str,
    field: str,
    raw: str,
    status: str,
    canonical: str | None = None,
    resolved: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "table": table,
        "row_id": row_id,
        "field": field,
        "value": raw,
        "status": status,
    }
    if canonical:
        item["canonical"] = canonical
    if resolved:
        item["resolved_path"] = resolved
    return item


def _known_data_relative(raw: str) -> str | None:
    """Normalize legacy data/static forms without touching arbitrary URLs."""
    value = raw.strip().replace("\\", "/")
    if not value:
        return None
    if value.startswith("/static/"):
        value = value[len("/static/"):]
    elif value.startswith("/data/"):
        value = value[1:]
    elif value.startswith((
        "/uploads/",
        "/results/",
        "/jobs/",
        "/reports/",
        "/calibration/",
        "/logs/",
        "/config/",
    )):
        value = value[1:]
    if value.startswith("data/"):
        value = value[len("data/"):]
    if value.startswith((
        "uploads/",
        "results/",
        "jobs/",
        "reports/",
        "calibration/",
        "logs/",
        "config/",
    )) and ".." not in Path(value).parts:
        return f"data/{value}"
    return None


def _classify_path(raw_value: Any) -> tuple[str, str | None, Path | None]:
    """Return ``(status, canonical, resolved_path)`` for one stored value."""
    if raw_value is None or str(raw_value).strip() == "":
        return "empty", None, None

    raw = str(raw_value).strip().replace("\\", "/")
    # An external URL is not a legacy filesystem path.
    if "://" in raw and not raw.startswith("/"):
        return "non_file_value", None, None

    known_rel = _known_data_relative(raw)
    if known_rel:
        resolved = to_abs_data_path(known_rel)
        if resolved is None:
            return "invalid", known_rel, None
        try:
            resolved.resolve().relative_to(DATA_DIR.resolve())
        except ValueError:
            return "out_of_bounds", known_rel, resolved
        if not resolved.exists():
            return "missing", known_rel, resolved
        return "ok" if raw == known_rel else "convertible", known_rel, resolved

    path = Path(raw)
    if not path.is_absolute():
        # Old relative values were implicitly rooted at data/.
        if ".." in path.parts:
            return "out_of_bounds", None, None
        resolved = DATA_DIR / path
        canonical = f"data/{path.as_posix().lstrip('/')}"
    else:
        try:
            resolved = path.resolve()
            rel = resolved.relative_to(DATA_DIR.resolve())
        except ValueError:
            return "out_of_bounds", None, path
        canonical = f"data/{rel.as_posix()}"

    try:
        resolved.resolve().relative_to(DATA_DIR.resolve())
    except ValueError:
        return "out_of_bounds", canonical, resolved
    if not resolved.exists():
        return "missing", canonical, resolved
    return "ok" if raw == canonical else "convertible", canonical, resolved


def _iter_path_rows(db: Session) -> Iterable[tuple[Any, str, str]]:
    for model, fields in PATH_FIELDS.items():
        for row in db.execute(select(model)).scalars().all():
            for field in fields:
                if hasattr(row, field):
                    yield row, field, model.__tablename__


def _migrate_database_paths(db: Session, *, apply: bool) -> dict[str, Any]:
    report: dict[str, Any] = {
        "converted": [],
        "missing_files": [],
        "out_of_bounds": [],
        "invalid_values": [],
        "skipped": 0,
    }
    for row, field, table in _iter_path_rows(db):
        raw_value = getattr(row, field, None)
        if raw_value is None or str(raw_value).strip() == "":
            continue
        status, canonical, resolved = _classify_path(raw_value)
        row_id = _row_identity(row)
        if status == "convertible" and canonical:
            entry = _path_issue(
                table=table,
                row_id=row_id,
                field=field,
                raw=str(raw_value),
                status="converted",
                canonical=canonical,
                resolved=str(resolved) if resolved else None,
            )
            report["converted"].append(entry)
            if apply:
                setattr(row, field, canonical)
        elif status == "missing":
            report["missing_files"].append(
                _path_issue(
                    table=table,
                    row_id=row_id,
                    field=field,
                    raw=str(raw_value),
                    status=status,
                    canonical=canonical,
                    resolved=str(resolved) if resolved else None,
                )
            )
        elif status in {"out_of_bounds", "invalid"}:
            report["out_of_bounds" if status == "out_of_bounds" else "invalid_values"].append(
                _path_issue(
                    table=table,
                    row_id=row_id,
                    field=field,
                    raw=str(raw_value),
                    status=status,
                    canonical=canonical,
                    resolved=str(resolved) if resolved else None,
                )
            )
        else:
            report["skipped"] += 1
    return report


def _json_value_is_path(key: str, value: str, *, in_report_charts: bool = False) -> bool:
    lower = key.lower()
    if lower.endswith("_url") or lower in {"video_id", "camera_id"}:
        return False
    if lower in JSON_PATH_KEYS or in_report_charts:
        return True
    if lower.endswith(("_path", "_dir")):
        return True
    normalized = value.replace("\\", "/")
    return normalized.startswith(("/data/", "/static/", "data/", "results/", "uploads/"))


def _rewrite_json_paths(value: Any, *, key: str = "", in_report_charts: bool = False) -> tuple[Any, list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    if isinstance(value, dict):
        output = dict(value)
        charts = key == "report_charts"
        for child_key, child_value in value.items():
            rewritten, child_issues = _rewrite_json_paths(
                child_value,
                key=str(child_key),
                in_report_charts=charts,
            )
            output[child_key] = rewritten
            issues.extend(child_issues)
        return output, issues
    if isinstance(value, list):
        output: list[Any] = []
        for item in value:
            rewritten, child_issues = _rewrite_json_paths(
                item,
                key=key,
                in_report_charts=in_report_charts,
            )
            output.append(rewritten)
            issues.extend(child_issues)
        return output, issues
    if not isinstance(value, str) or not _json_value_is_path(key, value, in_report_charts=in_report_charts):
        return value, issues

    status, canonical, resolved = _classify_path(value)
    if status == "convertible" and canonical:
        issues.append({
            "field": key,
            "value": value,
            "status": "converted",
            "canonical": canonical,
            "resolved_path": str(resolved) if resolved else None,
        })
        return canonical, issues
    if status in {"missing", "out_of_bounds", "invalid"}:
        issues.append({
            "field": key,
            "value": value,
            "status": status,
            "canonical": canonical,
            "resolved_path": str(resolved) if resolved else None,
        })
    return value, issues


def _write_json_atomically(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _migrate_result_json_paths(*, apply: bool) -> dict[str, Any]:
    report: dict[str, Any] = {
        "files_scanned": 0,
        "files_updated": [],
        "missing_files": [],
        "out_of_bounds": [],
        "invalid_values": [],
        "parse_errors": [],
    }
    if not DATA_DIR.exists():
        return report

    for path in sorted(DATA_DIR.rglob("*.json")):
        # Runtime caches and model files are not result payloads.  They are
        # still harmless to scan, but only pipeline results are eligible for
        # rewrite so calibration/config JSON remains untouched.
        if not path.name == "pipeline_result.json":
            continue
        report["files_scanned"] += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            report["parse_errors"].append({"path": str(path), "error": str(exc)})
            continue
        rewritten, issues = _rewrite_json_paths(payload)
        for issue in issues:
            issue["path"] = str(path.relative_to(DATA_DIR).as_posix())
            target = issue["status"]
            if target == "missing":
                report["missing_files"].append(issue)
            elif target == "out_of_bounds":
                report["out_of_bounds"].append(issue)
            elif target == "invalid":
                report["invalid_values"].append(issue)
        converted = [issue for issue in issues if issue["status"] == "converted"]
        if converted and apply:
            _write_json_atomically(path, rewritten)
        if converted:
            report["files_updated"].append({
                "path": str(path.relative_to(DATA_DIR).as_posix()),
                "converted": converted,
                "applied": apply,
            })
    return report


def _numeric(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _measurement_migration(db: Session, *, apply: bool) -> dict[str, Any]:
    report: dict[str, Any] = {
        "backfilled": [],
        "new_value_preserved": [],
        "differences": [],
        "invalid_values": [],
    }
    for fish in db.execute(select(Fish)).scalars().all():
        for old_field, new_field in MEASUREMENT_PAIRS:
            if not hasattr(fish, old_field) or not hasattr(fish, new_field):
                continue
            old_value = getattr(fish, old_field)
            new_value = getattr(fish, new_field)
            old_num = _numeric(old_value)
            new_num = _numeric(new_value)
            if old_value is not None and old_num is None:
                report["invalid_values"].append({
                    "fish_id": fish.fish_id,
                    "field": old_field,
                    "value": old_value,
                })
            if new_value is not None and new_num is None:
                report["invalid_values"].append({
                    "fish_id": fish.fish_id,
                    "field": new_field,
                    "value": new_value,
                })
            for field_name, number in ((old_field, old_num), (new_field, new_num)):
                if number is not None and number < 0:
                    report["invalid_values"].append({
                        "fish_id": fish.fish_id,
                        "field": field_name,
                        "value": number,
                        "reason": "negative_measurement",
                    })
            if new_num is None and old_num is not None:
                entry = {
                    "fish_id": fish.fish_id,
                    "old_field": old_field,
                    "new_field": new_field,
                    "value": old_num,
                }
                report["backfilled"].append(entry)
                if apply:
                    setattr(fish, new_field, old_num)
            elif new_num is not None and old_num is None and old_value is None:
                report["new_value_preserved"].append({
                    "fish_id": fish.fish_id,
                    "field": new_field,
                    "value": new_num,
                })
            elif new_num is not None and old_num is not None:
                tolerance = max(1e-6, abs(new_num) * 1e-6)
                if abs(new_num - old_num) > tolerance:
                    report["differences"].append({
                        "fish_id": fish.fish_id,
                        "old_field": old_field,
                        "old_value": old_num,
                        "new_field": new_field,
                        "new_value": new_num,
                    })
    return report


def _orphan_report(db: Session) -> dict[str, Any]:
    task_ids = set(db.execute(select(VideoTask.video_id)).scalars().all())
    fish_rows = db.execute(select(Fish)).scalars().all()
    frame_rows = db.execute(select(VideoFrame)).scalars().all()
    detection_rows = db.execute(select(FrameFishDetection)).scalars().all()
    stats_rows = db.execute(select(Stats5Min)).scalars().all()
    report_rows = db.execute(select(DetectionReport)).scalars().all()
    return {
        "fish": [row.fish_id for row in fish_rows if row.video_id not in task_ids],
        "video_frame": [row.frame_id for row in frame_rows if row.video_id not in task_ids],
        "frame_fish_detection": [row.id for row in detection_rows if row.video_id not in task_ids],
        "stats_5min": [row.id for row in stats_rows if row.video_id and row.video_id not in task_ids],
        "stats_5min_without_video_id": [row.id for row in stats_rows if row.video_id is None],
        "detection_report": [row.report_id for row in report_rows if row.video_id and row.video_id not in task_ids],
    }


def _summary_mismatch_report(db: Session) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for task in db.execute(select(VideoTask)).scalars().all():
        rows = db.execute(
            select(Fish.status, Fish.surface_damage_ratio).where(Fish.video_id == task.video_id)
        ).all()
        counts = {
            "total_fish": len(rows),
            "complete_fish": sum(str(status) in {"done", "complete"} for status, _ in rows),
            "incomplete_fish": sum(str(status) == "incomplete" for status, _ in rows),
            "failed_fish": sum(str(status) == "failed" for status, _ in rows),
            "damaged_fish": sum(float(value) > 0 for _, value in rows if value is not None),
        }
        stored = {field: int(getattr(task, field, 0) or 0) for field in counts}
        if counts != stored:
            mismatches.append({
                "video_id": task.video_id,
                "stored": stored,
                "actual": counts,
            })
    return mismatches


def _source_type_status(*, apply: bool) -> dict[str, Any]:
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("video_task")}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)}
    exists = "source_type" in columns
    result: dict[str, Any] = {"exists_before": exists, "removed": False, "applied": apply}
    if exists and apply:
        try:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE video_task DROP COLUMN source_type"))
            result["removed"] = True
        except Exception as exc:  # noqa: BLE001
            result["status"] = "error"
            result["error"] = str(exc)
            return result
    result["status"] = "present" if exists and not apply else "removed_or_absent"
    return result


def run_legacy_database_migration(
    *,
    apply: bool = False,
    include_json: bool = True,
) -> dict[str, Any]:
    """Inspect legacy data and optionally apply safe, deterministic changes."""
    report: dict[str, Any] = {
        "generated_at": _utc_now(),
        "apply": apply,
        "data_dir": str(DATA_DIR),
        "source_type": _source_type_status(apply=apply),
        "database_paths": {},
        "result_json_paths": {},
        "measurements": {},
        "orphans": {},
        "summary_mismatches": [],
    }

    db = SessionLocal()
    try:
        report["database_paths"] = _migrate_database_paths(db, apply=apply)
        report["measurements"] = _measurement_migration(db, apply=apply)
        report["orphans"] = _orphan_report(db)
        report["summary_mismatches"] = _summary_mismatch_report(db)
        if apply:
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    if include_json:
        report["result_json_paths"] = _migrate_result_json_paths(apply=apply)
    return report


def migrate_paths_to_relative() -> dict[str, Any]:
    """Startup-safe database path normalization.

    Existing absolute paths outside ``data/`` and missing files are retained so
    operators can inspect and recover them from the generated report later.
    """
    db = SessionLocal()
    try:
        result = _migrate_database_paths(db, apply=True)
        db.commit()
        logger.info(
            "[path_migration] converted=%d missing=%d out_of_bounds=%d",
            len(result["converted"]),
            len(result["missing_files"]),
            len(result["out_of_bounds"]),
        )
        if result["missing_files"] or result["out_of_bounds"]:
            logger.warning(
                "[path_migration] unresolved paths retained: missing=%d out_of_bounds=%d",
                len(result["missing_files"]),
                len(result["out_of_bounds"]),
            )
        return result
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.error("[path_migration] migration failed: %s", exc)
        return {"converted": [], "missing_files": [], "out_of_bounds": [], "error": str(exc)}
    finally:
        db.close()


__all__ = [
    "migrate_paths_to_relative",
    "run_legacy_database_migration",
]
