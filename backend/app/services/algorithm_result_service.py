from __future__ import annotations

import json
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.paths import DATA_DIR, PROJECT_ROOT
from app.services.algorithm_cli_service import (
    algorithm_path_to_static_url,
    error_conclusion_path,
    pipeline_result_path,
    read_error_conclusion,
    read_pipeline_result,
)

RESULTS_DIR = DATA_DIR / "results"


def _rebuild_stats_5min_for_video(db: Session, video_task: Any) -> None:
    """Rebuild the optional 5-minute summary from the authoritative fish rows."""
    from sqlalchemy import delete, select
    from app.repositories.models import Fish, Stats5Min

    db.execute(delete(Stats5Min).where(Stats5Min.video_id == video_task.video_id))
    fish_rows = list(
        db.execute(
            select(Fish).where(Fish.video_id == video_task.video_id)
        ).scalars().all()
    )
    if not fish_rows or video_task.created_at is None:
        return

    base = video_task.created_at
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    else:
        base = base.astimezone(timezone.utc)
    fps = float(video_task.fps_estimate or 25.0)
    buckets: dict[tuple[str, object], Any] = {}
    for fish in fish_rows:
        frame_offset = float(fish.frame_index or 0) / max(fps, 1.0)
        timestamp = base + timedelta(seconds=frame_offset)
        bucket_start = timestamp.replace(
            minute=(timestamp.minute // 5) * 5,
            second=0,
            microsecond=0,
        )
        key = (video_task.video_id, bucket_start)
        row = buckets.get(key)
        if row is None:
            row = Stats5Min(
                video_id=video_task.video_id,
                bucket_start=bucket_start,
                bucket_end=bucket_start + timedelta(minutes=5),
                count=0,
                sum_body_length=0.0,
                sum_body_height=0.0,
                sum_body_weight=0.0,
                sum_condition_factor=0.0,
                abnormal_count=0,
            )
            buckets[key] = row
            db.add(row)
        row.count += 1
        row.sum_body_length += float(fish.body_length_cm or 0.0)
        row.sum_body_height += float(fish.body_height_cm or 0.0)
        row.sum_body_weight += float(fish.pred_weight_g or 0.0)
        row.sum_condition_factor += float(fish.condition_factor or 0.0)
        row.abnormal_count += int(float(fish.surface_damage_ratio or 0.0) > 0)


def _backfill_fish_image_paths(video_id: str, item: dict[str, Any]) -> dict[str, Any]:
    """回填 fish_condition_image_path 和 arched_back_image_path（如果原始 JSON 为空）。

    fish_uid 格式：{video_id}_id_{track_id}，例如 20260905_0008_id_0
    目录名格式：id_{track_id}_{frame_index:012d}，例如 id_0_000000000002
    """
    fish_uid = str(item.get("fish_uid") or "").strip()
    track_id = item.get("track_id")
    frame_index = item.get("frame_index")
    fc_path = item.get("fish_condition_image_path")
    ab_path = item.get("arched_back_image_path")

    if not fc_path and track_id is not None and frame_index is not None:
        dir_name = f"id_{track_id}_{int(frame_index):012d}"
        candidate = RESULTS_DIR / video_id / "fish_images" / "fish_condition_images" / dir_name / f"{dir_name}_with_line.jpg"
        if candidate.is_file():
            item = dict(item)
            item["fish_condition_image_path"] = f"data/{candidate.relative_to(DATA_DIR).as_posix()}"

    if not ab_path and track_id is not None and frame_index is not None:
        overlay_name = f"id_{track_id}_{int(frame_index):012d}"
        candidate = RESULTS_DIR / video_id / "fish_images" / "arched_back" / "overlays" / f"{overlay_name}_overlay.png"
        if candidate.is_file():
            item = dict(item)
            item["arched_back_image_path"] = f"data/{candidate.relative_to(DATA_DIR).as_posix()}"

    return item


def _empty_to_none(value: Any) -> Any:
    if value == "":
        return None
    return value


def _maybe_float(value: Any) -> float | None:
    value = _empty_to_none(value)
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _maybe_int(value: Any) -> int | None:
    value = _empty_to_none(value)
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return None


def _maybe_str(value: Any) -> str | None:
    value = _empty_to_none(value)
    if value is None:
        return None
    return str(value)


def _measurement_length_fields(item: dict[str, Any], *, is_done: bool) -> tuple[float | None, float | None]:
    """Keep full length and no-tail length as separate measurements."""
    if not is_done:
        return None, None
    full_length = _maybe_float(item.get("body_length_cm"))
    no_tail_length = _maybe_float(item.get("body_length_no_tail_cm"))
    return full_length, no_tail_length


def _set_if_has(obj: Any, field: str, value: Any) -> bool:
    if hasattr(obj, field):
        setattr(obj, field, value)
        return True
    return False


def _path_to_project_relative(value: str | None) -> str | None:
    if not value:
        return None

    p = Path(str(value))
    if not p.is_absolute():
        return p.as_posix()

    try:
        return p.relative_to(PROJECT_ROOT).as_posix()
    except Exception:
        return p.as_posix()


def _path_to_data_relative(value: str | None) -> str | None:
    """Persist only paths inside the runtime data tree."""
    if not value:
        return None
    from app.services.path_service import to_data_relative

    raw = str(value).replace("\\", "/")
    path = Path(raw)
    if path.is_absolute():
        return to_data_relative(path)

    normalized = raw.lstrip("/")
    if normalized.startswith("data/"):
        return normalized
    if normalized.startswith(
        ("uploads/", "results/", "jobs/", "reports/", "calibration/", "logs/", "config/")
    ):
        return f"data/{normalized}"
    return None


def _normalize_status(value: str | None) -> str:
    if not value:
        return "failed"
    value = str(value).strip().lower()
    if value in {"done", "failed", "interrupted", "incomplete", "processing", "queued"}:
        return value
    return value


def _result_dir_for_video(video_id: str) -> str:
    return f"data/results/{video_id}"


def _make_fish_id(video_id: str, item: dict[str, Any]) -> str:
    fish_uid = _maybe_str(item.get("fish_uid"))
    if fish_uid:
        return fish_uid[:64]

    track_id = _maybe_int(item.get("track_id"))
    if track_id is not None:
        return f"{video_id}_id_{track_id}"[:64]

    frame_index = _maybe_int(item.get("frame_index")) or 0
    return f"{video_id}_frame_{frame_index}"[:64]


def load_pipeline_result(video_id: str) -> dict[str, Any]:
    return read_pipeline_result(video_id)


def load_pipeline_result_from_path(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p

    if not p.exists():
        raise FileNotFoundError(f"pipeline_result.json 不存在：{p}")

    return json.loads(p.read_text(encoding="utf-8"))


def pipeline_result_to_video_task_fields(payload: dict[str, Any]) -> dict[str, Any]:
    video_id = str(payload.get("video_id") or "").strip()
    if not video_id:
        raise ValueError("pipeline_result.json 缺少 video_id")

    status = _normalize_status(payload.get("status"))
    summary = payload.get("summary") or {}

    error_conclusion_rel = payload.get("error_conclusion")
    if not error_conclusion_rel:
        error_conclusion_rel = f"data/results/{video_id}/reports/error_conclusion.txt"

    pipeline_result_rel = payload.get("pipeline_result_path")
    if not pipeline_result_rel:
        pipeline_result_rel = f"data/results/{video_id}/reports/pipeline_result.json"

    result_dir = payload.get("result_dir") or _result_dir_for_video(video_id)

    fields = {
        "video_id": video_id,
        "status": status,

        # 算法新字段
        "original_input_video": _path_to_data_relative(payload.get("original_input_video")),
        "input_video": _path_to_data_relative(payload.get("input_video")),
        "video_duration_seconds": _maybe_float(payload.get("video_duration_seconds")),
        "processing_duration_seconds": _maybe_float(
            payload.get("processing_duration_seconds") or payload.get("processing_seconds")
        ),
        "failed_stage": _maybe_str(payload.get("failed_stage")),
        "error_message": _maybe_str(payload.get("error_message")),
        "result_dir": _path_to_data_relative(result_dir),
        "pipeline_result_path": _path_to_data_relative(pipeline_result_rel),
        "error_conclusion_path": _path_to_data_relative(error_conclusion_rel),
        "grounded_video_path": _path_to_data_relative(payload.get("grounded_video")),
        "measurement_csv_path": _path_to_data_relative(payload.get("measurement_csv")),
        "complete_match_dir": _path_to_data_relative(payload.get("complete_match_dir")),
        "incomplete_match_dir": _path_to_data_relative(payload.get("incomplete_match_dir")),

        # summary
        "total_fish": _maybe_int(summary.get("total_fish")) or 0,
        "complete_fish": _maybe_int(summary.get("complete_fish")) or 0,
        "incomplete_fish": _maybe_int(summary.get("incomplete_fish")) or 0,
        "failed_fish": _maybe_int(summary.get("failed_fish")) or 0,
        "damaged_fish": _maybe_int(summary.get("damaged_fish")) or 0,
        "avg_surface_damage_ratio": _maybe_float(summary.get("avg_surface_damage_ratio")),
        "max_surface_damage_ratio": _maybe_float(summary.get("max_surface_damage_ratio")),

        # 旧模型兼容字段
        "source_path": _path_to_data_relative(payload.get("input_video")),
        "processed_fish": _maybe_int(summary.get("complete_fish")) or 0,
        "last_error": _maybe_str(payload.get("error_message")),
        "current_algorithm": None,
        "current_fish_id": None,
    }

    if status != "done" and not fields["last_error"]:
        try:
            text = read_error_conclusion(video_id)
        except Exception:
            text = ""
        fields["last_error"] = text or fields["failed_stage"] or "algorithm task failed"

    # Older pipeline results did not carry timing fields. Keep import
    # compatible; the task status writer will fill processing duration.

    return fields


def upsert_video_task_from_payload(db: Session, payload: dict[str, Any]) -> Any:
    from app.repositories.models import VideoTask

    fields = pipeline_result_to_video_task_fields(payload)
    video_id = fields["video_id"]

    obj = db.get(VideoTask, video_id)

    if obj is None:
        obj = VideoTask(video_id=video_id)
        db.add(obj)

    for key, value in fields.items():
        if key == "source_path" and getattr(obj, "source_path", None):
            continue
        # Older pipeline results do not contain timing fields. Do not erase
        # values already captured by the task lifecycle in that case.
        if key in {"video_duration_seconds", "processing_duration_seconds"}:
            if value is None and getattr(obj, key, None) is not None:
                continue
        _set_if_has(obj, key, value)

    return obj


def pipeline_fish_to_db_fields(item: dict[str, Any]) -> dict[str, Any]:
    video_id = str(item.get("video_id") or "").strip()
    if not video_id:
        raise ValueError("fish item 缺少 video_id")

    status = _normalize_status(item.get("status"))
    # pipeline 的结果文件使用 complete 表示完整鱼，数据库旧流程使用
    # done；两者都代表已经完成五维测量。
    is_done = status in {"done", "complete"}

    body_length_cm, body_length_no_tail_cm = _measurement_length_fields(
        item, is_done=is_done
    )
    body_height_cm = _maybe_float(item.get("body_height_cm")) if is_done else None
    pred_weight_g = _maybe_float(item.get("pred_weight_g")) if is_done else None
    condition_factor = _maybe_float(item.get("condition_factor")) if is_done else None
    surface_damage_ratio = (
        _maybe_float(item.get("surface_damage_ratio")) if is_done else None
    )

    crop_image_path = _path_to_data_relative(item.get("crop_image_path"))
    crop_image_url = algorithm_path_to_static_url(crop_image_path)

    fish_uid = _maybe_str(item.get("fish_uid"))
    fish_id = _make_fish_id(video_id, item)

    fields = {
        # 算法新字段
        "fish_id": fish_id,
        "video_id": video_id,
        "track_id": _maybe_int(item.get("track_id")),
        "fish_uid": fish_uid,
        "frame_index": _maybe_int(item.get("frame_index")),
        "crop_image_path": crop_image_path,
        "crop_image_url": crop_image_url,
        "body_length_cm": body_length_cm,
        "body_length_no_tail_cm": body_length_no_tail_cm,
        "body_height_cm": body_height_cm,
        "pred_weight_g": pred_weight_g,
        "hex_color": _maybe_str(item.get("hex_color")),
        "condition_factor": condition_factor,
        "surface_damage_ratio": surface_damage_ratio,
        # 对外数据库状态统一为 done/incomplete/failed；pipeline 的
        # complete 只保留在结果 JSON 中，避免旧查询只认 done 时漏统计。
        "status": "done" if status == "complete" else status,
        "error_message": _maybe_str(item.get("error_message")) or "",

        # 旧模型兼容字段
        "body_length": body_length_cm,
        "body_length_without_tail": body_length_no_tail_cm,
        "body_height": body_height_cm,
        "body_weight": pred_weight_g,
        "error_msg": _maybe_str(item.get("error_message")) or "",
    }

    return fields


def upsert_fish_from_pipeline_item(db: Session, item: dict[str, Any]) -> Any:
    from app.repositories.models import Fish

    fields = pipeline_fish_to_db_fields(item)
    fish_id = fields["fish_id"]

    obj = db.get(Fish, fish_id)

    if obj is None:
        obj = Fish(
            fish_id=fish_id,
            video_id=fields["video_id"],
            track_id=fields.get("track_id"),
            status=fields.get("status") or "processing",
        )
        db.add(obj)

    for key, value in fields.items():
        _set_if_has(obj, key, value)

    return obj


def import_pipeline_result_payload_to_db(
    db: Session,
    payload: dict[str, Any],
    *,
    commit: bool = True,
    expected_video_id: str | None = None,
) -> dict[str, Any]:
    video_id = str(payload.get("video_id") or "").strip()
    if not video_id:
        raise ValueError("pipeline_result payload 缺少 video_id")
    if expected_video_id and video_id != str(expected_video_id).strip():
        raise ValueError(
            f"pipeline_result video_id={video_id} does not match "
            f"expected video_id={expected_video_id}"
        )

    status = _normalize_status(payload.get("status"))

    video_task = upsert_video_task_from_payload(db, payload)

    raw_items = payload.get("fish") or []
    fish_total = len(raw_items)
    fish_imported = 0
    fish_skipped = 0

    if status in {"done", "complete"}:
        from sqlalchemy import delete
        from app.repositories.models import Fish

        # Validate the complete payload before replacing existing rows. A
        # rerun for one video is a full replacement, not an additive upsert.
        prepared: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in raw_items:
            if not isinstance(item, dict):
                raise ValueError(f"video_id={video_id}: fish item must be an object")
            normalized_item = dict(item)
            normalized_item.setdefault("video_id", video_id)
            fields = pipeline_fish_to_db_fields(normalized_item)
            fish_id = str(fields["fish_id"])
            if fish_id in seen_ids:
                raise ValueError(f"video_id={video_id}: duplicate fish_id={fish_id}")
            seen_ids.add(fish_id)
            prepared.append(fields)

        db.execute(delete(Fish).where(Fish.video_id == video_id))
        for fields in prepared:
            db.add(
                Fish(
                    **{
                        key: value
                        for key, value in fields.items()
                        if hasattr(Fish, key)
                    }
                )
            )
        db.flush()
        fish_imported = len(prepared)
    else:
        fish_skipped = fish_total

    if status in {"done", "complete"}:
        from sqlalchemy import func, select
        from app.repositories.models import Fish

        counts = db.execute(
            select(Fish.status, func.count())
            .where(Fish.video_id == video_id)
            .group_by(Fish.status)
        ).all()
        count_map = {str(row[0]): int(row[1]) for row in counts}
        video_task.total_fish = fish_imported
        video_task.processed_fish = fish_imported
        video_task.complete_fish = count_map.get("done", 0) + count_map.get("complete", 0)
        video_task.incomplete_fish = count_map.get("incomplete", 0)
        video_task.failed_fish = count_map.get("failed", 0)
        damage_values = [
            float(value)
            for value in db.execute(
                select(Fish.surface_damage_ratio).where(
                    Fish.video_id == video_id,
                    Fish.surface_damage_ratio.is_not(None),
                )
            ).scalars().all()
        ]
        video_task.damaged_fish = sum(value > 0 for value in damage_values)
        video_task.avg_surface_damage_ratio = (
            sum(damage_values) / len(damage_values) if damage_values else None
        )
        video_task.max_surface_damage_ratio = max(damage_values) if damage_values else None
        _rebuild_stats_5min_for_video(db, video_task)

    if commit:
        db.commit()

    return {
        "ok": fish_skipped == 0,
        "video_id": video_id,
        "status": status,
        "video_task_saved": True,
        "fish_total": fish_total,
        "fish_imported": fish_imported,
        "fish_skipped": fish_skipped,
        "pipeline_result_path": f"data/results/{video_id}/reports/pipeline_result.json",
        "error_conclusion_path": f"data/results/{video_id}/reports/error_conclusion.txt",
    }


def import_pipeline_result_to_db(
    db: Session,
    video_id: str,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    payload = load_pipeline_result(video_id)
    return import_pipeline_result_payload_to_db(
        db,
        payload,
        commit=commit,
        expected_video_id=video_id,
    )


def import_pipeline_result_file_to_db(
    db: Session,
    path: str | Path,
    *,
    commit: bool = True,
    expected_video_id: str | None = None,
) -> dict[str, Any]:
    payload = load_pipeline_result_from_path(path)
    return import_pipeline_result_payload_to_db(
        db,
        payload,
        commit=commit,
        expected_video_id=expected_video_id,
    )


def _fish_row_to_response(item: dict[str, Any]) -> dict[str, Any]:
    """Convert a Fish database row or dict to the fish response format."""
    out = dict(item)
    out["display_fish_id"] = item.get("track_id")
    out["crop_image_url"] = algorithm_path_to_static_url(item.get("crop_image_path"))
    return out


def video_task_to_response_payload(db: Session, video_id: str) -> dict[str, Any] | None:
    """Query video_task + fish from DB and build a payload for pipeline_result_to_response.

    Used as fallback when pipeline_result.json does not exist.
    Returns None if the video_task is not found in DB.
    """
    from app.repositories.models import Fish, VideoTask
    from sqlalchemy import select

    task: VideoTask | None = db.get(VideoTask, video_id)
    if task is None:
        return None

    fish_rows: list[dict[str, Any]] = []
    fish_records = db.execute(
        select(Fish).where(Fish.video_id == video_id)
    ).scalars().all()
    for f in fish_records:
        row = {
            "fish_id": f.fish_id,
            "video_id": f.video_id,
            "track_id": f.track_id,
            "fish_uid": f.fish_uid,
            "frame_index": f.frame_index,
            "status": f.status,
            "error_message": f.error_message or f.error_msg,
            "body_length_cm": f.body_length_cm,
            "body_length_no_tail_cm": f.body_length_no_tail_cm,
            "body_height_cm": f.body_height_cm,
            "pred_weight_g": f.pred_weight_g,
            "condition_factor": f.condition_factor,
            "surface_damage_ratio": f.surface_damage_ratio,
            "crop_image_path": f.crop_image_path,
        }
        fish_rows.append(_fish_row_to_response(row))

    summary = {
        "total_fish": task.total_fish or 0,
        "complete_fish": task.complete_fish or 0,
        "incomplete_fish": task.incomplete_fish or 0,
        "failed_fish": task.failed_fish or 0,
        "damaged_fish": task.damaged_fish or 0,
        "avg_surface_damage_ratio": task.avg_surface_damage_ratio,
    }

    payload: dict[str, Any] = {
        "video_id": video_id,
        "status": task.status,
        "fish": fish_rows,
        "summary": summary,
         "input_video": _path_to_data_relative(task.input_video),
         "original_input_video": _path_to_data_relative(task.original_input_video),
        "video_duration_seconds": task.video_duration_seconds,
        "processing_duration_seconds": task.processing_duration_seconds,
         "grounded_video": _path_to_data_relative(task.grounded_video_path),
         "measurement_csv": _path_to_data_relative(task.measurement_csv_path),
         "pipeline_result_path": _path_to_data_relative(task.pipeline_result_path),
         "result_dir": _path_to_data_relative(task.result_dir),
         "error_conclusion_path": _path_to_data_relative(task.error_conclusion_path),
    }
    return payload


def fish_record_to_response(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    out["display_fish_id"] = item.get("track_id")
    out["crop_image_url"] = algorithm_path_to_static_url(item.get("crop_image_path"))
    out["fish_condition_image_url"] = algorithm_path_to_static_url(item.get("fish_condition_image_path"))
    out["arched_back_image_url"] = algorithm_path_to_static_url(item.get("arched_back_image_path"))
    return out


def pipeline_result_to_response(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    video_id = str(payload.get("video_id") or "").strip()
    result_video_url = ""
    if video_id:
        from app.services.video_playback_service import resolve_result_video_path

        if resolve_result_video_path(video_id) is not None:
            result_video_url = f"/api/algorithm/videos/{video_id}/result-video"
    out["result_video_url"] = result_video_url
    out["grounded_video_url"] = algorithm_path_to_static_url(payload.get("grounded_video"))
    out["measurement_csv_url"] = algorithm_path_to_static_url(payload.get("measurement_csv"))
    out["fish"] = [
        fish_record_to_response(_backfill_fish_image_paths(video_id, x))
        for x in payload.get("fish", [])
        if isinstance(x, dict)
    ]

    # 报表 URL：基于 video_id 默认路径，结合 payload 中已落盘字段做容错。
    video_id = str(payload.get("video_id") or "").strip()
    default_docx = f"data/results/{video_id}/reports/{video_id}_report.docx" if video_id else ""
    default_pdf = f"data/results/{video_id}/reports/{video_id}_report.pdf" if video_id else ""

    report_docx = payload.get("report_docx") or default_docx
    report_pdf = payload.get("report_pdf") or default_pdf
    out["report_docx"] = report_docx if _existing_static_url(report_docx) else ""
    out["report_pdf"] = report_pdf if _existing_static_url(report_pdf) else ""
    out["report_docx_url"] = _existing_static_url(
        out["report_docx"],
    )
    out["report_pdf_url"] = _existing_static_url(
        out["report_pdf"],
    )

    return out


def _existing_static_url(rel_path: str | None) -> str:
    """把 data/results/.../x.docx 转成 /static/results/.../x.docx；文件不存在时返回空串。"""
    if not rel_path:
        return ""
    p = Path(str(rel_path))
    abs_path = p if p.is_absolute() else PROJECT_ROOT / p
    if not abs_path.exists():
        return ""
    url = algorithm_path_to_static_url(str(rel_path))
    return url or ""


__all__ = [
    "load_pipeline_result",
    "load_pipeline_result_from_path",
    "pipeline_result_to_video_task_fields",
    "pipeline_fish_to_db_fields",
    "upsert_video_task_from_payload",
    "upsert_fish_from_pipeline_item",
    "import_pipeline_result_payload_to_db",
    "import_pipeline_result_to_db",
    "import_pipeline_result_file_to_db",
    "fish_record_to_response",
    "pipeline_result_to_response",
    "video_task_to_response_payload",
]
