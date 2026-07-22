"""
ops_algorithm_task_service.py

系统运维 -> 算法任务监控 后端服务。

聚合以下数据源，给出每个 video_id 的真实状态、阶段、错误、队列、文件、日志：

1. 数据库 VideoTask：status / total_fish / processed_fish / started_at / finished_at / last_error
2. RQ 队列：started / queued / failed / finished job count
3. 文件：
   - data/uploads/videos/playable/<video_id>_h264.mp4
   - data/jobs/<video_id>/state/pipeline_state.json
   - data/results/<video_id>/reports/pipeline_result.json
   - data/results/<video_id>/reports/error_conclusion.txt
   - data/results/<video_id>/reports/measurement.csv
   - data/results/<video_id>/videos/grounded_sam.mp4
4. 日志：data/logs/algorithm_cli/*run_video_pipeline.log
   找包含 video_id 的最新日志，提取 returncode / command / stdout / stderr tail
5. 简单 GPU 状态（nvidia-smi，可选）

只读，不修改任何业务数据；mark-failed / retry 接口由 router 调用 session_repo / queue。
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.paths import DATA_DIR, JOBS_DIR, RESULTS_DIR, VID_DIR
from app.repositories.db import SessionLocal
from app.repositories.models import VideoTask
from app.services.path_service import to_abs_data_path

logger = logging.getLogger("ops_algorithm_task_service")

# ---------------------------------------------------------------------------
# 阶段定义
# ---------------------------------------------------------------------------

# (stage_key, display_name)
STAGES: list[tuple[str, str]] = [
    ("calibrate", "标定检查"),
    ("video_to_frames", "视频抽帧"),
    ("adjust_batch", "视角矫正"),
    ("grounded_sam", "鱼体检测分割"),
    ("filter_complete_fish", "完整鱼筛选"),
    ("fish_condition", "五维指标计算"),
    ("publish_results", "结果发布"),
]

# 阶段关键字 -> stage_key
STAGE_KEYWORDS: list[tuple[str, str]] = [
    ("calibrate", "标定检查"),
    ("video_to_frames", "视频抽帧"),
    ("adjust_batch", "视角矫正"),
    ("grounded_sam", "鱼体检测分割"),
    ("filter_complete_fish", "完整鱼筛选"),
    ("fish_condition", "五维指标计算"),
    ("publish_results", "结果发布"),
    # 兼容旧 key
    ("crop_fish", "鱼体检测裁剪"),
    ("correct_image", "视角矫正"),
    ("import_db", "结果入库"),
    ("report", "报告生成"),
]

# 阶段粗略默认耗时（秒），用于 ETA
DEFAULT_STAGE_SECONDS: dict[str, float] = {
    "calibrate": 10.0,
    "video_to_frames_per_second": 0.3,
    "adjust_batch_per_second": 0.5,
    "grounded_sam_per_frame": 1.5,
    "filter_complete_fish": 30.0,
    "fish_condition_per_fish": 3.0,
    "publish_results": 20.0,
}

STUCK_THRESHOLD_SECONDS = 10 * 60  # 10 分钟没动 + 仍在 processing/queued ⇒ stuck

# 日志读取限制
LOG_TAIL_LINES = 200
LOG_TAIL_CHARS = 12000


# ---------------------------------------------------------------------------
# 文件 / 日志读取
# ---------------------------------------------------------------------------


def _safe_read_text(path: Path, max_chars: int = LOG_TAIL_CHARS) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            return text[-max_chars:]
        return text
    except Exception as exc:
        logger.warning("read text failed: %s err=%s", path, exc)
        return ""


def _file_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}
    try:
        st = path.stat()
        return {
            "exists": True,
            "path": str(path),
            "size": int(st.st_size),
            "mtime": datetime.fromtimestamp(
                st.st_mtime, tz=timezone.utc
            ).isoformat(),
        }
    except Exception as exc:
        return {"exists": True, "path": str(path), "error": str(exc)}


def _read_json(path: Path) -> tuple[Optional[dict], Optional[str]]:
    if not path.exists() or not path.is_file():
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace")), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def find_latest_algorithm_log(
    video_id: str,
    *,
    scan_limit: int = 100,
) -> Optional[Path]:
    """
    找 data/logs/algorithm_cli 下包含 video_id 的最新 run_video_pipeline.log。

    匹配规则（按以下顺序）：
        1. 文件名包含 video_id
        2. 文件内容包含 video_id（--video-id、video_id:、<video_id>_h264.mp4 等关键字）
    只扫描最近的 scan_limit 个 run_video_pipeline.log（按 mtime 倒序），避免性能问题。
    """
    log_dir = DATA_DIR / "logs" / "algorithm_cli"
    if not log_dir.exists():
        return None

    # 先按文件名匹配（命中概率最高）
    candidates: list[Path] = []
    try:
        for p in log_dir.glob("*run_video_pipeline*.log"):
            if video_id in p.name:
                candidates.append(p)
    except Exception as exc:
        logger.warning("scan log dir failed: %s", exc)

    # 1) 文件名命中
    if candidates:
        candidates.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        return candidates[0]

    # 2) 退化为按文件内容匹配最近 scan_limit 个日志
    all_logs: list[Path] = []
    try:
        for p in log_dir.glob("*run_video_pipeline*.log"):
            all_logs.append(p)
    except Exception as exc:
        logger.warning("scan log dir failed: %s", exc)
        return None
    all_logs.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    recent = all_logs[:scan_limit]

    video_id_str = str(video_id).strip()
    keys = (
        video_id_str,
        f"--video-id {video_id_str}",
        f"video_id: {video_id_str}",
        f"{video_id_str}_h264.mp4",
        f"pipeline start: video_id={video_id_str}",
    )
    for p in recent:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if any(k in text for k in keys):
            return p

    return None


def find_worker_log_tail(
    video_id: str,
    *,
    log_path: Optional[Path] = None,
    max_bytes: int = 2 * 1024 * 1024,
    context_lines: int = 50,
    max_chars: int = LOG_TAIL_CHARS,
) -> dict[str, Any]:
    """
    在 data/logs/backend_worker.log 里找包含 video_id 的最近若干行。
    为了避免读巨大文件，只从文件尾部读 max_bytes 字节。
    """
    if log_path is None:
        log_path = DATA_DIR / "logs" / "backend_worker.log"

    info: dict[str, Any] = {
        "path": str(log_path) if log_path else None,
        "exists": False,
        "mtime": None,
        "tail": "",
        "matched_lines": 0,
        "message": "",
    }
    if not log_path or not log_path.exists():
        info["message"] = f"未找到 worker 日志：{log_path}"
        return info
    info["exists"] = True
    try:
        st = log_path.stat()
        info["mtime"] = datetime.fromtimestamp(
            st.st_mtime, tz=timezone.utc
        ).isoformat()
    except Exception:
        pass

    try:
        with open(log_path, "rb") as fp:
            fp.seek(0, 2)  # 末尾
            file_size = fp.tell()
            start = max(0, file_size - max_bytes)
            fp.seek(start)
            raw = fp.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
    except Exception as exc:
        info["message"] = f"读 worker 日志失败：{exc}"
        return info

    lines = text.splitlines()
    video_id_str = str(video_id).strip()
    matched_indexes: list[int] = []
    for i, ln in enumerate(lines):
        if video_id_str in ln:
            matched_indexes.append(i)
    if not matched_indexes:
        info["message"] = (
            f"worker 日志中存在 {len(lines)} 行，但未匹配 video_id={video_id_str}"
        )
        return info

    info["matched_lines"] = len(matched_indexes)

    # 取最近 5 个匹配，每个匹配前后各 context_lines 行
    recent_idx = matched_indexes[-5:]
    picked_indexes: set[int] = set()
    for i in recent_idx:
        for j in range(max(0, i - context_lines), min(len(lines), i + context_lines + 1)):
            picked_indexes.add(j)
    picked = sorted(picked_indexes)
    out_lines = [lines[i] for i in picked]
    tail_text = "\n".join(out_lines)
    if len(tail_text) > max_chars:
        tail_text = tail_text[-max_chars:]
    info["tail"] = tail_text
    return info


def parse_algorithm_log(path: Path) -> dict[str, Any]:
    """
    解析 algorithm_cli 日志：

    提取 command、returncode、stdout tail、stderr tail、mtime。
    """
    info: dict[str, Any] = {
        "path": str(path) if path else None,
        "exists": False,
        "returncode": None,
        "command": None,
        "stdout_tail": "",
        "stderr_tail": "",
        "mtime": None,
        "parse_error": None,
    }
    if not path or not path.exists():
        return info
    info["exists"] = True
    try:
        st = path.stat()
        info["mtime"] = datetime.fromtimestamp(
            st.st_mtime, tz=timezone.utc
        ).isoformat()
    except Exception:
        pass

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        info["parse_error"] = f"read failed: {exc}"
        return info

    # 简易解析：基于关键词
    rc_match = re.search(r"^returncode:\s*(-?\d+)", text, re.MULTILINE)
    if rc_match:
        try:
            info["returncode"] = int(rc_match.group(1))
        except Exception:
            pass
    cmd_match = re.search(r"^command:\s*(.+)$", text, re.MULTILINE)
    if cmd_match:
        info["command"] = cmd_match.group(1).strip()

    # STDOUT / STDERR sections
    stdout_match = re.search(
        r"=+\s*STDOUT\s*=+\s*\n(.*?)(?=\n=+\s*STDERR\s*=|\\Z)",
        text,
        re.DOTALL,
    )
    if stdout_match:
        info["stdout_tail"] = _trim_text(stdout_match.group(1))
    stderr_match = re.search(
        r"=+\s*STDERR\s*=+\s*\n(.*?)(?=\n=+\s*STDOUT\s*=|\\Z)",
        text,
        re.DOTALL,
    )
    if stderr_match:
        info["stderr_tail"] = _trim_text(stderr_match.group(1))

    # 解析每个 step 的状态 / 计数 / metrics
    try:
        info["stages"] = _parse_log_stages(text)
    except Exception as exc:
        info["stages"] = {}
        info["parse_error"] = f"parse stages failed: {exc}"
    return info


def _trim_text(s: str) -> str:
    if not s:
        return ""
    if len(s) <= LOG_TAIL_CHARS:
        return s
    return s[-LOG_TAIL_CHARS:]


_STEP_PATTERN = re.compile(
    r"^\[step:(?P<key>[a-zA-Z_]+)\]\s+(?P<state>开始|完成)\s*$",
    re.MULTILINE,
)


def _parse_log_stages(log_text: str) -> dict[str, dict[str, Any]]:
    """从 algorithm_cli 日志解析每个 stage 的 status / metrics。

    支持的关键字：
        [step:<key>] 开始 / 完成
        frame_count : <int>           (video_to_frames / grounded_sam)
        last_frame    : <name>        (video_to_frames)
        resolution    : <str>          (video_to_frames)
        fps           : <float>        (video_to_frames)
        批量矫正完成: 输入 <int> 张，成功 <int> 张
        total / complete / incomplete / skipped (filter_complete_fish)
        rows_total / done / incomplete / failed (fish_condition)
        [publish] 已发布长期结果 / 长期结果
    """
    if not log_text:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for m in _STEP_PATTERN.finditer(log_text):
        key = m.group("key")
        state = m.group("state")
        cur = out.setdefault(
            key, {"status": "pending", "input_count": None, "output_count": None, "metrics": {}}
        )
        if state == "完成":
            cur["status"] = "done"
        elif state == "开始" and cur.get("status") == "pending":
            cur["status"] = "running"

    # video_to_frames
    for m in re.finditer(r"frame_count\s*:\s*(\d+)", log_text):
        out.setdefault("video_to_frames", {"status": "pending", "input_count": None, "output_count": None, "metrics": {}})
        out["video_to_frames"]["output_count"] = int(m.group(1))
        out["video_to_frames"]["metrics"]["frame_count"] = int(m.group(1))
    for m in re.finditer(r"resolution\s*:\s*([^\n]+)", log_text):
        out.setdefault("video_to_frames", {"status": "pending", "input_count": None, "output_count": None, "metrics": {}})
        out["video_to_frames"]["metrics"]["resolution"] = m.group(1).strip()
    for m in re.finditer(r"fps\s*:\s*([\d.]+)", log_text):
        out.setdefault("video_to_frames", {"status": "pending", "input_count": None, "output_count": None, "metrics": {}})
        out["video_to_frames"]["metrics"]["fps"] = float(m.group(1))

    # adjust_batch
    for m in re.finditer(r"批量矫正完成[：:]\s*输入\s*(\d+)\s*张[，,]\s*成功\s*(\d+)\s*张", log_text):
        out.setdefault("adjust_batch", {"status": "pending", "input_count": None, "output_count": None, "metrics": {}})
        out["adjust_batch"]["input_count"] = int(m.group(1))
        out["adjust_batch"]["output_count"] = int(m.group(2))
        out["adjust_batch"]["metrics"]["input"] = int(m.group(1))
        out["adjust_batch"]["metrics"]["success"] = int(m.group(2))
        out["adjust_batch"]["status"] = "done"

    # grounded_sam
    if re.search(r"Video saved to:.+\.mp4", log_text):
        out.setdefault("grounded_sam", {"status": "pending", "input_count": None, "output_count": None, "metrics": {}})
        if out["grounded_sam"].get("status") != "done":
            out["grounded_sam"]["status"] = "done"
    for m in re.finditer(r"frame_count\s*:\s*(\d+)", log_text):
        # Don't overwrite video_to_frames's frame_count; only apply to grounded_sam if not set
        if out.get("video_to_frames", {}).get("metrics", {}).get("frame_count") != int(m.group(1)):
            # ambiguous - skip
            pass
    # video_output line is the most reliable grounded_sam indicator
    for m in re.finditer(r"video_output\s*:?\s*(\S+)", log_text):
        out.setdefault("grounded_sam", {"status": "pending", "input_count": None, "output_count": None, "metrics": {}})
        out["grounded_sam"]["metrics"]["video_output"] = m.group(1).strip()

    # filter_complete_fish
    for m in re.finditer(r"^total\s*:\s*(\d+)\s*$", log_text, re.MULTILINE):
        out.setdefault("filter_complete_fish", {"status": "pending", "input_count": None, "output_count": None, "metrics": {}})
        out["filter_complete_fish"]["output_count"] = int(m.group(1))
        out["filter_complete_fish"]["metrics"]["total"] = int(m.group(1))
    for key_pat, val_key in [
        (r"^complete\s*:\s*(\d+)\s*$", "complete"),
        (r"^incomplete\s*:\s*(\d+)\s*$", "incomplete"),
        (r"^skipped\s*:\s*(\d+)\s*$", "skipped"),
    ]:
        for m in re.finditer(key_pat, log_text, re.MULTILINE):
            out.setdefault("filter_complete_fish", {"status": "pending", "input_count": None, "output_count": None, "metrics": {}})
            out["filter_complete_fish"]["metrics"][val_key] = int(m.group(1))
    # If any filter metric is set, ensure status reflects done if 完成 line was seen
    if out.get("filter_complete_fish"):
        if re.search(r"====\s*filter_complete_fish\s*完成", log_text):
            out["filter_complete_fish"]["status"] = "done"

    # fish_condition
    for m in re.finditer(r"^rows_total\s*:\s*(\d+)\s*$", log_text, re.MULTILINE):
        out.setdefault("fish_condition", {"status": "pending", "input_count": None, "output_count": None, "metrics": {}})
        out["fish_condition"]["output_count"] = int(m.group(1))
        out["fish_condition"]["metrics"]["rows_total"] = int(m.group(1))
    for key_pat, val_key in [
        (r"^\s*done\s*:\s*(\d+)\s*$", "done"),
        (r"^\s*incomplete\s*:\s*(\d+)\s*$", "incomplete"),
        (r"^\s*failed\s*:\s*(\d+)\s*$", "failed"),
    ]:
        for m in re.finditer(key_pat, log_text, re.MULTILINE):
            out.setdefault("fish_condition", {"status": "pending", "input_count": None, "output_count": None, "metrics": {}})
            out["fish_condition"]["metrics"][val_key] = int(m.group(1))
    if out.get("fish_condition"):
        if re.search(r"====\s*fish_condition\s*完成", log_text):
            out["fish_condition"]["status"] = "done"

    # publish_results
    if re.search(r"\[publish\]\s*已发布长期结果", log_text):
        out.setdefault("publish_results", {"status": "pending", "input_count": None, "output_count": None, "metrics": {}})
        out["publish_results"]["status"] = "done"

    return out


def _pipeline_state_summary(video_id: str) -> dict[str, Any]:
    """
    读 data/jobs/<video_id>/state/pipeline_state.json，构造一个"日志兜底"摘要。
    当 algorithm_cli 日志找不到时，前端至少能看到这个摘要。
    """
    out: dict[str, Any] = {
        "exists": False,
        "path": "",
        "mtime": None,
        "status": None,
        "current_stage": None,
        "failed_stage": None,
        "error_message": None,
        "completed_stages": [],
        "command": None,
        "raw_tail": "",
        "message": "",
    }
    path = JOBS_DIR / video_id / "state" / "pipeline_state.json"
    out["path"] = str(path)
    if not path.exists() or not path.is_file():
        out["message"] = f"未找到 pipeline_state.json：{path}"
        return out

    out["exists"] = True
    try:
        st = path.stat()
        out["mtime"] = datetime.fromtimestamp(
            st.st_mtime, tz=timezone.utc
        ).isoformat()
    except Exception:
        pass

    payload, parse_err = _read_json(path)
    if not isinstance(payload, dict):
        out["message"] = parse_err or "pipeline_state.json 解析失败"
        return out

    out["status"] = payload.get("status")
    out["current_stage"] = payload.get("current_stage")
    out["failed_stage"] = payload.get("failed_stage")
    out["completed_stages"] = list(payload.get("completed_stages") or [])
    out["error_message"] = payload.get("error_message")
    out["command"] = payload.get("command")

    # raw_tail：格式化后的关键字段摘要（避免把整个 JSON 塞进 raw_tail）
    summary_keys = (
        "status",
        "current_stage",
        "failed_stage",
        "completed_stages",
        "error_message",
        "started_at",
        "updated_at",
    )
    summary = {k: payload.get(k) for k in summary_keys if k in payload}
    try:
        out["raw_tail"] = json.dumps(summary, ensure_ascii=False, indent=2)
    except Exception:
        out["raw_tail"] = str(summary)
    return out


# ---------------------------------------------------------------------------
# RQ 状态
# ---------------------------------------------------------------------------


def _probe_redis() -> dict[str, Any]:
    """
    统一探测 Redis 可用性。summary / detail 共享这个函数，避免两边 redis_ok 状态不一致。

    返回：
        {"ok": bool, "error_message": Optional[str], "client": Optional[redis.Redis]}
    """
    try:
        from app.infra.redis_client import get_redis

        client = get_redis()
        client.ping()
        return {"ok": True, "error_message": None, "client": client}
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.warning("redis ping failed: %s", msg)
        return {"ok": False, "error_message": msg, "client": None}


def _safe_redis_ping() -> bool:
    """
    保留兼容旧调用方；现在直接转 _probe_redis().ok。
    """
    return _probe_redis()["ok"]


def _query_rq_jobs(video_id: str) -> dict[str, Any]:
    """
    在 RQ 队列里查 video_id 关联的 job 状态。

    返回：
        {
            "redis_ok": bool,
            "error_message": Optional[str],
            "queue": Optional[str],
            "job_id": Optional[str],
            "job_state": Optional[str],
            "func_name": Optional[str],
            "args_summary": Optional[str],
            "started_at": Optional[str],
            "ended_at": Optional[str],
            "exc_info": Optional[str],
        }

    注意：
        - redis_ok / error_message 跟 summary 接口的 worker.redis_ok / worker.error_message
          用同一个 _probe_redis() 探测，保证两边状态一致。
        - 如果 Redis 不可用，直接返回 redis_ok=False + error_message，不静默只给 false。
    """
    info: dict[str, Any] = {
        "redis_ok": False,
        "error_message": None,
        "queue": None,
        "job_id": None,
        "job_state": None,
        "func_name": None,
        "args_summary": None,
        "started_at": None,
        "ended_at": None,
        "exc_info": None,
    }
    probe = _probe_redis()
    if not probe["ok"]:
        info["redis_ok"] = False
        info["error_message"] = probe["error_message"]
        info["exc_info"] = probe["error_message"]
        info["queue"] = None
        return info

    info["redis_ok"] = True
    info["error_message"] = None
    redis_client = probe["client"]
    if redis_client is None:
        return info

    try:
        from rq.job import Job, NoSuchJob
        from rq.registry import (
            StartedJobRegistry,
            FinishedJobRegistry,
            FailedJobRegistry,
            DeferredJobRegistry,
            ScheduledJobRegistry,
        )

        from app.workers.queue import DEFAULT_QUEUE_NAME, get_queue

        info["queue"] = DEFAULT_QUEUE_NAME
        redis = redis_client

        # 通过所有 registry 查
        for reg_cls, reg_state in (
            (StartedJobRegistry, "started"),
            (DeferredJobRegistry, "deferred"),
            (ScheduledJobRegistry, "scheduled"),
            (FinishedJobRegistry, "finished"),
            (FailedJobRegistry, "failed"),
        ):
            try:
                reg = reg_cls(queue=get_queue(DEFAULT_QUEUE_NAME), connection=redis)
                for jid in reg.get_job_ids():
                    try:
                        j = Job.fetch(jid, connection=redis)
                    except NoSuchJob:
                        continue
                    if j.kwargs.get("upload_video_id") == video_id:
                        info["job_id"] = jid
                        info["job_state"] = reg_state
                        info["func_name"] = j.func_name
                        info["args_summary"] = _summarize_args(j.kwargs)
                        info["started_at"] = (
                            j.started_at.isoformat() if j.started_at else None
                        )
                        info["ended_at"] = (
                            j.ended_at.isoformat() if j.ended_at else None
                        )
                        return info
            except Exception as exc:
                logger.debug("scan registry %s failed: %s", reg_state, exc)
                continue
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.warning("query RQ jobs failed: %s", msg)
        # 如果查 job 时整体失败，但 Redis ping 已经成功，则 redis_ok 仍然 True；
        # 但要把 error_message 透出给前端。
        info["error_message"] = msg
        info["exc_info"] = msg
    return info


def _summarize_args(kwargs: dict[str, Any] | None) -> str:
    if not kwargs:
        return ""
    parts = []
    for k, v in kwargs.items():
        if isinstance(v, str) and len(v) > 80:
            parts.append(f"{k}=<{len(v)} chars>")
        else:
            parts.append(f"{k}={v}")
    return ", ".join(parts)


def _count_jobs_by_state() -> dict[str, Any]:
    """
    统计 fish 队列里所有 job 的 state 分布（粗略）。

    与 _query_rq_jobs 共享 _probe_redis() 探测结果，保证 summary.worker.redis_ok
    与 detail.queue.redis_ok 表现一致。

    返回字段：
        queued_jobs / started_jobs / deferred_jobs / finished_jobs / failed_jobs
        redis_ok
        error_message
    """
    counts: dict[str, Any] = {
        "queued_jobs": 0,
        "started_jobs": 0,
        "deferred_jobs": 0,
        "finished_jobs": 0,
        "failed_jobs": 0,
        "redis_ok": False,
        "error_message": None,
    }

    probe = _probe_redis()
    if not probe["ok"]:
        msg = probe["error_message"] or "redis unavailable"
        logger.warning("count_jobs_by_state redis unavailable: %s", msg)
        counts["redis_ok"] = False
        counts["error_message"] = msg
        return counts
    counts["redis_ok"] = True
    redis = probe["client"]
    if redis is None:
        counts["error_message"] = "redis client unavailable"
        return counts

    try:
        from app.workers.queue import DEFAULT_QUEUE_NAME, get_queue
        from rq.registry import (
            StartedJobRegistry,
            FinishedJobRegistry,
            FailedJobRegistry,
            DeferredJobRegistry,
            ScheduledJobRegistry,
        )

        q = get_queue(DEFAULT_QUEUE_NAME)
        for reg_cls, key in (
            (ScheduledJobRegistry, "queued_jobs"),
            (StartedJobRegistry, "started_jobs"),
            (DeferredJobRegistry, "deferred_jobs"),
            (FinishedJobRegistry, "finished_jobs"),
            (FailedJobRegistry, "failed_jobs"),
        ):
            try:
                reg = reg_cls(queue=q, connection=redis)
                counts[key] = len(reg.get_job_ids())
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                logger.debug("count %s failed: %s", key, msg)
                # 局部 registry 失败：让 worker 整体 error_message 也透出来
                counts["error_message"] = msg
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.warning("count_jobs_by_state failed: %s", msg)
        counts["error_message"] = msg
    return counts


# ---------------------------------------------------------------------------
# 阶段识别
# ---------------------------------------------------------------------------


@dataclass
class StageInfo:
    key: str
    name: str
    status: str  # done / failed / running / pending / skipped / unknown
    elapsed_seconds: Optional[float] = None
    input_count: Optional[int] = None
    output_count: Optional[int] = None
    error_message: Optional[str] = None


def _detect_current_stage(text: str) -> Optional[str]:
    """从 stdout / 日志里抓当前阶段关键字。"""
    if not text:
        return None
    # 倒序匹配
    last_key = None
    for line in text.splitlines():
        line_l = line.lower()
        for key, _name in STAGE_KEYWORDS:
            if key in line_l:
                last_key = key
    return last_key


def _build_stage_timeline(
    pipeline_result: Optional[dict],
    pipeline_state: Optional[dict],
    log_text: str,
    rq_state: Optional[str],
    video_task: VideoTask,
) -> list[dict[str, Any]]:
    """
    构造算法阶段状态时间线。

    优先级：
        1. pipeline_result.completed_stages / failed_stage / summary
        2. pipeline_state.completed_stages / failed_stage / current_stage
        3. algorithm_cli 日志（_parse_log_stages）：status / input_count / output_count / metrics
        4. 日志关键字（_detect_current_stage）
        5. fallback: pending

    当 db / pipeline_result 状态为 done 时，main pipeline 中所有 STAGES
    都被强制标记为 done（除非 pipeline_result.failed_stage 显式声明），
    避免 import_db / report 仍显示 running。
    """
    completed: set[str] = set()
    failed_stage: Optional[str] = None
    current_stage: Optional[str] = None
    stage_elapsed: dict[str, float] = {}
    summary_metrics: dict[str, Any] = {}

    if pipeline_result and isinstance(pipeline_result, dict):
        for s in (pipeline_result.get("completed_stages") or []):
            if isinstance(s, str):
                completed.add(s)
        if isinstance(pipeline_result.get("failed_stage"), str):
            failed_stage = pipeline_result["failed_stage"]
            completed.discard(failed_stage)
        pr_summary = pipeline_result.get("summary")
        if isinstance(pr_summary, dict):
            for k, v in pr_summary.items():
                if isinstance(v, (int, float, str)):
                    summary_metrics[k] = v

    if pipeline_state and isinstance(pipeline_state, dict):
        for s in (pipeline_state.get("completed_stages") or []):
            if isinstance(s, str):
                completed.add(s)
        if isinstance(pipeline_state.get("failed_stage"), str):
            failed_stage = pipeline_state["failed_stage"]
        if isinstance(pipeline_state.get("current_stage"), str):
            current_stage = pipeline_state["current_stage"]
        st = pipeline_state.get("stage_timings") or pipeline_state.get("timings")
        if isinstance(st, dict):
            for k, v in st.items():
                if isinstance(v, (int, float)):
                    stage_elapsed[str(k)] = float(v)

    # 日志解析：每个 stage 的 status / input_count / output_count / metrics
    log_stages: dict[str, dict[str, Any]] = _parse_log_stages(log_text or "")
    # fallback: 日志关键字
    log_stage = _detect_current_stage(log_text or "")
    if not current_stage and log_stage:
        current_stage = log_stage

    # 当 db / pipeline_result 状态为 done 时，main pipeline 中所有 STAGES
    # 都被强制标记为 done（除非 failed_stage 显式声明）。
    db_status = (video_task.status or "").lower() if video_task else ""
    pr_status = (
        (pipeline_result.get("status") or "").lower()
        if pipeline_result and isinstance(pipeline_result, dict)
        else ""
    )
    if db_status == "done" or pr_status == "done":
        for key, _name in STAGES:
            completed.add(key)
        if current_stage and current_stage in completed:
            current_stage = None

    # 状态转移：current_stage 在 completed 集合中时，认为 completed
    if current_stage and current_stage in completed:
        current_stage = None

    out: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    base_started = video_task.started_at if video_task else None

    for key, name in STAGES:
        # 兼容：把旧 key 映射到新 key
        log_info = log_stages.get(key) or {}
        elapsed: Optional[float] = stage_elapsed.get(key) or log_info.get("elapsed_seconds")
        in_completed = key in completed
        in_failed = failed_stage == key
        in_current = current_stage == key and not in_completed

        if in_failed:
            status = "failed"
        elif in_completed:
            status = "done"
        elif in_current:
            status = "running"
        else:
            # fall back to log_stages parsed status if available
            log_status = log_info.get("status")
            if log_status in {"done", "failed", "running"}:
                status = log_status
            elif log_status in {"pending"}:
                status = "pending"
            else:
                status = "pending"

        if status == "running" and base_started:
            try:
                base_dt = (
                    base_started
                    if base_started.tzinfo
                    else base_started.replace(tzinfo=timezone.utc)
                )
                elapsed = elapsed or max(0.0, (now - base_dt).total_seconds())
            except Exception:
                pass

        entry: dict[str, Any] = {
            "key": key,
            "name": name,
            "status": status,
            "elapsed_seconds": elapsed,
            "input_count": log_info.get("input_count"),
            "output_count": log_info.get("output_count"),
            "error_message": (
                video_task.last_error if (in_failed and video_task) else None
            ),
            "metrics": dict(log_info.get("metrics") or {}),
        }
        if not in_failed and entry["error_message"] is None and status == "failed":
            entry["error_message"] = _extract_stage_error(log_text or "", key)
        out.append(entry)

    # 从 pipeline_result.summary 补一些 output_count（兼容旧 summary key）
    for stage in out:
        if stage["status"] == "done" and stage["output_count"] is None:
            if stage["key"] in {"filter_complete_fish", "fish_condition"}:
                v = summary_metrics.get("total_fish") or summary_metrics.get("total")
                if isinstance(v, (int, float)):
                    stage["output_count"] = int(v)
    return out


def _extract_stage_error(log_text: str, stage_key: str) -> Optional[str]:
    if not log_text:
        return None
    lines = log_text.splitlines()
    in_stage = False
    captured: list[str] = []
    for ln in lines:
        low = ln.lower()
        if stage_key in low:
            in_stage = True
            captured.append(ln)
            continue
        if in_stage:
            captured.append(ln)
            if "Traceback" in ln or "Stage failed" in ln:
                break
            if len(captured) > 40:
                break
    if captured:
        return "\n".join(captured[-12:])
    return None


# ---------------------------------------------------------------------------
# Display status / ETA
# ---------------------------------------------------------------------------


def _compute_last_activity(
    video_task: VideoTask,
    log_info: dict[str, Any],
    pipeline_state: Optional[dict],
) -> tuple[Optional[datetime], str]:
    """
    决定 last_activity_time：取 VideoTask.updated_at / pipeline_state mtime /
    algorithm_cli 日志 mtime 三者最大值（max）。
    """
    candidates: list[tuple[datetime, str]] = []
    if video_task.updated_at:
        ts = (
            video_task.updated_at
            if video_task.updated_at.tzinfo
            else video_task.updated_at.replace(tzinfo=timezone.utc)
        )
        candidates.append((ts, "video_task.updated_at"))
    if pipeline_state and isinstance(pipeline_state, dict):
        ps_mtime = pipeline_state.get("started_at") or pipeline_state.get(
            "updated_at"
        )
        if ps_mtime:
            try:
                ts = datetime.fromisoformat(ps_mtime)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                candidates.append((ts, "pipeline_state"))
            except Exception:
                pass
    log_mtime_str = log_info.get("mtime")
    if log_mtime_str:
        try:
            ts = datetime.fromisoformat(log_mtime_str)
            candidates.append((ts, "algorithm_cli_log"))
        except Exception:
            pass
    if not candidates:
        return None, ""
    best = max(candidates, key=lambda x: x[0])
    return best


def _compute_display_status(
    video_task: VideoTask,
    rq_info: dict[str, Any],
    log_info: dict[str, Any],
    files_info: dict[str, Any],
    stage_timeline: list[dict[str, Any]],
    pipeline_state: Optional[dict] = None,
) -> dict[str, Any]:
    """
    返回 dict：
        display_status, is_stuck, reason, last_activity_at, last_activity_source

    display_status: processing | done | failed | stuck | queued | recording | unknown
    """
    db_status = (video_task.status or "").lower()

    # 决定 last_activity：取 VideoTask.updated_at / pipeline_state mtime / 算法日志 mtime 的 max
    last_mtime_dt, last_source = _compute_last_activity(
        video_task, log_info, pipeline_state
    )

    if db_status in {"done", "failed", "stopped"}:
        return {
            "display_status": db_status,
            "is_stuck": False,
            "reason": "",
            "last_activity_at": last_mtime_dt.isoformat() if last_mtime_dt else None,
            "last_activity_source": last_source,
        }

    rq_state = (rq_info.get("job_state") or "").lower()
    now = datetime.now(timezone.utc)
    minutes_since_activity = None
    if last_mtime_dt:
        minutes_since_activity = (now - last_mtime_dt).total_seconds() / 60

    no_rq = rq_state in (None, "", "none")
    activity_stale = last_mtime_dt is None or (
        minutes_since_activity is not None and minutes_since_activity > 10
    )

    base = {
        "is_stuck": False,
        "reason": "",
        "last_activity_at": last_mtime_dt.isoformat() if last_mtime_dt else None,
        "last_activity_source": last_source,
    }

    # stuck 判定：DB 还在 processing/queued，RQ 没找到 job，且 last_activity 超过 10 分钟
    if db_status in {"processing", "queued"} and no_rq and activity_stale:
        reason = (
            f"DB 状态为 {video_task.status}，但 RQ 队列中找不到该 video_id，"
            f"且最近活动（{last_source}）距今已超过 10 分钟。"
        )
        return {
            "display_status": "stuck",
            "is_stuck": True,
            "reason": reason,
            "last_activity_at": last_mtime_dt.isoformat() if last_mtime_dt else None,
            "last_activity_source": last_source,
        }

    if db_status == "recording":
        return {**base, "display_status": "recording"}

    if db_status == "queued":
        return {**base, "display_status": "queued"}

    if db_status == "processing":
        if any(s["status"] == "running" for s in stage_timeline):
            return {**base, "display_status": "processing"}
        return {
            **base,
            "display_status": "processing",
            "reason": "未识别到正在运行的阶段。",
        }

    return {**base, "display_status": db_status or "unknown"}


def _compute_eta(
    stage_timeline: list[dict[str, Any]],
    video_task: VideoTask,
    video_duration_seconds: Optional[float],
) -> tuple[Optional[float], str, str]:
    """
    返回 (eta_seconds, eta_text, eta_confidence)。
    """
    if not stage_timeline:
        return None, "暂无法估算", "low"
    failed_or_done = all(
        s["status"] in {"done", "failed", "skipped"} for s in stage_timeline
    )
    if failed_or_done:
        return 0, "已完成或失败", "high"

    remaining_keys = [
        s["key"] for s in stage_timeline if s["status"] in {"running", "pending"}
    ]
    if not remaining_keys:
        return None, "暂无法估算", "low"

    # 粗略估算
    total = 0.0
    confidences = []
    for key in remaining_keys:
        if key == "calibrate":
            total += DEFAULT_STAGE_SECONDS["calibrate"]
            confidences.append("medium")
        elif key == "video_to_frames":
            vd = video_duration_seconds or 60.0
            total += vd * DEFAULT_STAGE_SECONDS["video_to_frames_per_second"]
            confidences.append("low")
        elif key == "crop_fish":
            vd = video_duration_seconds or 60.0
            total += vd * DEFAULT_STAGE_SECONDS["crop_fish_per_second"]
            confidences.append("low")
        elif key == "correct_image":
            vd = video_duration_seconds or 60.0
            total += vd * DEFAULT_STAGE_SECONDS["correct_image_per_second"]
            confidences.append("low")
        elif key == "five_dimension":
            tf = max(int(video_task.total_fish or 0), 1)
            total += tf * DEFAULT_STAGE_SECONDS["five_dimension_per_fish"]
            confidences.append("low")
        elif key == "import_db":
            total += DEFAULT_STAGE_SECONDS["import_db"]
            confidences.append("medium")
        elif key == "report":
            total += DEFAULT_STAGE_SECONDS["report"]
            confidences.append("medium")
        else:
            total += 30.0
            confidences.append("low")
    eta = round(total, 1)
    if "high" in confidences:
        conf = "high"
    elif "medium" in confidences:
        conf = "medium"
    else:
        conf = "low"
    return eta, f"约 {int(eta)} 秒", conf


# ---------------------------------------------------------------------------
# GPU 状态（可选）
# ---------------------------------------------------------------------------


def _read_gpu_info() -> dict[str, Any]:
    info: dict[str, Any] = {"available": False}
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return info
    if proc.returncode != 0 or not proc.stdout.strip():
        return info
    first = proc.stdout.strip().splitlines()[0]
    parts = [p.strip() for p in first.split(",")]
    if len(parts) >= 3:
        try:
            info["available"] = True
            info["name"] = parts[0]
            info["memory_used_mb"] = int(float(parts[1]))
            info["memory_total_mb"] = int(float(parts[2]))
        except Exception:
            pass
    return info


# ---------------------------------------------------------------------------
# Files / Pipeline state
# ---------------------------------------------------------------------------


def _collect_files(video_id: str) -> dict[str, Any]:
    abs_vid = video_id
    playable = (
        VID_DIR
        / "playable"
        / f"{abs_vid}_h264.mp4"
    )
    playable_upload = VID_DIR / f"{abs_vid}{'.mp4'}"
    job_root = JOBS_DIR / abs_vid
    pipeline_state = job_root / "state" / "pipeline_state.json"
    final_results = RESULTS_DIR / abs_vid
    pipeline_result = final_results / "reports" / "pipeline_result.json"
    error_conclusion = final_results / "reports" / "error_conclusion.txt"
    # 真实测量 CSV 路径：measurements/long_height_weight_results.csv
    # 候选路径：results/measurements/、jobs/measurements/、pipeline_result.measurement_csv
    measurement_csv_candidates = [
        final_results / "measurements" / "long_height_weight_results.csv",
        job_root / "measurements" / "long_height_weight_results.csv",
    ]
    measurement_csv_path: Optional[Path] = None
    for cand in measurement_csv_candidates:
        if cand.is_file():
            measurement_csv_path = cand
            break
    if measurement_csv_path is None:
        # 兜底：保留一个默认 result 路径（不一定存在）以便前端展示 path
        measurement_csv_path = measurement_csv_candidates[0]
    grounded_video = final_results / "videos" / "grounded_sam.mp4"
    return {
        "playable_h264": _file_info(playable),
        "playable_mp4": _file_info(playable_upload),
        "job_root": _file_info(job_root),
        "pipeline_state_json": _file_info(pipeline_state),
        "pipeline_result_json": _file_info(pipeline_result),
        "error_conclusion_txt": _file_info(error_conclusion),
        "measurement_csv": {
            **_file_info(measurement_csv_path),
            "candidates": [str(c) for c in measurement_csv_candidates],
        },
        "grounded_sam_mp4": _file_info(grounded_video),
    }


# ---------------------------------------------------------------------------
# DB -> dict
# ---------------------------------------------------------------------------


def _video_task_to_dict(vt: VideoTask) -> dict[str, Any]:
    def _iso(dt: Optional[datetime]) -> Optional[str]:
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc).isoformat()
        return dt.isoformat()

    return {
        "video_id": vt.video_id,
        "source_type": vt.source_type,
        "status": vt.status,
        "current_algorithm": vt.current_algorithm,
        "total_fish": int(vt.total_fish or 0),
        "processed_fish": int(vt.processed_fish or 0),
        "complete_fish": int(vt.complete_fish or 0),
        "incomplete_fish": int(vt.incomplete_fish or 0),
        "failed_fish": int(vt.failed_fish or 0),
        "damaged_fish": int(vt.damaged_fish or 0),
        "avg_surface_damage_ratio": vt.avg_surface_damage_ratio,
        "max_surface_damage_ratio": vt.max_surface_damage_ratio,
        "source_path": vt.source_path,
        "result_dir": vt.result_dir,
        "pipeline_result_path": vt.pipeline_result_path,
        "error_conclusion_path": vt.error_conclusion_path,
        "grounded_video_path": vt.grounded_video_path,
        "measurement_csv_path": vt.measurement_csv_path,
        "failed_stage": vt.failed_stage,
        "error_message": vt.error_message,
        "last_error": vt.last_error,
        "fps_estimate": vt.fps_estimate,
        "created_at": _iso(vt.created_at),
        "updated_at": _iso(vt.updated_at),
        "started_at": _iso(vt.started_at),
        "finished_at": _iso(vt.finished_at),
        "total_fish": int(getattr(vt, "total_fish", 0) or 0),
        "complete_fish": int(getattr(vt, "complete_fish", 0) or 0),
        "incomplete_fish": int(getattr(vt, "incomplete_fish", 0) or 0),
        "failed_fish": int(getattr(vt, "failed_fish", 0) or 0),
        "damaged_fish": int(getattr(vt, "damaged_fish", 0) or 0),
    }


# ---------------------------------------------------------------------------
# Summary / List / Detail
# ---------------------------------------------------------------------------


def get_algorithm_summary() -> dict[str, Any]:
    """
    GET /api/ops/algorithm-tasks/summary
    """
    today = datetime.now(timezone.utc).date()
    counts = {
        "today_total": 0,
        "queued": 0,
        "processing": 0,
        "done": 0,
        "failed": 0,
        "stuck": 0,
    }
    try:
        db = SessionLocal()
        try:
            rows = db.query(VideoTask).order_by(desc(VideoTask.created_at)).limit(500).all()
        finally:
            db.close()
        for r in rows:
            if r.created_at and r.created_at.date() == today:
                counts["today_total"] += 1
            st = (r.status or "").lower()
            if st == "queued":
                counts["queued"] += 1
            elif st == "processing":
                counts["processing"] += 1
            elif st == "done":
                counts["done"] += 1
            elif st == "failed":
                counts["failed"] += 1
    except Exception as exc:
        logger.warning("query summary failed: %s", exc)

    rq_counts = _count_jobs_by_state()
    # 复用 _count_jobs_by_state 里的 redis_ok / error_message，避免两边不一致
    redis_ok = bool(rq_counts.get("redis_ok", False))
    worker_error = rq_counts.get("error_message")

    return {
        "ok": True,
        "data": {
            **counts,
            "worker": {
                "queue": "fish",
                **rq_counts,
                "redis_ok": redis_ok,
                "error_message": worker_error,
            },
            "model_runtime": {
                "mode": "cli",
                "preload_supported": False,
                "loaded": False,
                "message": (
                    "当前算法以 CLI 子进程方式运行，模型会随任务进程加载和释放；"
                    "若要常驻显存，需要后续改造成常驻算法服务。"
                ),
                "gpu": _read_gpu_info(),
            },
        },
    }


def list_algorithm_tasks(
    *,
    status: Optional[str] = None,
    date: Optional[str] = None,
    limit: int = 100,
) -> dict[str, Any]:
    """
    GET /api/ops/algorithm-tasks
    """
    items: list[dict[str, Any]] = []
    try:
        db = SessionLocal()
        try:
            q = db.query(VideoTask).order_by(desc(VideoTask.created_at))
            if status:
                q = q.filter(VideoTask.status == status)
            rows = q.limit(max(1, min(limit, 500))).all()
        finally:
            db.close()
    except Exception as exc:
        logger.warning("list tasks failed: %s", exc)
        rows = []

    for r in rows:
        if date and r.created_at and r.created_at.date().isoformat() != date:
            continue
        rq_info = _query_rq_jobs(r.video_id)
        log_path = find_latest_algorithm_log(r.video_id)
        log_info = parse_algorithm_log(log_path) if log_path else {}
        log_text = _safe_read_text(log_path) if log_path else ""
        files = _collect_files(r.video_id)
        pr, pr_err = _read_json(
            RESULTS_DIR / r.video_id / "reports" / "pipeline_result.json"
        )
        ps, ps_err = _read_json(
            JOBS_DIR / r.video_id / "state" / "pipeline_state.json"
        )
        timeline = _build_stage_timeline(pr, ps, log_text, rq_info.get("job_state"), r)
        status_info = _compute_display_status(
            r, rq_info, log_info, files, timeline,
            pipeline_state=ps,
        )
        display_status = status_info["display_status"]
        is_stuck = status_info["is_stuck"]
        stuck_reason = status_info["reason"]
        eta_seconds, eta_text, eta_conf = _compute_eta(
            timeline, r, video_task_to_duration(r)
        )

        finished, time_anomaly = _normalize_finish_time(r, ps)
        elapsed = None
        if r.started_at:
            try:
                base_dt = (
                    r.started_at
                    if r.started_at.tzinfo
                    else r.started_at.replace(tzinfo=timezone.utc)
                )
                end_dt = finished if finished else datetime.now(timezone.utc)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                elapsed = max(0.0, (end_dt - base_dt).total_seconds())
            except Exception:
                pass

        # 当前阶段：第一个 running 阶段，否则 failed 阶段
        current_stage: Optional[str] = None
        for s in timeline:
            if s["status"] == "running":
                current_stage = s["key"]
                break
        if not current_stage and r.failed_stage:
            current_stage = r.failed_stage

        progress = _estimate_progress(display_status, timeline, r)

        items.append(
            {
                "video_id": r.video_id,
                "source_type": r.source_type,
                "status": r.status,
                "display_status": display_status,
                "is_stuck": is_stuck,
                "stuck_reason": stuck_reason,
                "current_stage": current_stage,
                "progress_percent": progress,
                "total_fish": int(r.total_fish or 0),
                "complete_fish": int(r.complete_fish or 0),
                "incomplete_fish": int(r.incomplete_fish or 0),
                "failed_fish": int(r.failed_fish or 0),
                "damaged_fish": int(r.damaged_fish or 0),
                "processed_fish": int(r.processed_fish or 0),
                "elapsed_seconds": round(elapsed, 1) if elapsed is not None else None,
                "eta_seconds": eta_seconds,
                "eta_text": eta_text,
                "eta_confidence": eta_conf,
                "queue_state": rq_info.get("job_state") or "none",
                "has_pipeline_result": pr is not None,
                "has_error_conclusion": files["error_conclusion_txt"]["exists"],
                "has_grounded_video": files["grounded_sam_mp4"]["exists"],
                "last_error": r.last_error or r.error_message,
                "failed_stage": r.failed_stage,
                "latest_log_path": log_info.get("path"),
                "latest_log_returncode": log_info.get("returncode"),
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": finished.isoformat() if finished else None,
                "time_anomaly": time_anomaly,
            }
        )

    return {"ok": True, "total": len(items), "items": items}


def _normalize_finish_time(
    video_task: VideoTask,
    pipeline_state: Optional[dict],
) -> tuple[Optional[datetime], bool]:
    """
    返回 (effective_finished_at, is_time_anomaly)。
    - 优先使用 VideoTask.finished_at；
    - 缺失时使用 pipeline_state.updated_at；
    - finished_at < started_at 视为异常（时区/旧字段混用），
      此时用 started_at + max(60, pipeline_state.elapsed_seconds || 60)。
    """
    finished = video_task.finished_at
    started = video_task.started_at
    if not finished and pipeline_state:
        ps_updated = pipeline_state.get("updated_at")
        if ps_updated:
            try:
                finished = datetime.fromisoformat(ps_updated)
            except Exception:
                finished = None
    anomaly = False
    # 让两个时间都带 timezone 才能比较
    if finished and started:
        if finished.tzinfo is None and started.tzinfo is not None:
            finished = finished.replace(tzinfo=started.tzinfo)
        elif started.tzinfo is None and finished.tzinfo is not None:
            started = started.replace(tzinfo=finished.tzinfo)
        if finished < started:
            anomaly = True
            # 估算 fallback finished_at
            delay = 60
            if pipeline_state:
                try:
                    delay = int(pipeline_state.get("elapsed_seconds") or delay)
                except Exception:
                    pass
            try:
                base_dt = (
                    started if started.tzinfo else started.replace(tzinfo=timezone.utc)
                )
                finished = base_dt + timedelta(seconds=delay)
            except Exception:
                finished = started
    return finished, anomaly
def video_task_to_duration(vt: VideoTask) -> Optional[float]:
    """从 VideoTask 拿视频时长（秒），拿不到返回 None。"""
    # VideoTask 暂无 video_duration_seconds 字段；先返回 None，
    # ETA 估算会使用粗略默认值。
    return None


def _estimate_progress(
    display_status: str, timeline: list[dict[str, Any]], vt: VideoTask
) -> int:
    """
    粗略估算进度 0~100。基于 7 个阶段中 done 比例。
    """
    if display_status == "done":
        return 100
    if display_status in {"failed"}:
        failed_idx = next(
            (i for i, s in enumerate(timeline) if s["status"] == "failed"), -1
        )
        if failed_idx >= 0:
            return int((failed_idx + 0.5) / max(1, len(timeline)) * 100)
    done = sum(1 for s in timeline if s["status"] == "done")
    running = sum(1 for s in timeline if s["status"] == "running")
    total = max(1, len(timeline))
    base = done / total * 100
    if running:
        base += (0.5 / total) * 100
    return int(min(99, max(0, base)))


def get_algorithm_task_detail(video_id: str) -> dict[str, Any]:
    """
    GET /api/ops/algorithm-tasks/{video_id}

    增强点：
        - log_sources：聚合 algorithm_cli 日志、pipeline_state.json、worker 日志三类来源；
        - latest_log：兜底用 pipeline_state.json 的 command / error_message 填；
        - last_activity_at / last_activity_source：取 video_task.updated_at、
          pipeline_state mtime、algorithm_cli 日志 mtime 的最大值；
        - current_stage / failed_stage：按 pipeline_result → pipeline_state → VideoTask
          优先级回退；
        - files.measurement_csv：候选路径 candidates + 真实存在的 path。
    """
    db = SessionLocal()
    try:
        vt = db.get(VideoTask, video_id)
        if vt is None:
            return {"ok": False, "error": f"VideoTask 不存在：{video_id}"}
        video_dict = _video_task_to_dict(vt)
    finally:
        db.close()

    rq_info = _query_rq_jobs(video_id)
    log_path = find_latest_algorithm_log(video_id)
    log_info = parse_algorithm_log(log_path) if log_path else {}
    log_text = _safe_read_text(log_path) if log_path else ""
    files = _collect_files(video_id)
    pr, pr_err = _read_json(
        RESULTS_DIR / video_id / "reports" / "pipeline_result.json"
    )
    ps, ps_err = _read_json(JOBS_DIR / video_id / "state" / "pipeline_state.json")
    ps_summary = _pipeline_state_summary(video_id)
    worker_log = find_worker_log_tail(video_id)

    timeline = _build_stage_timeline(pr, ps, log_text, rq_info.get("job_state"), vt)
    status_info = _compute_display_status(
        vt, rq_info, log_info, files, timeline, pipeline_state=ps
    )
    display_status = status_info["display_status"]
    is_stuck = status_info["is_stuck"]
    stuck_reason = status_info["reason"]
    last_activity_at = status_info.get("last_activity_at")
    last_activity_source = status_info.get("last_activity_source")
    eta_seconds, eta_text, eta_conf = _compute_eta(
        timeline, vt, video_task_to_duration(vt)
    )


    # 时间归一化：处理 finished_at < started_at 这种时区/旧字段混用异常
    finished_at_normalized, time_anomaly = _normalize_finish_time(vt, ps)
    # current_stage / failed_stage 优先从 pipeline_result / pipeline_state 读取
    current_stage = None
    failed_stage = None
    if isinstance(pr, dict):
        current_stage = pr.get("current_stage")
        failed_stage = pr.get("failed_stage")
    if not current_stage and isinstance(ps, dict):
        current_stage = ps.get("current_stage")
    if not failed_stage and isinstance(ps, dict):
        failed_stage = ps.get("failed_stage")
    if not current_stage:
        current_stage = vt.current_algorithm
    if not failed_stage:
        failed_stage = vt.failed_stage

    # last_log：保持兼容旧字段；algorithm_cli 找不到时 fallback pipeline_state 摘要
    if log_info.get("exists"):
        latest_log = dict(log_info)
        latest_log["source"] = "algorithm_cli"
        latest_log.setdefault("message", "")
    else:
        # 用 pipeline_state 兜底：command / error_message / raw_tail 都来自 ps
        latest_log = {
            "path": ps_summary.get("path") or "",
            "exists": ps_summary.get("exists", False),
            "returncode": None,
            "command": ps_summary.get("command"),
            "stdout_tail": ps_summary.get("raw_tail") or "",
            "stderr_tail": ps_summary.get("error_message") or "",
            "mtime": ps_summary.get("mtime"),
            "parse_error": None,
            "source": "pipeline_state",
            "message": "未找到 algorithm_cli 日志，已用 pipeline_state 兜底。",
        }

    # log_sources
    log_sources: list[dict[str, Any]] = []
    log_sources.append(
        {
            "type": "algorithm_cli",
            "title": "算法 CLI 日志",
            "exists": log_info.get("exists", False),
            "path": log_info.get("path"),
            "mtime": log_info.get("mtime"),
            "returncode": log_info.get("returncode"),
            "command": log_info.get("command"),
            "stdout_tail": log_info.get("stdout_tail") or "",
            "stderr_tail": log_info.get("stderr_tail") or "",
            "message": ""
            if log_info.get("exists")
            else "未在 data/logs/algorithm_cli 最近 100 个 run_video_pipeline.log 中找到包含该 video_id 的日志。",
        }
    )
    log_sources.append(
        {
            "type": "pipeline_state",
            "title": "pipeline_state.json",
            "exists": ps_summary.get("exists", False),
            "path": ps_summary.get("path"),
            "mtime": ps_summary.get("mtime"),
            "status": ps_summary.get("status"),
            "current_stage": ps_summary.get("current_stage"),
            "failed_stage": ps_summary.get("failed_stage"),
            "error_message": ps_summary.get("error_message"),
            "completed_stages": ps_summary.get("completed_stages") or [],
            "command": ps_summary.get("command"),
            "raw_tail": ps_summary.get("raw_tail") or "",
            "message": ps_summary.get("message") or "",
        }
    )
    log_sources.append(
        {
            "type": "worker",
            "title": "Worker 日志",
            "exists": worker_log.get("exists", False),
            "path": worker_log.get("path"),
            "mtime": worker_log.get("mtime"),
            "matched_lines": worker_log.get("matched_lines") or 0,
            "tail": worker_log.get("tail") or "",
            "message": worker_log.get("message") or "",
        }
    )

    diagnosis_lines: list[str] = []
    if "time_anomaly" in dir() and time_anomaly:
        diagnosis_lines.append("时间字段异常（finished_at 早于 started_at），可能存在旧任务状态残留。")
    if is_stuck:
        diagnosis_lines.append(stuck_reason or "任务长时间未推进")
    if vt.status == "failed":
        if vt.last_error:
            diagnosis_lines.append(f"last_error: {vt.last_error[:500]}")
        if vt.failed_stage:
            diagnosis_lines.append(f"failed_stage: {vt.failed_stage}")
    if log_info.get("returncode") and log_info.get("returncode") != 0:
        diagnosis_lines.append(
            f"latest algorithm CLI returncode={log_info.get('returncode')}"
        )
    if not files["playable_h264"]["exists"]:
        diagnosis_lines.append("缺少 playable H.264 MP4，可能上游没成功生成。")

    return {
        "ok": True,
        "data": {
            "video": video_dict,
            "queue": rq_info,
            "files": files,
            "pipeline_state": {
                "raw": ps,
                "parse_error": ps_err,
                "summary": ps_summary,
            },
            "pipeline_result": {
                "raw": pr,
                "parse_error": pr_err,
            },
            "stages": timeline,
            "display_status": display_status,
            "is_stuck": is_stuck,
            "stuck_reason": stuck_reason,
            "current_stage": current_stage,
            "failed_stage": failed_stage,
            "last_activity_at": last_activity_at,
            "time_anomaly": time_anomaly,
            "finished_at_normalized": finished_at_normalized.isoformat() if finished_at_normalized else None,
            "last_activity_source": last_activity_source,
            "eta_seconds": eta_seconds,
            "eta_text": eta_text,
            "eta_confidence": eta_conf,
            "latest_log": latest_log,
            "log_sources": log_sources,
            "diagnosis": {
                "is_stuck": is_stuck,
                "reason": "\n".join(diagnosis_lines) if diagnosis_lines else "",
                "suggestion": _suggest_action(
                    display_status, is_stuck, vt, log_info, current_stage
                ),
            },
            "model_runtime": {
                "mode": "cli",
                "preload_supported": False,
                "loaded": False,
                "message": (
                    "当前算法以 CLI 子进程方式运行；若要常驻显存需后续改造。"
                ),
                "gpu": _read_gpu_info(),
            },
        },
    }


def _suggest_action(
    display_status: str,
    is_stuck: bool,
    vt: VideoTask,
    log_info: dict[str, Any],
    current_stage: Optional[str] = None,
) -> str:
    if display_status == "done":
        return "已成功。"
    if display_status == "failed":
        return "请查看 latest_log.stderr_tail / last_error；如果想重新跑，可调用 retry 接口。"
    if is_stuck:
        msg = (
            "数据库状态长时间未推进，但 RQ 没有 running job。"
            "可调用 mark-failed 接口先标记为 failed；或先清理 data/jobs/<video_id> 后调用 retry。"
        )
        if current_stage:
            msg += f" 上次记录到的运行阶段：{current_stage}。"
        return msg
    if display_status == "processing":
        return "请稍后刷新；RQ 仍有 running job。"
    if display_status == "queued":
        return "等待 worker 拉起。"
    return "请结合 latest_log 排查。"


__all__ = [
    "STAGES",
    "STAGE_KEYWORDS",
    "STUCK_THRESHOLD_SECONDS",
    "find_latest_algorithm_log",
    "find_worker_log_tail",
    "parse_algorithm_log",
    "_pipeline_state_summary",
    "get_algorithm_summary",
    "list_algorithm_tasks",
    "get_algorithm_task_detail",
]