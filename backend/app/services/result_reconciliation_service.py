"""JSON、PostgreSQL 和 PDF 结果一致性对账。

这是只读诊断流程。导入后的查询真值是 ``video_task`` / ``fish``，
``pipeline_result.json`` 只作为原始交付物和“待导入结果”来源；报表只
通过数据库记录和文件路径校验，不会用 JSON 静默覆盖数据库。
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.paths import DATA_DIR
from app.repositories.models import DetectionReport, Fish, VideoTask
from app.services.path_service import to_abs_data_path


_METRIC_FIELDS = (
    "body_length_cm",
    "body_length_no_tail_cm",
    "body_height_cm",
    "pred_weight_g",
    "condition_factor",
    "surface_damage_ratio",
)


def _as_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return "done" if normalized == "complete" else normalized


def _fish_key(record: Any, *, video_id: str) -> str:
    if isinstance(record, dict):
        for key in ("fish_uid", "fish_id"):
            value = record.get(key)
            if value not in (None, ""):
                return str(value)
        if record.get("track_id") is not None:
            return f"{video_id}:track:{record['track_id']}"
        if record.get("frame_index") is not None:
            return f"{video_id}:frame:{record['frame_index']}"
    else:
        for key in ("fish_uid", "fish_id"):
            value = getattr(record, key, None)
            if value not in (None, ""):
                return str(value)
        track_id = getattr(record, "track_id", None)
        if track_id is not None:
            return f"{video_id}:track:{track_id}"
    return f"{video_id}:unknown:{id(record)}"


def _counts(records: Iterable[Any]) -> dict[str, int]:
    items = list(records)
    return {
        "total_fish": len(items),
        "complete_fish": sum(_status(item.get("status") if isinstance(item, dict) else getattr(item, "status", None)) == "done" for item in items),
        "incomplete_fish": sum(_status(item.get("status") if isinstance(item, dict) else getattr(item, "status", None)) == "incomplete" for item in items),
        "failed_fish": sum(_status(item.get("status") if isinstance(item, dict) else getattr(item, "status", None)) == "failed" for item in items),
        "damaged_fish": sum(
            (_as_number(item.get("surface_damage_ratio") if isinstance(item, dict) else getattr(item, "surface_damage_ratio", None)) or 0.0) > 0
            for item in items
        ),
    }


def _summary_counts(summary: Any) -> dict[str, int]:
    summary = summary if isinstance(summary, dict) else {}
    return {
        key: int(summary.get(key) or 0)
        for key in (
            "total_fish",
            "complete_fish",
            "incomplete_fish",
            "failed_fish",
            "damaged_fish",
        )
    }


def _read_result(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return None, "pipeline_result.json must contain an object"
    return payload, None


def _result_files(video_ids: set[str] | None) -> dict[str, Path]:
    root = DATA_DIR / "results"
    if not root.exists():
        return {}
    result: dict[str, Path] = {}
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or directory.name.endswith(".partial"):
            continue
        if video_ids is not None and directory.name not in video_ids:
            continue
        path = directory / "reports" / "pipeline_result.json"
        if path.is_file():
            result[directory.name] = path
    return result


def _compare_fish(
    video_id: str,
    json_fish: list[dict[str, Any]],
    db_fish: list[Fish],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    json_map = {_fish_key(item, video_id=video_id): item for item in json_fish}
    db_map = {_fish_key(item, video_id=video_id): item for item in db_fish}
    missing_in_db = sorted(set(json_map) - set(db_map))
    missing_in_json = sorted(set(db_map) - set(json_map))
    if missing_in_db:
        issues.append({"code": "fish_missing_in_database", "fish_keys": missing_in_db})
    if missing_in_json:
        issues.append({"code": "fish_missing_in_json", "fish_keys": missing_in_json})

    for key in sorted(set(json_map) & set(db_map)):
        json_item = json_map[key]
        db_item = db_map[key]
        if _status(json_item.get("status")) != _status(db_item.status):
            issues.append({
                "code": "fish_status_mismatch",
                "fish_key": key,
                "json": _status(json_item.get("status")),
                "database": _status(db_item.status),
            })
        for field in _METRIC_FIELDS:
            json_value = _as_number(json_item.get(field))
            db_value = _as_number(getattr(db_item, field, None))
            if json_value is None and db_value is None:
                continue
            tolerance = max(1e-5, abs(json_value or 0.0) * 1e-5)
            if json_value is None or db_value is None or abs(json_value - db_value) > tolerance:
                issues.append({
                    "code": "fish_metric_mismatch",
                    "fish_key": key,
                    "field": field,
                    "json": json_value,
                    "database": db_value,
                })
    return issues


def _report_checks(
    video_id: str,
    reports: list[DetectionReport],
    authoritative_counts: dict[str, int],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not reports:
        return [{"code": "missing_database_report"}]
    report = reports[0]
    if not report.file_path:
        issues.append({"code": "report_path_empty", "report_id": report.report_id})
    else:
        report_path = to_abs_data_path(report.file_path)
        if report_path is None or not report_path.is_file():
            issues.append({
                "code": "report_file_missing",
                "report_id": report.report_id,
                "file_path": report.file_path,
            })
    snapshot = report.summary_json if isinstance(report.summary_json, dict) else {}
    snapshot_counts = {
        "total_fish": int(snapshot.get("total_fish") or 0),
        "complete_fish": int(snapshot.get("valid_fish") or snapshot.get("complete_fish") or 0),
        "incomplete_fish": int(snapshot.get("incomplete_fish") or 0),
        "failed_fish": int(snapshot.get("failed_fish") or 0),
        "damaged_fish": int(snapshot.get("surface_damage_fish_total") or snapshot.get("damaged_fish") or 0),
    }
    if snapshot_counts != authoritative_counts:
        issues.append({
            "code": "report_summary_mismatch",
            "report_id": report.report_id,
            "report": snapshot_counts,
            "database": authoritative_counts,
        })
    if str(report.video_id or "") != video_id:
        issues.append({"code": "report_video_id_mismatch", "report_id": report.report_id})
    return issues


def reconcile_results(
    db: Session,
    *,
    video_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    selected = {str(value).strip() for value in (video_ids or []) if str(value).strip()}
    selected_or_none = selected or None
    result_files = _result_files(selected_or_none)
    task_stmt = select(VideoTask)
    if selected:
        task_stmt = task_stmt.where(VideoTask.video_id.in_(selected))
    tasks = {task.video_id: task for task in db.execute(task_stmt).scalars().all()}

    fish_stmt = select(Fish)
    if selected:
        fish_stmt = fish_stmt.where(Fish.video_id.in_(selected))
    fish_by_video: dict[str, list[Fish]] = {}
    for fish in db.execute(fish_stmt).scalars().all():
        fish_by_video.setdefault(fish.video_id, []).append(fish)

    report_stmt = select(DetectionReport).where(DetectionReport.video_id.is_not(None))
    if selected:
        report_stmt = report_stmt.where(DetectionReport.video_id.in_(selected))
    reports_by_video: dict[str, list[DetectionReport]] = {}
    for report in db.execute(report_stmt.order_by(DetectionReport.created_at.desc())).scalars().all():
        reports_by_video.setdefault(str(report.video_id), []).append(report)

    all_video_ids = set(result_files) | set(tasks) | set(fish_by_video) | set(reports_by_video)
    if selected:
        all_video_ids |= selected
    items: list[dict[str, Any]] = []

    for video_id in sorted(all_video_ids):
        task = tasks.get(video_id)
        db_fish = fish_by_video.get(video_id, [])
        reports = reports_by_video.get(video_id, [])
        path = result_files.get(video_id)
        item: dict[str, Any] = {
            "video_id": video_id,
            "pipeline_result_path": f"data/results/{video_id}/reports/pipeline_result.json" if path else None,
            "database_task": task is not None,
            "database_imported": bool(task and task.pipeline_result_path),
            "status": "ok",
            "issues": [],
        }
        payload: dict[str, Any] | None = None
        if path is None:
            item["issues"].append({"code": "missing_pipeline_result_json"})
        else:
            payload, error = _read_result(path)
            if error:
                item["issues"].append({"code": "invalid_pipeline_result_json", "error": error})
            else:
                payload_video_id = str(payload.get("video_id") or "").strip()
                if payload_video_id != video_id:
                    item["issues"].append({
                        "code": "pipeline_video_id_mismatch",
                        "json": payload_video_id,
                        "directory": video_id,
                    })
                json_fish = [record for record in (payload.get("fish") or []) if isinstance(record, dict)]
                json_counts = _counts(json_fish)
                item["json_counts"] = json_counts
                item["json_summary"] = _summary_counts(payload.get("summary"))
                if item["json_counts"] != item["json_summary"]:
                    item["issues"].append({
                        "code": "json_summary_mismatch",
                        "summary": item["json_summary"],
                        "actual": item["json_counts"],
                    })

                if task is None:
                    item["issues"].append({"code": "missing_database_task"})
                elif task.pipeline_result_path:
                    db_counts = _counts(db_fish)
                    item["database_counts"] = db_counts
                    stored_counts = {
                        key: int(getattr(task, key, 0) or 0)
                        for key in db_counts
                    }
                    if stored_counts != db_counts:
                        item["issues"].append({
                            "code": "database_summary_mismatch",
                            "stored": stored_counts,
                            "actual": db_counts,
                        })
                    if json_counts != db_counts:
                        item["issues"].append({
                            "code": "json_database_count_mismatch",
                            "json": json_counts,
                            "database": db_counts,
                        })
                    item["issues"].extend(_compare_fish(video_id, json_fish, db_fish))
                    item["issues"].extend(_report_checks(video_id, reports, db_counts))
                else:
                    item["status"] = "pending_import"

        if task is None and not path:
            item["issues"].append({"code": "orphan_database_records"})
        if task is not None and task.pipeline_result_path and payload is None:
            item["issues"].append({"code": "imported_task_without_valid_json"})
        if item["issues"] and item["status"] == "ok":
            item["status"] = "mismatch"
        items.append(item)

    status_counts = Counter(item["status"] for item in items)
    return {
        "ok": not any(item["status"] == "mismatch" for item in items),
        "total": len(items),
        "status_counts": dict(status_counts),
        "items": items,
    }


__all__ = ["reconcile_results"]
