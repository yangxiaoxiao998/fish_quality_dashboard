from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.repositories.models import Fish, VideoTask
from app.services.path_service import to_static_url
from app.services.path_service import to_abs_data_path
from app.services.fish_id_service import (
    derive_fish_display_id,
    derive_fish_short_id,
    derive_video_display_id,
)
from app.services.report_format_utils import (
    format_percent,
    has_surface_damage,
    normalize_ratio_percent,
)
from app.services.fish_distribution_service import build_fixed_distribution
from app.utils.time_utils import BEIJING_TZ
from app.utils.timezone import format_china_datetime
from app.paths import DATA_DIR, RESULTS_DIR


# ----------------- 通用工具 -----------------

def _day_range(report_date: date) -> tuple[datetime, datetime]:
    """北京时区当天 00:00:00 ~ 23:59:59.999999"""
    start = datetime.combine(report_date, time.min).replace(tzinfo=BEIJING_TZ)
    end = datetime.combine(report_date, time.max).replace(tzinfo=BEIJING_TZ)
    return start, end


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def build_fish_quality_filters(
    *,
    report_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    video_id: str | None = None,
):
    """根据传入参数返回 Fish 过滤条件。

    日报统计口径改为：视频拍摄/录制时间 (VideoTask.created_at)，
    而不是算法完成时间 (Fish.created_at)，避免 2026-07-06 拍摄、
    2026-07-07 算法完成的视频在 2026-07-06 日报显示 0 的问题。

    通过 JOIN VideoTask 取得 created_at 来过滤 Fish。
    """
    from sqlalchemy import and_

    vt_filters = []

    if report_date:
        d = _parse_date(report_date)
        if d:
            start, end = _day_range(d)
            vt_filters.append(VideoTask.created_at >= start)
            vt_filters.append(VideoTask.created_at <= end)

    if start_date:
        d = _parse_date(start_date)
        if d:
            start, _ = _day_range(d)
            vt_filters.append(VideoTask.created_at >= start)

    if end_date:
        d = _parse_date(end_date)
        if d:
            _, end = _day_range(d)
            vt_filters.append(VideoTask.created_at <= end)

    if video_id:
        vt_filters.append(VideoTask.video_id == video_id)

    # 用 EXISTS 子查询限定 Fish.video_id 在视频范围内
    filters = []
    if vt_filters:
        from sqlalchemy import select as _select, exists as _exists
        vt_subquery = _select(VideoTask.video_id).where(and_(*vt_filters)).subquery()
        filters.append(Fish.video_id.in_(vt_subquery))

    return filters


def _avg(values: list[Any]) -> float | None:
    nums: list[float] = []
    for v in values:
        if v is None:
            continue
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            continue
    return round(sum(nums) / len(nums), 2) if nums else None


def _fish_length_cm(f: Fish) -> float | None:
    return getattr(f, "body_length_cm", None)


def _fish_length_no_tail_cm(f: Fish) -> float | None:
    return getattr(f, "body_length_no_tail_cm", None)


def _fish_height_cm(f: Fish) -> float | None:
    return getattr(f, "body_height_cm", None)


def _fish_weight_g(f: Fish) -> float | None:
    return getattr(f, "pred_weight_g", None)


def _fish_error_message(f: Fish) -> str | None:
    return getattr(f, "error_message", None) or getattr(f, "error_msg", None)


def _fish_crop_url(f: Fish) -> str | None:
    path = getattr(f, "crop_image_path", None)
    if path:
        return to_static_url(path)
    return to_static_url(getattr(f, "original_image_path", None)) or to_static_url(
        getattr(f, "corrected_image_path", None)
    )


def _bucket(values: list[float], buckets: list[tuple[float, float, str]]) -> list[dict]:
    result = []
    for low, high, label in buckets:
        count = len([v for v in values if low <= v < high])
        result.append({"label": label, "count": count})
    return result


_NORMAL_DAMAGE_KEYS = {"无", "正常", "none", "None", "", "无明显破损"}


def _is_abnormal(fish: Fish) -> bool:
    """异常鱼定义为破损占比超过 50%。"""
    if fish is None:
        return False
    pct = normalize_ratio_percent(fish.surface_damage_ratio)
    if pct is not None and pct > 50.0:
        return True
    return False


def _record_is_abnormal(record: dict) -> bool:
    """JSON 算法结果的异常鱼判定，与数据库口径保持一致。"""
    pct = normalize_ratio_percent(record.get("surface_damage_ratio"))
    return pct is not None and pct > 50.0


def _is_partial(fish: Fish) -> bool:
    """部分指标缺失（status != done 且不是 failed）。"""
    return fish.status == "processing"


# ----------------- 鱼品质检测报表 -----------------

def get_fish_quality_summary(
    db: Session,
    *,
    report_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    video_id: str | None = None,
) -> dict:
    """鱼品质检测报表汇总。

    返回字段：
        total_video / total_fish / valid_fish / abnormal_fish
        avg_body_length / avg_body_length_without_tail
        avg_body_height / avg_body_weight / avg_condition_factor
        surface_damage_count
        surface_damage_fish_total / surface_damage_rate
        metric_complete_rate / abnormal_fish_rate
    """
    # 步骤 1：按 video 拍摄/录制时间筛选 video_id（不再是 Fish.created_at）
    from app.repositories.models import VideoTask
    vt_filters = []
    if report_date:
        d = _parse_date(report_date)
        if d:
            start, end = _day_range(d)
            vt_filters.append(VideoTask.created_at >= start)
            vt_filters.append(VideoTask.created_at <= end)
    if start_date:
        d = _parse_date(start_date)
        if d:
            start, _ = _day_range(d)
            vt_filters.append(VideoTask.created_at >= start)
    if end_date:
        d = _parse_date(end_date)
        if d:
            _, end = _day_range(d)
            vt_filters.append(VideoTask.created_at <= end)
    if video_id:
        vt_filters.append(VideoTask.video_id == video_id)
    vt_stmt = select(VideoTask)
    if vt_filters:
        from sqlalchemy import and_
        vt_stmt = vt_stmt.where(and_(*vt_filters))
    video_tasks = list(db.execute(vt_stmt).scalars().all())
    video_ids = sorted({vt.video_id for vt in video_tasks if vt.video_id})

    # 步骤 2：读 DB Fish（按 video_id 过滤）
    fish_filters = []
    if video_ids:
        fish_filters.append(Fish.video_id.in_(video_ids))
    if fish_filters:
        fishes = list(db.execute(select(Fish).where(*fish_filters)).scalars().all())
    else:
        fishes = []

    # 步骤 3：pipeline_result.json 兜底
    cache = _get_or_load_pipeline_results(video_ids) if video_ids else {}

    # 计算汇总
    total_video = len(video_ids)
    total_video_duration = round(
        sum(
            float(v.video_duration_seconds or 0)
            for v in video_tasks
            if v.video_duration_seconds is not None
        ),
        2,
    )
    if total_video_duration <= 0:
        total_video_duration = _query_video_total_duration(db, video_tasks)
    total_fish = len(fishes)
    valid_fish = len([f for f in fishes if (f.status or "").lower() in {"done", "complete"}])
    abnormal_fish = sum(1 for f in fishes if _is_abnormal(f))
    surface_damage_fish_total = sum(1 for f in fishes if has_surface_damage(f.surface_damage_ratio))

    # 优先使用 pipeline_result.json，避免数据库旧字段与最终算法结果重复或不一致。
    all_records: list[dict] = []
    if cache:
        for info in cache.values():
            all_records.extend(info.get("fish_records", []))
    else:
        for f in fishes:
            all_records.append(
                {
                    "body_length_cm": _fish_length_cm(f),
                    "body_length_no_tail_cm": _fish_length_no_tail_cm(f),
                    "body_height_cm": _fish_height_cm(f),
                    "body_weight_g": _fish_weight_g(f),
                    "condition_factor": getattr(f, "condition_factor", None),
                    "surface_damage_ratio": getattr(f, "surface_damage_ratio", None),
                }
            )

    # 如果 DB 完全没有 fish 但 JSON 有，total_fish/complete/incomplete/failed 来自 JSON
    db_fish_count = len(fishes)
    json_fish_count = sum(len(v.get("fish_records", [])) for v in cache.values())
    if json_fish_count > 0:
        total_fish = json_fish_count
        valid_fish = sum(
            1
            for v in cache.values()
            for r in v.get("fish_records", [])
            if (r.get("status") or "").lower() in ("done", "complete")
        )
        incomplete_fish = sum(
            1
            for v in cache.values()
            for r in v.get("fish_records", [])
            if (r.get("status") or "").lower() == "incomplete"
        )
        failed_fish = sum(
            1
            for v in cache.values()
            for r in v.get("fish_records", [])
            if (r.get("status") or "").lower() == "failed"
        )
        surface_damage_fish_total = sum(
            1
            for v in cache.values()
            for r in v.get("fish_records", [])
            if normalize_ratio_percent(r.get("surface_damage_ratio")) not in (None, 0)
        )
        abnormal_fish = sum(
            1 for v in cache.values() for r in v.get("fish_records", [])
            if _record_is_abnormal(r)
        )

    metric_complete_rate = (valid_fish / total_fish) if total_fish else 0.0
    if json_fish_count > 0:
        abnormal_fish = sum(
            1 for v in cache.values() for r in v.get("fish_records", [])
            if _record_is_abnormal(r)
        )
    else:
        abnormal_fish = sum(1 for f in fishes if _is_abnormal(f))
    abnormal_fish_rate = (abnormal_fish / total_fish) if total_fish else 0.0
    surface_damage_rate = (
        surface_damage_fish_total / total_fish if total_fish else 0.0
    )

    # 修复 incomplete_fish / failed_fish 字段（之前没计算）
    if db_fish_count > 0 and json_fish_count == 0:
        incomplete_fish = sum(1 for f in fishes if (f.status or "") == "incomplete")
        failed_fish = sum(1 for f in fishes if (f.status or "") == "failed")
    # 兜底：db_fish_count=0 且 json_fish_count=0 时上面两个分支都不会跑，
    # incomplete_fish / failed_fish 未定义；return 时引用会 UnboundLocalError。
    if "incomplete_fish" not in locals():
        incomplete_fish = 0
    if "failed_fish" not in locals():
        failed_fish = 0
    if json_fish_count == 0:
        abnormal_fish = sum(1 for f in fishes if _is_abnormal(f))

    return {
        "total_video": total_video,
        "total_video_duration": total_video_duration,
        "total_fish": total_fish,
        "valid_fish": valid_fish,
        "abnormal_fish": abnormal_fish,
        "complete_fish": locals().get("valid_fish", 0),  # 兼容：valid_fish == complete_fish
        "incomplete_fish": incomplete_fish,
        "failed_fish": failed_fish,
        "surface_damage_count": surface_damage_fish_total,
        "surface_damage_fish_total": surface_damage_fish_total,
        "surface_damage_rate": surface_damage_rate,
        "metric_complete_rate": metric_complete_rate,
        "abnormal_fish_rate": abnormal_fish_rate,
        "avg_body_length": _avg([r.get("body_length_cm") for r in all_records]) or 0.0,
        "avg_body_length_without_tail": _avg([r.get("body_length_no_tail_cm") for r in all_records]) or 0.0,
        "avg_body_height": _avg([r.get("body_height_cm") for r in all_records]) or 0.0,
        "avg_body_weight": _avg([r.get("body_weight_g") for r in all_records]) or 0.0,
        "avg_condition_factor": _avg([r.get("condition_factor") for r in all_records]) or 0.0,
        "video_ids": video_ids,
        "db_fish_count": db_fish_count,
        "json_fish_count": json_fish_count,
        "used_json_fallback": db_fish_count == 0 and json_fish_count > 0,
    }


def _build_fish_count_trend(
    db: Session,
    *,
    fishes: list,
    report_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    video_id: str | None = None,
) -> list[dict]:
    """鱼数量趋势图数据。

    - 当天模式（report_date 存在）：按小时聚合鱼数量（最多 24 个点）。
    - 日期范围模式：按天聚合鱼数量。
    - 空数据时返回空列表。
    """
    if not fishes:
        return []

    bucket_format = "%Y-%m-%d %H:00"
    bucket_kind = "hour"

    if not report_date and start_date and end_date:
        bucket_format = "%Y-%m-%d"
        bucket_kind = "day"

    bucket_map: dict[str, int] = {}
    for f in fishes:
        if f.created_at is None:
            continue
        # 统一转到北京时间再格式化
        beijing_time = f.created_at.astimezone(BEIJING_TZ)
        key = beijing_time.strftime(bucket_format)
        bucket_map[key] = bucket_map.get(key, 0) + 1

    return [
        {"bucket": k, "bucket_kind": bucket_kind, "count": v}
        for k, v in sorted(bucket_map.items())
    ]


def get_fish_quality_charts(
    db: Session,
    *,
    report_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    video_id: str | None = None,
) -> dict:
    """鱼品质检测图表数据。"""
    filters = build_fish_quality_filters(
        report_date=report_date,
        start_date=start_date,
        end_date=end_date,
    )
    if video_id:
        filters.append(Fish.video_id == video_id)

    fish_stmt = select(Fish)
    if filters:
        fish_stmt = fish_stmt.where(*filters)
    fishes = list(db.execute(fish_stmt).scalars().all())

    # JSON 兜底：从 pipeline_result.json 拉鱼字段，merge 到 all_records
    video_ids_now = sorted({f.video_id for f in fishes if f.video_id})
    json_cache = _get_or_load_pipeline_results(video_ids_now) if video_ids_now else {}
    extra_records: list[dict] = []
    for v_info in json_cache.values():
        for r in v_info.get("fish_records", []):
            extra_records.append(r)
    if extra_records:
        all_records_for_charts = extra_records
    else:
        all_records_for_charts = [
            {
                "body_length_cm": _fish_length_cm(f),
                "body_length_no_tail_cm": _fish_length_no_tail_cm(f),
                "body_height_cm": f.body_height_cm,
                "body_weight_g": f.pred_weight_g,
                "condition_factor": f.condition_factor,
                "surface_damage_ratio": f.surface_damage_ratio,
            }
            for f in fishes
        ]

    length_values = [float(r.get("body_length_cm")) for r in all_records_for_charts if r.get("body_length_cm") is not None]
    no_tail_values = [float(r.get("body_length_no_tail_cm")) for r in all_records_for_charts if r.get("body_length_no_tail_cm") is not None]
    height_values = [float(r.get("body_height_cm")) for r in all_records_for_charts if r.get("body_height_cm") is not None]
    weight_values = [float(r.get("body_weight_g")) for r in all_records_for_charts if r.get("body_weight_g") is not None]
    condition_values = [float(r.get("condition_factor")) for r in all_records_for_charts if r.get("condition_factor") is not None]

    damage_pcts: list[float] = []
    for f in fishes:
        pct = normalize_ratio_percent(f.surface_damage_ratio)
        if pct is not None:
            damage_pcts.append(pct)
    for r in extra_records:
        pct = normalize_ratio_percent(r.get("surface_damage_ratio"))
        if pct is not None:
            damage_pcts.append(pct)

    video_rank: dict[str, int] = {}
    for f in fishes:
        video_rank[f.video_id] = video_rank.get(f.video_id, 0) + 1

    fish_count_trend = _build_fish_count_trend(
        db,
        fishes=fishes,
        report_date=report_date,
        start_date=start_date,
        end_date=end_date,
        video_id=video_id,
    )

    return {
        "fish_count_trend": fish_count_trend,
        "body_length_distribution": build_fixed_distribution(length_values, "body_length_cm"),
        "body_length_without_tail_distribution": build_fixed_distribution(height_values, "body_height_cm"),
        "body_height_distribution": build_fixed_distribution(height_values, "body_height_cm"),
        "body_weight_distribution": build_fixed_distribution(weight_values, "body_weight_g"),
        "condition_factor_distribution": build_fixed_distribution(condition_values, "condition_factor"),
        # 第五张图：体表损伤占比分布（按百分比档）
        "surface_damage_distribution": build_fixed_distribution(damage_pcts, "surface_damage_percent"),
        "video_fish_rank": [
            {"video_id": k, "count": v}
            for k, v in sorted(video_rank.items(), key=lambda x: x[1], reverse=True)[:20]
        ],
    }


def get_fish_quality_fishes(
    db: Session,
    *,
    report_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    video_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """鱼品质检测 - 鱼明细表。"""
    filters = build_fish_quality_filters(
        report_date=report_date,
        start_date=start_date,
        end_date=end_date,
    )
    if video_id:
        filters.append(Fish.video_id == video_id)

    count_stmt = select(func.count()).select_from(Fish)
    stmt = select(Fish)
    if filters:
        count_stmt = count_stmt.where(*filters)
        stmt = stmt.where(*filters)

    total = int(db.execute(count_stmt).scalar() or 0)
    fishes = list(
        db.execute(
            stmt.order_by(Fish.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars().all()
    )

    json_by_fish_id: dict[str, dict] = {}
    if video_id:
        payload = _read_pipeline_result_json(video_id) or {}
        for record in payload.get("fish") or payload.get("fishes") or []:
            if isinstance(record, dict):
                key = record.get("fish_uid") or record.get("fish_id")
                if key:
                    json_by_fish_id[str(key)] = record

    items = []
    for f in fishes:
        record = json_by_fish_id.get(str(f.fish_id), {})
        crop_path = record.get("crop_image_path") or f.crop_image_path
        items.append({
            "fish_id": f.fish_id,
            "fish_display_id": derive_fish_display_id(f.fish_id),
            "fish_short_id": derive_fish_short_id(f.fish_id),
            "video_id": f.video_id,
            "video_display_id": derive_video_display_id(f.video_id),
            "created_at": f.created_at.isoformat() if f.created_at else None,
            "body_length": record.get("body_length_cm") if record else f.body_length_cm,
            "body_length_without_tail": record.get("body_length_no_tail_cm") if record else f.body_length_no_tail_cm,
            "body_height": record.get("body_height_cm") if record else f.body_height_cm,
            "body_weight": record.get("pred_weight_g") if record else f.pred_weight_g,
            "condition_factor": record.get("condition_factor") if record else f.condition_factor,
            "surface_damage": f.surface_damage,
            "surface_damage_ratio": record.get("surface_damage_ratio") if record else f.surface_damage_ratio,
            "hex_color": record.get("hex_color") or getattr(f, "hex_color", None),
            "status": record.get("status") or f.status,
            "track_id": record.get("track_id") if record else f.track_id,
            "fish_uid": record.get("fish_uid") or f.fish_uid,
            "frame_index": record.get("frame_index") if record else f.frame_index,
            "crop_image_path": crop_path,
            "crop_image_url": to_static_url(crop_path),
            "body_length_cm": record.get("body_length_cm") if record else f.body_length_cm,
            "body_length_no_tail_cm": record.get("body_length_no_tail_cm") if record else f.body_length_no_tail_cm,
            "body_height_cm": record.get("body_height_cm") if record else f.body_height_cm,
            "pred_weight_g": record.get("pred_weight_g") if record else f.pred_weight_g,
            "original_image_url": to_static_url(f.original_image_path),
            "corrected_image_url": to_static_url(f.corrected_image_path),
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_videos_by_date(db: Session, *, report_date: str) -> list[dict]:
    """按日期返回视频列表。"""
    d = _parse_date(report_date)
    if d is None:
        return []
    start, end = _day_range(d)

    stmt = (
        select(VideoTask)
        .where(VideoTask.created_at >= start)
        .where(VideoTask.created_at <= end)
        .order_by(VideoTask.created_at.desc())
    )
    rows = list(db.execute(stmt).scalars().all())

    items: list[dict] = []
    for v in rows:
        pipeline = _read_pipeline_result_json(v.video_id) or {}
        video_duration = v.video_duration_seconds
        if video_duration is None or float(video_duration) <= 0:
            try:
                video_duration = float(pipeline.get("video_duration_seconds"))
            except (TypeError, ValueError):
                video_duration = None
        if video_duration is None or float(video_duration) <= 0:
            source = pipeline.get("original_input_video") or pipeline.get("input_video")
            path = to_abs_data_path(source) if source else None
            if path and path.exists():
                try:
                    probe = subprocess.run(
                        [
                            "ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
                        ], capture_output=True, text=True, timeout=20, check=False,
                    )
                    video_duration = float(probe.stdout.strip())
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    video_duration = None
        processing_duration = v.processing_duration_seconds
        if processing_duration is None:
            try:
                processing_duration = float(
                    pipeline.get("processing_duration_seconds")
                    or pipeline.get("processing_seconds")
                )
            except (TypeError, ValueError):
                processing_duration = None
        items.append(
            {
                "video_id": v.video_id,
                "video_display_id": derive_video_display_id(v.video_id),
                "source_type": v.source_type,
                "status": v.status,
                "total_fish": v.total_fish,
                "video_duration_seconds": video_duration,
                "processing_duration_seconds": processing_duration,
                "avg_body_length": _avg([
                    r.get("body_length_cm")
                    for r in (pipeline.get("fish") or [])
                    if isinstance(r, dict)
                    and str(r.get("status") or "").lower() in {"done", "complete"}
                ]),
                "avg_body_height": _avg([
                    r.get("body_height_cm") for r in (pipeline.get("fish") or [])
                    if isinstance(r, dict)
                    and str(r.get("status") or "").lower() in {"done", "complete"}
                ]),
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
        )
    return items


# ----------------- pipeline_result.json 兜底读取 -----------------

def _read_pipeline_result_json(video_id: str) -> dict | None:
    """读取 data/results/<video_id>/reports/pipeline_result.json。"""
    path = RESULTS_DIR / video_id / "reports" / "pipeline_result.json"
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fp:
            data = json.loads(fp.read())
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def _get_or_load_pipeline_results(video_ids: list[str]) -> dict[str, dict]:
    """对每个 video_id 优先读 DB VideoTask 总数字段；
    若 0 / 缺，则读 pipeline_result.json 兜底。

    返回 mapping: video_id -> {
        "summary": {total_fish, complete_fish, incomplete_fish, failed_fish, damaged_fish,
                   avg_surface_damage_ratio, max_surface_damage_ratio},
        "fish_records": list[dict]    # 鱼字段统一映射
    }
    """
    out: dict[str, dict] = {}
    if not video_ids:
        return out
    try:
        from app.repositories.db import SessionLocal
        from app.repositories.models import VideoTask
        db = SessionLocal()
        try:
            vts = (
                db.query(VideoTask)
                .filter(VideoTask.video_id.in_(video_ids))
                .all()
            )
            vt_map = {vt.video_id: vt for vt in vts}
        finally:
            db.close()
    except Exception:
        vt_map = {}

    for vid in video_ids:
        vt = vt_map.get(vid)
        # 来自 DB VideoTask 的统计（如果>0）
        summary = {
            "total_fish": int(getattr(vt, "total_fish", 0) or 0) if vt else 0,
            "complete_fish": int(getattr(vt, "complete_fish", 0) or 0) if vt else 0,
            "incomplete_fish": int(getattr(vt, "incomplete_fish", 0) or 0) if vt else 0,
            "failed_fish": int(getattr(vt, "failed_fish", 0) or 0) if vt else 0,
            "damaged_fish": int(getattr(vt, "damaged_fish", 0) or 0) if vt else 0,
            "avg_surface_damage_ratio": (
                float(getattr(vt, "avg_surface_damage_ratio", 0) or 0.0) if vt else 0.0
            ),
            "max_surface_damage_ratio": (
                float(getattr(vt, "max_surface_damage_ratio", 0) or 0.0) if vt else 0.0
            ),
        }

        # 读取 pipeline_result.json
        pr = _read_pipeline_result_json(vid)
        fish_records: list[dict] = []
        used_json = False
        if pr:
            used_json = True
            pr_summary = pr.get("summary") or {}
            if isinstance(pr_summary, dict):
                for k in (
                    "total_fish",
                    "complete_fish",
                    "incomplete_fish",
                    "failed_fish",
                    "damaged_fish",
                    "avg_surface_damage_ratio",
                    "max_surface_damage_ratio",
                ):
                    if k in pr_summary and pr_summary[k] is not None:
                        summary[k] = pr_summary[k]
            raw_fish = pr.get("fish") or pr.get("fishes") or []
            if isinstance(raw_fish, list):
                for r in raw_fish:
                    if not isinstance(r, dict):
                        continue
                    # 算法结果统一使用当前字段口径。
                    rec = {
                        "body_length_cm": r.get("body_length_cm"),
                        "body_length_no_tail_cm": r.get("body_length_no_tail_cm"),
                        "body_height_cm": r.get("body_height_cm"),
                        "pred_weight_g": r.get("pred_weight_g"),
                        "body_weight_g": r.get("pred_weight_g"),
                        "condition_factor": r.get("condition_factor"),
                        "surface_damage_ratio": r.get("surface_damage_ratio"),
                        "status": r.get("status"),
                        "video_id": r.get("video_id") or vid,
                        "fish_id": r.get("fish_id") or r.get("fish_uid"),
                        "track_id": r.get("track_id"),
                    }
                    fish_records.append(rec)

        # 如果 DB 全 0 而 JSON 有，采纳 JSON 的 summary
        if (summary["total_fish"] == 0
            and summary["complete_fish"] == 0
            and summary["incomplete_fish"] == 0
            and summary["failed_fish"] == 0
            and fish_records
        ):
            try:
                summary["total_fish"] = len(fish_records)
            except Exception:
                pass
            try:
                summary["complete_fish"] = sum(
                    1 for r in fish_records
                    if (r.get("status") or "").lower() in ("done", "complete")
                )
            except Exception:
                pass
            try:
                summary["incomplete_fish"] = sum(
                    1 for r in fish_records
                    if (r.get("status") or "").lower() == "incomplete"
                )
            except Exception:
                pass
            try:
                summary["failed_fish"] = sum(
                    1 for r in fish_records
                    if (r.get("status") or "").lower() == "failed"
                )
            except Exception:
                pass
            try:
                damaged = sum(
                    1 for r in fish_records
                    if (r.get("surface_damage_ratio") or 0) > 0
                )
                summary["damaged_fish"] = damaged
            except Exception:
                pass

        out[vid] = {
            "summary": summary,
            "fish_records": fish_records,
            "used_json": used_json,
        }
    return out


# ----------------- 兼容早期 dashboard 调用 -----------------

def build_daily_fish_quality_summary(report_date: date) -> dict:
    return {
        "report_date": report_date.isoformat(),
        "total_fish": 0,
        "abnormal_fish": 0,
    }


# ----------------- 鱼品质日报 - 模板上下文构建 -----------------

# 旧版接口已存在；此处加 ALL_PLACEHOLDERS 供新 docx 模板使用。
ALL_REPORT_PLACEHOLDERS: list[str] = [
    # 基础
    "report_date_cn", "report_no", "generated_at", "system_name",
    "report_type_name", "report_status_name",
    "aquaculture_bin",
    # 视频任务概况
    "video_total", "upload_video_total", "rtsp_video_total",
    "done_video_total", "failed_video_total", "no_fish_video_total",
    "total_video_duration", "avg_processing_seconds", "avg_fps",
    "task_success_rate",
    # 鱼品质检测总览
    "fish_total", "valid_fish_total", "partial_fish_total",
    "abnormal_fish_total", "surface_damage_fish_total",
    "metric_complete_rate", "abnormal_fish_rate", "surface_damage_rate",
    # 核心指标 (avg/min/max)
    "avg_body_length", "min_body_length", "max_body_length",
    "avg_body_length_without_tail", "min_body_length_without_tail",
    "max_body_length_without_tail",
    "avg_body_height", "min_body_height", "max_body_height",
    "avg_body_weight", "min_body_weight", "max_body_weight",
    "avg_condition_factor", "min_condition_factor", "max_condition_factor",
    # 图表
    "chart_body_length_distribution",
    "chart_body_length_without_tail_distribution",
    "chart_body_weight_distribution",
    "chart_condition_factor_distribution",
    "chart_surface_damage_distribution",
    "chart_large_yellow_croaker_body",
    # 视频任务明细 1~5
    *[f"video_index_{i}" for i in range(1, 6)],
    *[f"video_id_{i}" for i in range(1, 6)],
    *[f"aquaculture_bin_{i}" for i in range(1, 6)],
    *[f"video_status_{i}" for i in range(1, 6)],
    *[f"video_total_fish_{i}" for i in range(1, 6)],
    *[f"video_processed_fish_{i}" for i in range(1, 6)],
    *[f"video_created_at_{i}" for i in range(1, 6)],
    *[f"video_finished_at_{i}" for i in range(1, 6)],
    # 异常鱼明细 1~4
    *[f"abnormal_index_{i}" for i in range(1, 5)],
    *[f"abnormal_fish_no_{i}" for i in range(1, 5)],
    *[f"abnormal_video_id_{i}" for i in range(1, 5)],
    *[f"abnormal_body_length_{i}" for i in range(1, 5)],
    *[f"abnormal_body_length_without_tail_{i}" for i in range(1, 5)],
    *[f"abnormal_body_height_{i}" for i in range(1, 5)],
    *[f"abnormal_body_weight_{i}" for i in range(1, 5)],
    *[f"abnormal_condition_factor_{i}" for i in range(1, 5)],
    *[f"abnormal_surface_damage_{i}" for i in range(1, 5)],
    *[f"abnormal_reason_{i}" for i in range(1, 5)],
    # 结论 + 归档
    "daily_conclusion", "local_save_status", "usb_backup_status",
    "report_file_name", "report_file_path",
]


def ensure_all_placeholders(ctx: dict) -> dict:
    """任何缺失的占位符补 '--'，避免 {{xxx}} 残留。"""
    for key in ALL_REPORT_PLACEHOLDERS:
        ctx.setdefault(key, "--")
    return ctx


def _min(values: list[Any]) -> float | None:
    nums: list[float] = []
    for v in values:
        if v is None:
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        nums.append(x)
    return round(min(nums), 2) if nums else None


def _max(values: list[Any]) -> float | None:
    nums: list[float] = []
    for v in values:
        if v is None:
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        nums.append(x)
    return round(max(nums), 2) if nums else None


def _fmt_or_dash(value: Any, digits: int = 2) -> str:
    """空值 (None) → '--'，否则保留 digits 位小数。"""
    if value is None:
        return "--"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "--"


def _compute_video_total_duration_seconds(videos: list) -> float:
    """从 video_frame.frame_time_ms 估算视频时长。"""
    if not videos:
        return 0.0
    # 取当天的所有 frame_time_ms 的最大值 / 1000
    # 简化：取最新一条 video 的最大 frame_time
    from app.repositories.models import VideoFrame

    video_ids = [v.video_id for v in videos if v.video_id]
    if not video_ids:
        return 0.0
    # 这里仅取第一条视频做估算（按"日报"语义，单日通常 1 段录像）
    # 真要算所有 sum 也行，但模板只要"总时长"
    # 用最简实现：取所有视频最大 frame_time
    # 不走 DB 二次查询时——通过 ORM 一次 join
    return 0.0  # 实际在 build_daily_report_context 里查 VideoFrame


def _query_video_total_duration(db: Session, videos: list) -> float:
    """计算当天视频文件时长总和，优先使用视频文件元数据。"""
    if not videos:
        return 0.0
    total = 0.0
    for video in videos:
        source = to_abs_data_path(getattr(video, "source_path", None))
        if source is None or not source.exists():
            continue
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", str(source),
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            value = float(result.stdout.strip())
            if value > 0:
                total += value
        except (OSError, ValueError, subprocess.TimeoutExpired):
            continue
    return round(total, 2)


def _query_avg_processing_seconds(videos: list) -> float:
    """平均处理时长，过滤旧数据中的结束时间早于开始时间记录。"""
    pairs: list[float] = []
    for v in videos:
        end = v.finished_at or v.updated_at
        if end is None:
            continue
        for start in (v.started_at, v.created_at):
            if start is None:
                continue
            delta = (end - start).total_seconds()
            if delta > 0:
                pairs.append(delta)
                break
    return round(sum(pairs) / len(pairs), 2) if pairs else 0.0


def _query_avg_fps(videos: list) -> float:
    """读取任务 FPS；旧任务缺字段时从源视频流元数据补算。"""
    fps_list = [float(v.fps_estimate) for v in videos if v.fps_estimate is not None]
    for video in videos:
        if video.fps_estimate is not None:
            continue
        source = to_abs_data_path(getattr(video, "source_path", None))
        if source is None or not source.exists():
            continue
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=r_frame_rate",
                    "-of", "default=noprint_wrappers=1:nokey=1", str(source),
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            value = result.stdout.strip()
            if "/" in value:
                numerator, denominator = value.split("/", 1)
                fps = float(numerator) / float(denominator)
            else:
                fps = float(value)
            if fps > 0:
                fps_list.append(fps)
        except (OSError, ValueError, ZeroDivisionError, subprocess.TimeoutExpired):
            continue
    return round(sum(fps_list) / len(fps_list), 2) if fps_list else 0.0


def _query_partial_fish_count(fishes: list) -> int:
    return sum(1 for f in fishes if f.status == "processing")


def _query_top_videos(db: Session, fishes: list, videos: list) -> list:
    """前 5 个视频任务（按时间倒序）。"""
    return sorted(videos, key=lambda v: v.created_at or _ZERO_DT, reverse=True)[:5]


_ZERO_DT = datetime(1970, 1, 1, tzinfo=BEIJING_TZ)


def _query_top_abnormal_fishes(fishes: list) -> list:
    """前 4 条异常鱼（按 created_at 倒序）。"""
    abn = [f for f in fishes if _is_abnormal(f)]
    abn.sort(key=lambda f: f.created_at or _ZERO_DT, reverse=True)
    return abn[:4]


def build_daily_report_context(
    db: Session,
    *,
    report_date: date,
    file_name: str | None = None,
    file_path: str | None = None,
    existing_summary: dict | None = None,
) -> dict:
    """为新 docx 模板构建完整 context。

    字段对应 detection_daily_report_template.docx 的 93+ 个占位符。
    """
    summary = existing_summary or get_fish_quality_summary(
        db, report_date=report_date.isoformat()
    )
    # 补齐可能的字段
    summary.setdefault("surface_damage_fish_total", 0)
    summary.setdefault("surface_damage_rate", 0.0)
    summary.setdefault("metric_complete_rate", 0.0)
    summary.setdefault("abnormal_fish_rate", 0.0)
    summary.setdefault("upload_video_total", 0)
    summary.setdefault("rtsp_video_total", 0)
    summary.setdefault("done_video_total", 0)
    summary.setdefault("failed_video_total", 0)
    summary.setdefault("no_fish_video_total", 0)
    summary.setdefault("total_video_duration", 0.0)
    summary.setdefault("avg_processing_seconds", 0.0)
    summary.setdefault("avg_fps", 0.0)
    summary.setdefault("task_success_rate", 0.0)
    summary.setdefault("valid_fish_total", summary.get("valid_fish", 0))
    summary.setdefault("partial_fish_total", 0)
    summary.setdefault("abnormal_fish_total", summary.get("abnormal_fish", 0))

    start_dt, end_dt = _day_range(report_date)

    # 当天视频
    video_rows = list(
        db.execute(
            select(VideoTask)
            .where(VideoTask.created_at >= start_dt)
            .where(VideoTask.created_at <= end_dt)
            .order_by(VideoTask.created_at.desc())
        ).scalars().all()
    )

    upload_videos = [v for v in video_rows if (v.source_type or "upload") == "upload"]
    rtsp_videos = [v for v in video_rows if (v.source_type or "upload") == "rtsp"]
    done_videos = [v for v in video_rows if v.status == "done"]
    failed_videos = [v for v in video_rows if v.status == "failed"]
    no_fish_videos = [v for v in video_rows if (v.total_fish or 0) == 0]

    summary["upload_video_total"] = len(upload_videos)
    summary["rtsp_video_total"] = len(rtsp_videos)
    summary["done_video_total"] = len(done_videos)
    summary["failed_video_total"] = len(failed_videos)
    summary["no_fish_video_total"] = len(no_fish_videos)
    summary["total_video_duration"] = round(
        sum(
            float(v.video_duration_seconds or 0)
            for v in video_rows
            if v.video_duration_seconds is not None
        ),
        2,
    )
    if summary["total_video_duration"] <= 0:
        summary["total_video_duration"] = _query_video_total_duration(db, video_rows)
    summary["avg_processing_seconds"] = round(
        sum(
            float(v.processing_duration_seconds)
            for v in done_videos + failed_videos
            if v.processing_duration_seconds is not None
        )
        / max(
            1,
            sum(
                1
                for v in done_videos + failed_videos
                if v.processing_duration_seconds is not None
            ),
        ),
        2,
    ) if any(v.processing_duration_seconds is not None for v in done_videos + failed_videos) else _query_avg_processing_seconds(done_videos + failed_videos)
    summary["avg_fps"] = _query_avg_fps(video_rows)
    summary["video_total"] = len(video_rows)
    summary["task_success_rate"] = (
        len(done_videos) / len(video_rows) if video_rows else 0.0
    )

    # 当天鱼
    fish_rows = list(
        db.execute(
            select(Fish)
            .where(Fish.created_at >= start_dt)
            .where(Fish.created_at <= end_dt)
            .order_by(Fish.created_at.desc())
        ).scalars().all()
    )
    report_video_ids = [v.video_id for v in video_rows if v.video_id]
    report_cache = _get_or_load_pipeline_results(report_video_ids)
    records_by_id = {
        str(record.get("fish_id")): record
        for data in report_cache.values()
        for record in data.get("fish_records", [])
        if record.get("fish_id")
    }
    records_by_track = {
        (str(record.get("video_id")), str(record.get("track_id"))): record
        for data in report_cache.values()
        for record in data.get("fish_records", [])
        if record.get("track_id") is not None
    }
    for fish in fish_rows:
        record = records_by_id.get(str(fish.fish_id)) or records_by_track.get(
            (str(fish.video_id), str(fish.track_id))
        )
        if record and record.get("surface_damage_ratio") is not None:
            fish.surface_damage_ratio = record["surface_damage_ratio"]
    total_fish = len(fish_rows)
    valid_fish = [f for f in fish_rows if str(f.status or "").lower() in {"done", "complete"}]
    partial_fish = [f for f in fish_rows if str(f.status or "").lower() == "incomplete"]
    abnormal_fish_list = [f for f in fish_rows if _is_abnormal(f)]
    surface_damage_fish = [f for f in fish_rows if has_surface_damage(f.surface_damage_ratio)]

    summary["total_fish"] = total_fish
    summary["fish_total"] = total_fish
    summary["valid_fish_total"] = len(valid_fish)
    summary["partial_fish_total"] = len(partial_fish)
    summary["abnormal_fish_total"] = len(abnormal_fish_list)
    summary["surface_damage_fish_total"] = len(surface_damage_fish)
    summary["metric_complete_rate"] = (
        len(valid_fish) / total_fish if total_fish else 0.0
    )
    summary["abnormal_fish_rate"] = (
        len(abnormal_fish_list) / total_fish if total_fish else 0.0
    )
    summary["surface_damage_rate"] = (
        len(surface_damage_fish) / total_fish if total_fish else 0.0
    )

    # 核心指标
    report_records = [
        record
        for video_data in report_cache.values()
        for record in video_data.get("fish_records", [])
    ]
    if report_records:
        record_statuses = [str(r.get("status") or "").lower() for r in report_records]
        total_fish = len(report_records)
        valid_count = sum(s in {"done", "complete"} for s in record_statuses)
        partial_count = sum(s == "processing" for s in record_statuses)
        abnormal_count = sum(_record_is_abnormal(r) for r in report_records)
        damaged_count = sum(
            normalize_ratio_percent(r.get("surface_damage_ratio")) not in (None, 0)
            for r in report_records
        )
        summary["total_fish"] = total_fish
        summary["fish_total"] = total_fish
        summary["valid_fish_total"] = valid_count
        summary["partial_fish_total"] = sum(s == "incomplete" for s in record_statuses)
        summary["abnormal_fish_total"] = abnormal_count
        summary["surface_damage_fish_total"] = damaged_count
        summary["metric_complete_rate"] = valid_count / total_fish if total_fish else 0.0
        summary["abnormal_fish_rate"] = abnormal_count / total_fish if total_fish else 0.0
        summary["surface_damage_rate"] = damaged_count / total_fish if total_fish else 0.0
        valid_fish = [f for f in valid_fish if f.status in {"done", "complete"}]
    metric_records = [r for r in report_records if str(r.get("status") or "").lower() in {"done", "complete"}] or [
        {
            "body_length_cm": f.body_length_cm,
            "body_length_no_tail_cm": f.body_length_no_tail_cm,
            "body_height_cm": f.body_height_cm,
            "pred_weight_g": f.pred_weight_g,
            "condition_factor": f.condition_factor,
        }
        for f in valid_fish
    ]
    body_length = [float(r["body_length_cm"]) for r in metric_records if r.get("body_length_cm") is not None]
    body_length_no_tail = [float(r["body_length_no_tail_cm"]) for r in metric_records if r.get("body_length_no_tail_cm") is not None]
    body_height = [float(r["body_height_cm"]) for r in metric_records if r.get("body_height_cm") is not None]
    body_weight = [float(r["pred_weight_g"]) for r in metric_records if r.get("pred_weight_g") is not None]
    condition_factor = [float(r["condition_factor"]) for r in metric_records if r.get("condition_factor") is not None]

    metric_block = {
        "body_length": body_length,
        "body_length_without_tail": body_length_no_tail,
        "body_height": body_height,
        "body_weight": body_weight,
        "condition_factor": condition_factor,
    }

    # 报告编号
    existing_count = int(
        db.execute(
            select(func.count())
            .select_from(__import__("app.repositories.models", fromlist=["DetectionReport"]).DetectionReport)
        .where(__import__("app.repositories.models", fromlist=["DetectionReport"]).DetectionReport.report_date == report_date)
        .where(__import__("app.repositories.models", fromlist=["DetectionReport"]).DetectionReport.report_type == "daily_fish_quality")
        ).scalar()
        or 0
    )
    report_no = f"R{report_date.strftime('%Y%m%d')}{existing_count + 1:04d}"

    # 综合结论（简单版本：基于数据自动生成）
    daily_conclusion = _build_daily_conclusion(summary, metric_block)

    # local_save_status：先填"已保存"（生成后回填真实路径）
    # usb_backup_status：填"未检测"（运维侧决定）
    local_save_status = "已保存"
    usb_backup_status = "未检测"

    ctx: dict[str, Any] = {
        # 基础
        "report_date_cn": report_date.strftime("%Y年%m月%d日"),
        "report_no": report_no,
        "generated_at": format_china_datetime(),
        "system_name": "鱼质检测分析系统",
        "report_type_name": "鱼品质检测日报",
        "report_status_name": "已生成",

        # 视频任务概况
        "video_total": summary["video_total"],
        "upload_video_total": summary["upload_video_total"],
        "rtsp_video_total": summary["rtsp_video_total"],
        "done_video_total": summary["done_video_total"],
        "failed_video_total": summary["failed_video_total"],
        "no_fish_video_total": summary["no_fish_video_total"],
        "total_video_duration": format_duration_with_unit(summary["total_video_duration"]),
        "avg_processing_seconds": f"{summary['avg_processing_seconds']:.2f}",
        "avg_fps": f"{summary['avg_fps']:.2f}",
        "task_success_rate": format_percent(
            summary["task_success_rate"] * 100, digits=2
        ) if summary["task_success_rate"] <= 1.0 else format_percent(summary["task_success_rate"] / 100, digits=2),

        # 鱼品质检测总览
        "fish_total": summary["fish_total"],
        "valid_fish_total": summary["valid_fish_total"],
        "partial_fish_total": summary["partial_fish_total"],
        "abnormal_fish_total": summary["abnormal_fish_total"],
        "surface_damage_fish_total": summary["surface_damage_fish_total"],
        "metric_complete_rate": format_percent(summary["metric_complete_rate"]),
        "abnormal_fish_rate": format_percent(summary["abnormal_fish_rate"]),
        "surface_damage_rate": format_percent(summary["surface_damage_rate"]),

        # 核心指标
        "avg_body_length": _fmt_or_dash(_avg(body_length), 2),
        "min_body_length": _fmt_or_dash(_min(body_length), 2),
        "max_body_length": _fmt_or_dash(_max(body_length), 2),
        "avg_body_length_without_tail": _fmt_or_dash(_avg(body_length_no_tail), 2),
        "min_body_length_without_tail": _fmt_or_dash(_min(body_length_no_tail), 2),
        "max_body_length_without_tail": _fmt_or_dash(_max(body_length_no_tail), 2),
        "avg_body_height": _fmt_or_dash(_avg(body_height), 2),
        "min_body_height": _fmt_or_dash(_min(body_height), 2),
        "max_body_height": _fmt_or_dash(_max(body_height), 2),
        "avg_body_weight": _fmt_or_dash(_avg(body_weight), 2),
        "min_body_weight": _fmt_or_dash(_min(body_weight), 2),
        "max_body_weight": _fmt_or_dash(_max(body_weight), 2),
        "avg_condition_factor": _fmt_or_dash(_avg(condition_factor), 3),
        "min_condition_factor": _fmt_or_dash(_min(condition_factor), 3),
        "max_condition_factor": _fmt_or_dash(_max(condition_factor), 3),

        # 图表占位符先填标记串，render_docx 时再替换为图片
        "chart_body_length_distribution": "{{chart_body_length_distribution}}",
        "chart_body_length_without_tail_distribution": "{{chart_body_length_without_tail_distribution}}",
        "chart_body_weight_distribution": "{{chart_body_weight_distribution}}",
        "chart_condition_factor_distribution": "{{chart_condition_factor_distribution}}",
        "chart_surface_damage_distribution": "{{chart_surface_damage_distribution}}",
        "chart_large_yellow_croaker_body": "{{chart_large_yellow_croaker_body}}",

        # 视频任务明细 1~5
        **_build_video_rows_context(_query_top_videos(db, fish_rows, video_rows)),

        # 异常鱼明细 1~4
        **_build_abnormal_rows_context(_query_top_abnormal_fishes(fish_rows)),

        # 结论 / 归档
        "daily_conclusion": daily_conclusion,
        "local_save_status": local_save_status,
        "usb_backup_status": usb_backup_status,
        "report_file_name": file_name or "--",
        "report_file_path": file_path or "--",
    }

    return ensure_all_placeholders(ctx)


def format_duration_with_unit(seconds: float | int | None) -> str:
    value = float(seconds or 0)
    if value < 60:
        return f"{value:.2f}秒"
    total = round(value)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes}分{secs}秒"
    return f"{minutes}分{secs}秒"


def _build_daily_conclusion(summary: dict, metric_block: dict) -> str:
    """生成包含任务、指标质量和高破损风险的业务结论。"""
    fish_total = summary.get("fish_total", 0)
    if not fish_total:
        return "当日无检测数据，无可生成结论。"

    abnormal_rate = summary.get("abnormal_fish_rate", 0.0)
    damage_rate = summary.get("surface_damage_rate", 0.0)
    valid_rate = summary.get("metric_complete_rate", 0.0)
    video_total = summary.get("video_total", 0)
    done_video_total = summary.get("done_video_total", 0)
    duration = float(summary.get("total_video_duration", 0.0) or 0.0)
    processing = float(summary.get("avg_processing_seconds", 0.0) or 0.0)

    def format_duration(seconds: float) -> str:
        hours, remainder = divmod(max(0, round(seconds)), 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}小时{minutes}分钟"
        if minutes:
            return f"{minutes}分钟{secs}秒"
        return f"{secs}秒"

    avg_height = _avg(metric_block.get("body_height") or [])
    avg_weight = _avg(metric_block.get("body_weight") or [])
    avg_condition = _avg(metric_block.get("condition_factor") or [])
    body_length = metric_block.get("body_length") or []
    avg_length = sum(body_length) / len(body_length) if body_length else 0.0

    parts: list[str] = []
    parts.append(
        f"本日报覆盖 {video_total} 个视频任务，完成 {done_video_total} 个，"
        f"累计视频时长 {format_duration(duration)}，平均处理耗时 {format_duration(processing)}。"
    )
    parts.append(
        f"共检测鱼体 {fish_total} 条，测量指标完整率 {valid_rate * 100:.2f}%，"
        f"平均无尾体长 {avg_length:.2f} cm，平均体高 {_fmt_or_dash(avg_height, 2)} cm，"
        f"平均体重 {_fmt_or_dash(avg_weight, 2)} g，平均肥满度 {_fmt_or_dash(avg_condition, 3)}。"
    )
    if abnormal_rate > 0:
        parts.append(
            f"破损占比超过 50% 的异常鱼占比 {abnormal_rate * 100:.2f}%，"
            f"存在高破损风险，应优先复核异常鱼明细。"
        )
    else:
        parts.append(
            f"未检出破损占比超过 50% 的异常鱼；所有鱼体中出现体表破损的比例为 {damage_rate * 100:.2f}%。"
        )

    return " ".join(parts)


def _build_video_rows_context(top_videos: list) -> dict:
    """为模板 video_index_1..5 / video_id_1..5 / ... 生成 5 行。

    不足 5 条的位置填 '--'。
    """
    out: dict[str, Any] = {}
    for i in range(1, 6):
        out[f"video_index_{i}"] = "--"
        out[f"video_id_{i}"] = "--"
        out[f"aquaculture_bin_{i}"] = "--"
        out[f"video_status_{i}"] = "--"
        out[f"video_total_fish_{i}"] = "--"
        out[f"video_processed_fish_{i}"] = "--"
        out[f"video_created_at_{i}"] = "--"
        out[f"video_finished_at_{i}"] = "--"

    for idx, v in enumerate(top_videos[:5], start=1):
        out[f"video_index_{idx}"] = str(idx)
        out[f"video_id_{idx}"] = v.video_id or "--"
        from app.services.report_format_utils import video_source_cn, video_status_cn
        from app.services.fish_id_service import derive_video_display_id
        out[f"aquaculture_bin_{idx}"] = getattr(v, "aquaculture_bin", None) or "未填写"
        out[f"video_status_{idx}"] = video_status_cn(v.status)
        out[f"video_total_fish_{idx}"] = str(v.total_fish or 0)
        out[f"video_processed_fish_{idx}"] = str(v.processed_fish or 0)
        out[f"video_created_at_{idx}"] = (
            format_china_datetime(v.created_at)
            if v.created_at else "--"
        )
        out[f"video_finished_at_{idx}"] = (
            format_china_datetime(v.finished_at)
            if v.finished_at else "--"
        )
    return out


def _build_abnormal_rows_context(top_abnormal: list) -> dict:
    """为模板 abnormal_index_1..4 / abnormal_fish_no_1..4 / ... 生成 4 行。"""
    from app.services.report_format_utils import build_abnormal_reason
    from app.services.fish_id_service import derive_fish_short_id, derive_video_display_id

    out: dict[str, Any] = {}
    for i in range(1, 5):
        out[f"abnormal_index_{i}"] = "--"
        out[f"abnormal_fish_no_{i}"] = "--"
        out[f"abnormal_video_id_{i}"] = "--"
        out[f"abnormal_body_length_{i}"] = "--"
        out[f"abnormal_body_length_without_tail_{i}"] = "--"
        out[f"abnormal_body_height_{i}"] = "--"
        out[f"abnormal_body_weight_{i}"] = "--"
        out[f"abnormal_condition_factor_{i}"] = "--"
        out[f"abnormal_surface_damage_{i}"] = "--"
        out[f"abnormal_reason_{i}"] = "--"

    for idx, f in enumerate(top_abnormal[:4], start=1):
        out[f"abnormal_index_{idx}"] = str(idx)
        out[f"abnormal_fish_no_{idx}"] = derive_fish_short_id(f.fish_id) or "--"
        out[f"abnormal_video_id_{idx}"] = derive_video_display_id(f.video_id) or "--"
        out[f"abnormal_body_length_{idx}"] = (
            f"{f.body_length_cm:.2f}" if f.body_length_cm is not None else "--"
        )
        out[f"abnormal_body_length_without_tail_{idx}"] = (
            f"{f.body_length_no_tail_cm:.2f}"
            if f.body_length_no_tail_cm is not None else "--"
        )
        out[f"abnormal_body_height_{idx}"] = (
            f"{f.body_height_cm:.2f}" if f.body_height_cm is not None else "--"
        )
        out[f"abnormal_body_weight_{idx}"] = (
            f"{f.pred_weight_g:.2f}" if f.pred_weight_g is not None else "--"
        )
        out[f"abnormal_condition_factor_{idx}"] = (
            f"{f.condition_factor:.3f}" if f.condition_factor is not None else "--"
        )
        pct = normalize_ratio_percent(f.surface_damage_ratio)
        out[f"abnormal_surface_damage_{idx}"] = (
            f"{pct:.2f}%" if pct is not None else "--"
        )
        out[f"abnormal_reason_{idx}"] = build_abnormal_reason(
            surface_damage_pct=pct,
            abnormal=True,
            is_partial=(f.status == "processing"),
        )

    return out
