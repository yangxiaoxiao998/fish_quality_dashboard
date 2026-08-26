from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.paths import DATA_DIR, PROJECT_ROOT
from app.services.algorithm_cli_service import (
    algorithm_path_to_static_url,
    algorithm_path_to_static_url,
    error_conclusion_path,
    pipeline_result_path,
    read_error_conclusion,
    read_pipeline_result,
)

RESULTS_DIR = DATA_DIR / "results"


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
        "original_input_video": _maybe_str(payload.get("original_input_video")),
        "input_video": _path_to_project_relative(payload.get("input_video")),
        "video_duration_seconds": _maybe_float(payload.get("video_duration_seconds")),
        "processing_duration_seconds": _maybe_float(
            payload.get("processing_duration_seconds") or payload.get("processing_seconds")
        ),
        "failed_stage": _maybe_str(payload.get("failed_stage")),
        "error_message": _maybe_str(payload.get("error_message")),
        "result_dir": _path_to_project_relative(result_dir),
        "pipeline_result_path": _path_to_project_relative(pipeline_result_rel),
        "error_conclusion_path": _path_to_project_relative(error_conclusion_rel),
        "grounded_video_path": _path_to_project_relative(payload.get("grounded_video")),
        "measurement_csv_path": _path_to_project_relative(payload.get("measurement_csv")),
        "complete_match_dir": _path_to_project_relative(payload.get("complete_match_dir")),
        "incomplete_match_dir": _path_to_project_relative(payload.get("incomplete_match_dir")),

        # summary
        "total_fish": _maybe_int(summary.get("total_fish")) or 0,
        "complete_fish": _maybe_int(summary.get("complete_fish")) or 0,
        "incomplete_fish": _maybe_int(summary.get("incomplete_fish")) or 0,
        "failed_fish": _maybe_int(summary.get("failed_fish")) or 0,
        "damaged_fish": _maybe_int(summary.get("damaged_fish")) or 0,
        "avg_surface_damage_ratio": _maybe_float(summary.get("avg_surface_damage_ratio")),
        "max_surface_damage_ratio": _maybe_float(summary.get("max_surface_damage_ratio")),

        # 旧模型兼容字段
        "source_type": "algorithm",
        "source_path": _path_to_project_relative(payload.get("input_video")),
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

    crop_image_path = _path_to_project_relative(item.get("crop_image_path"))
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
) -> dict[str, Any]:
    video_id = str(payload.get("video_id") or "").strip()
    if not video_id:
        raise ValueError("pipeline_result payload 缺少 video_id")

    status = _normalize_status(payload.get("status"))

    video_task = upsert_video_task_from_payload(db, payload)

    fish_total = len(payload.get("fish") or [])
    fish_imported = 0
    fish_skipped = 0

    if status in {"done", "complete"}:
        for item in payload.get("fish") or []:
            if not isinstance(item, dict):
                fish_skipped += 1
                continue
            try:
                upsert_fish_from_pipeline_item(db, item)
                fish_imported += 1
            except Exception as exc:
                fish_skipped += 1
                print(
                    "[algorithm_result_service] fish import failed:",
                    f"video_id={video_id!r}",
                    f"item={item!r}",
                    f"error={type(exc).__name__}: {exc}",
                )
    else:
        fish_skipped = fish_total

    if commit:
        db.commit()

    return {
        "ok": True,
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
    return import_pipeline_result_payload_to_db(db, payload, commit=commit)


def import_pipeline_result_file_to_db(
    db: Session,
    path: str | Path,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    payload = load_pipeline_result_from_path(path)
    return import_pipeline_result_payload_to_db(db, payload, commit=commit)


def fish_record_to_response(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    out["display_fish_id"] = item.get("track_id")
    out["crop_image_url"] = algorithm_path_to_static_url(item.get("crop_image_path"))
    return out


def pipeline_result_to_response(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out["grounded_video_url"] = algorithm_path_to_static_url(payload.get("grounded_video"))
    out["measurement_csv_url"] = algorithm_path_to_static_url(payload.get("measurement_csv"))
    out["fish"] = [
        fish_record_to_response(x)
        for x in payload.get("fish", [])
        if isinstance(x, dict)
    ]

    # 报表 URL：基于 video_id 默认路径，结合 payload 中已落盘字段做容错。
    video_id = str(payload.get("video_id") or "").strip()
    default_docx = f"data/results/{video_id}/reports/{video_id}_report.docx" if video_id else ""
    default_pdf = f"data/results/{video_id}/reports/{video_id}_report.pdf" if video_id else ""

    out["report_docx"] = payload.get("report_docx") or default_docx
    out["report_pdf"] = payload.get("report_pdf") or default_pdf
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
]
