"""
test_post_video_pipeline.py - 真实 POST 视频端到端测试

行为：
    1. POST 一个本地视频到 /api/videos
    2. 轮询真实 PostgreSQL，查看 video_task 和 fish 表
    3. 打印 RQ worker log 尾部
    4. 打印输出目录样例
    5. 根据结果返回不同 exit code

不在该脚本中：
    - 改业务代码
    - 启动 RQ worker / FastAPI
    - 写绝对路径

参数：
    --video-path        默认 data/uploads/videos/video_25fps_h264.mp4
    --api-url           默认 http://127.0.0.1:8000/api/videos
    --timeout           默认 7200 秒
    --poll-interval     默认 5 秒
    --tail-lines        默认 160
    --expect-fish       默认 1
    --no-wait           上传后不轮询
    --print-worker-log  每次轮询都打印 worker log 尾部
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(
    os.getenv("FISH_PROJECT_ROOT", Path(__file__).absolute().parents[2])
).expanduser().absolute()
BACKEND_DIR = PROJECT_ROOT / "backend"
DATA_DIR = Path(
    os.getenv("FISH_DATA_ROOT", PROJECT_ROOT / "data")
).expanduser().absolute()

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.repositories.db import SessionLocal
from app.repositories.models import Fish, VideoTask

TERMINAL_STATUSES = {"done", "failed", "stopped", "no_fish_detected"}


def _rel(path: Path) -> str:
    try:
        return path.relative_to(DATA_DIR).as_posix()
    except Exception:
        return str(path)


def _date_parts(video_id: str) -> tuple[str, str, str]:
    if len(video_id) < 8 or not video_id[:8].isdigit():
        return "unknown", "unknown", "unknown"
    return video_id[:4], video_id[4:6], video_id[6:8]


def tail_file(path: Path, lines: int = 160) -> str:
    if not path.exists():
        return f"[WARN] log not found: {path}"
    try:
        data = path.read_text(errors="replace").splitlines()
    except Exception as e:
        return f"[WARN] cannot read log: {path}, error={e!r}"
    return "\n".join(data[-lines:])


def list_samples(root: Path, limit: int = 10) -> list[str]:
    if not root.exists():
        return []
    files = sorted(
        [p for p in root.rglob("*") if p.is_file()],
        key=lambda p: p.as_posix(),
    )
    return [_rel(p) for p in files[:limit]]


def post_video(api_url: str, video_path: Path) -> dict[str, Any]:
    with video_path.open("rb") as f:
        resp = requests.post(
            api_url,
            files={"file": (video_path.name, f, "video/mp4")},
            timeout=600,
        )

    print("[POST] status_code =", resp.status_code)
    print("[POST] response     =", resp.text[:3000])

    resp.raise_for_status()
    return resp.json()


def query_state(video_id: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        task = db.get(VideoTask, video_id)

        fish_base = db.query(Fish).filter(Fish.video_id == video_id)
        fish_count = fish_base.count()
        done_count = fish_base.filter(Fish.status == "done").count()
        failed_count = fish_base.filter(Fish.status == "failed").count()
        partial_count = fish_base.filter(Fish.status == "partial").count()

        fishes = (
            db.query(Fish)
            .filter(Fish.video_id == video_id)
            .order_by(Fish.created_at.desc())
            .limit(10)
            .all()
        )

        return {
            "task_exists": task is not None,
            "status": getattr(task, "status", None),
            "total_fish": getattr(task, "total_fish", None),
            "processed_fish": getattr(task, "processed_fish", None),
            "last_error": getattr(task, "last_error", None),
            "current_algorithm": getattr(task, "current_algorithm", None),
            "current_fish_id": getattr(task, "current_fish_id", None),
            "fish_count": fish_count,
            "done_count": done_count,
            "failed_count": failed_count,
            "partial_count": partial_count,
            "sample_fish": [
                {
                    "fish_id": f.fish_id,
                    "status": f.status,
                    "body_length": f.body_length,
                    "body_height": f.body_height,
                    "body_weight": f.body_weight,
                    "condition_factor": f.condition_factor,
                    "surface_damage_ratio": getattr(f, "surface_damage_ratio", None),
                    "error_msg": f.error_msg,
                    "original_image_path": f.original_image_path,
                    "corrected_image_path": f.corrected_image_path,
                    "mask_image_path": getattr(f, "mask_image_path", None),
                    "wound_mask_path": getattr(f, "wound_mask_path", None),
                }
                for f in fishes
            ],
        }
    finally:
        db.close()


def print_output_samples(video_id: str) -> None:
    y, m, d = _date_parts(video_id)

    roots = [
        ("uploads/videos", DATA_DIR / "uploads" / "videos", False),
        ("original_images", DATA_DIR / "uploads" / "original_images" / y / m / d / video_id, True),
        ("perspective_images", DATA_DIR / "results" / "perspective_images" / y / m / d / video_id, True),
        ("crop_images", DATA_DIR / "results" / "crop_images" / y / m / d / video_id, True),
        ("mask_images", DATA_DIR / "results" / "mask_images" / y / m / d / video_id, True),
        ("corrected_images", DATA_DIR / "results" / "corrected_images" / y / m / d / video_id, True),
        ("wound_masks", DATA_DIR / "results" / "wound_masks", False),
        ("wound_annotated", DATA_DIR / "results" / "wound_annotated", False),
    ]

    print("\n========== output samples ==========")
    counts: dict[str, int] = {}
    for label, root, _only_video in roots:
        if root.exists():
            all_files = [
                pp for pp in root.rglob("*") if pp.is_file() and video_id in str(pp)
            ]
            all_files.sort(key=lambda pp: pp.as_posix())
            samples = [_rel(p) for p in all_files[:10]]
            count = len(all_files)
        else:
            samples = []
            count = 0

        counts[label] = count
        print(f"\n--- {label} ({_rel(root)}) ---")
        print(f"exists={root.exists()} sample_count={len(samples)} total_count_for_video={count}")
        for s in samples:
            print(" ", s)

    return counts


def print_final_summary(state: dict[str, Any], counts: dict[str, int]) -> None:
    print("\n========== final summary ==========")
    print(f"  status                = {state.get('status')}")
    print(f"  total_fish            = {state.get('total_fish')}")
    print(f"  processed_fish        = {state.get('processed_fish')}")
    print(f"  last_error            = {state.get('last_error')!r}")
    print(f"  fish_count            = {state.get('fish_count')}")
    print(f"  done_count            = {state.get('done_count')}")
    print(f"  failed_count          = {state.get('failed_count')}")
    print(f"  partial_count         = {state.get('partial_count')}")
    print()
    print(f"  original_count        = {counts.get('original_images', 0)}")
    print(f"  perspective_count     = {counts.get('perspective_images', 0)}")
    print(f"  crop_count            = {counts.get('crop_images', 0)}")
    print(f"  mask_count            = {counts.get('mask_images', 0)}")
    print(f"  corrected_count       = {counts.get('corrected_images', 0)}")
    print(f"  wound_mask_count      = {counts.get('wound_masks', 0)}")
    print(f"  wound_annotated_count = {counts.get('wound_annotated', 0)}")

    status = state.get("status")
    fish_count = int(state.get("fish_count") or 0)
    done_count = int(state.get("done_count") or 0)
    corrected_count = counts.get("corrected_images", 0)

    if status == "done" and fish_count >= 1 and done_count >= 1 and corrected_count >= 1:
        print("\n[RESULT] PASS")
    elif status == "done" and fish_count == 0:
        print("\n[RESULT] NO_FISH")
    elif status == "no_fish_detected":
        print("\n[RESULT] NO_FISH")
    elif status == "failed":
        print("\n[RESULT] FAIL")
    else:
        print(f"\n[RESULT] OTHER (status={status})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--video-path",
        default=str(DATA_DIR / "uploads" / "videos" / "video_25fps_h264.mp4"),
    )
    parser.add_argument("--api-url", default="http://127.0.0.1:8000/api/videos")
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--poll-interval", type=int, default=5)
    parser.add_argument("--tail-lines", type=int, default=160)
    parser.add_argument("--expect-fish", type=int, default=1)
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--print-worker-log", action="store_true")
    args = parser.parse_args()

    video_path = Path(args.video_path).expanduser()
    if not video_path.is_absolute():
        video_path = (PROJECT_ROOT / video_path).absolute()

    if not video_path.exists():
        print(f"[ERROR] video not found: {video_path}")
        return 10

    print("========== config ==========")
    print("PROJECT_ROOT_LOGICAL =", PROJECT_ROOT)
    print("PROJECT_ROOT_REAL    =", PROJECT_ROOT.resolve())
    print("DATA_DIR_LOGICAL     =", DATA_DIR)
    print("DATA_DIR_REAL        =", DATA_DIR.resolve())
    print("video_path           =", video_path)
    print("video_path_real      =", video_path.resolve())
    print("api_url              =", args.api_url)

    data = post_video(args.api_url, video_path)

    video_id = data.get("video_id")
    job_id = data.get("job_id")

    print("[POST] video_id    =", video_id)
    print("[POST] job_id      =", job_id)
    print("[POST] frame_count =", data.get("frame_count"))
    print("[POST] status      =", data.get("status"))
    print("[POST] enqueued    =", data.get("enqueued"))
    print("[POST] queue_size_before =", data.get("queue_size_before_enqueue"))
    print("[POST] queue_size_after  =", data.get("queue_size_after_enqueue"))

    if not video_id:
        print("[ERROR] response has no video_id")
        return 11

    if args.no_wait:
        print_output_samples(video_id)
        return 0

    start = time.time()
    worker_log = DATA_DIR / "logs" / "rq_worker.log"
    last_state = None
    counts: dict[str, int] = {}

    while True:
        elapsed = time.time() - start
        state = query_state(video_id)
        last_state = state

        print(
            f"[POLL] elapsed={elapsed:.1f}s "
            f"status={state.get('status')} "
            f"total={state.get('total_fish')} "
            f"processed={state.get('processed_fish')} "
            f"fish_count={state.get('fish_count')} "
            f"done={state.get('done_count')} "
            f"failed={state.get('failed_count')} "
            f"partial={state.get('partial_count')} "
            f"current={state.get('current_algorithm')} "
            f"error={state.get('last_error')!r}"
        )

        if args.print_worker_log:
            print("\n----- worker log tail -----")
            print(tail_file(worker_log, min(args.tail_lines, 80)))

        status = state.get("status")
        if status in TERMINAL_STATUSES:
            break

        if elapsed > args.timeout:
            print("[ERROR] timeout waiting for pipeline")
            counts = print_output_samples(video_id)
            print("\n========== final db state ==========")
            print(json.dumps(last_state, ensure_ascii=False, indent=2))
            print("\n========== worker log tail ==========")
            print(tail_file(worker_log, args.tail_lines))
            print_final_summary(last_state, counts)
            return 4

        time.sleep(args.poll_interval)

    counts = print_output_samples(video_id)

    print("\n========== final db state ==========")
    print(json.dumps(last_state, ensure_ascii=False, indent=2))

    print("\n========== worker log tail ==========")
    print(tail_file(worker_log, args.tail_lines))

    print_final_summary(last_state, counts)

    status = last_state.get("status")
    fish_count = int(last_state.get("fish_count") or 0)
    done_count = int(last_state.get("done_count") or 0)
    corrected_count = counts.get("corrected_images", 0)

    if status == "failed":
        print("[ERROR] pipeline failed")
        return 3

    if status == "no_fish_detected":
        print("[WARN] pipeline finished but no fish detected")
        return 6

    if status == "done" and fish_count >= args.expect_fish and done_count >= 1 and corrected_count >= 1:
        print("[OK] pipeline done")
        return 0

    if status == "done" and fish_count == 0:
        print(f"[WARN] pipeline done but fish_count=0")
        return 6

    if status == "done":
        print(
            f"[ERROR] pipeline done but fish_count={fish_count} done={done_count} "
            f"corrected={corrected_count} (expect_fish={args.expect_fish})"
        )
        return 2

    print(f"[WARN] terminal status={status}")
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
