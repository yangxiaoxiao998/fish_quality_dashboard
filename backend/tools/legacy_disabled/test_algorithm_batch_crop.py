"""
test_algorithm_batch_crop.py - 算法 crop/mask 批处理最小验证脚本

输入：
    --video-id        必填或自动从 batch_input_service 推断
    --source          original | perspective | corrected
    --limit           最多处理多少张图
    --crop-mode       tracked_unique | frame_each (默认 tracked_unique)
    --save-debug-images
    --fallback-to-frame-each

行为：
    - 通过 batch_input_service.image_root_for_source 找到 frame_dir。
    - 调用 fishalgo.measures.fish_segment.crop_fish_images_from_frames。
    - 只做 crop + mask 阶段，不做五维、不入库、不连数据库。
    - tracked_unique 模式额外打印 unique_fish_count / duplicate_track_ids。

不在该脚本中：
    - 连接 PostgreSQL / Redis
    - 写绝对路径
    - 启动 FastAPI / RQ worker
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
ALGO_DIR = PROJECT_ROOT / "algorithms"

for p in (str(BACKEND_DIR), str(ALGO_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("FISH_PROJECT_ROOT", str(PROJECT_ROOT))
os.environ.setdefault("FISH_ALGO_ROOT", str(ALGO_DIR))
os.environ.setdefault("FISH_DATA_ROOT", str(PROJECT_ROOT / "data"))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Algorithm batch crop/mask smoke test."
    )
    p.add_argument(
        "--video-id",
        dest="video_id",
        default=None,
        help="target video_id. If omitted, picks the first one from list_video_ids().",
    )
    p.add_argument(
        "--source",
        dest="source",
        default="perspective",
        choices=["original", "perspective", "corrected"],
        help="image source type (default: perspective)",
    )
    p.add_argument(
        "--limit",
        dest="limit",
        type=int,
        default=5,
        help="max number of input frames to process (default: 5)",
    )
    p.add_argument(
        "--crop-mode",
        dest="crop_mode",
        default="tracked_unique",
        choices=["tracked_unique", "frame_each"],
        help="crop mode (default: tracked_unique)",
    )
    p.add_argument(
        "--save-debug-images",
        dest="save_debug_images",
        action="store_true",
        help="also save matplotlib debug _grounded_sam.jpg images (slow, needs matplotlib)",
    )
    p.add_argument(
        "--fallback-to-frame-each",
        dest="fallback_to_frame_each",
        action="store_true",
        help="fallback to frame_each mode if tracked_unique returns 0",
    )
    p.add_argument(
        "--allow-fallback",
        dest="allow_fallback",
        action="store_true",
        default=True,
        help="allow fallback detector when real model is unavailable",
    )
    p.add_argument(
        "--no-allow-fallback",
        dest="allow_fallback",
        action="store_false",
        help="disable fallback detector",
    )
    return p.parse_args(argv)


def _scan_dir(root: Path, video_id: str) -> int:
    if not root.exists():
        return 0
    n = 0
    for p in root.rglob("*"):
        if p.is_file() and video_id in str(p):
            n += 1
    return n


def main(argv: list[str]) -> int:
    args = _parse_args(argv)

    from app.services.batch_input_service import (
        collect_video_images,
        image_root_for_source,
        list_video_ids,
    )
    from fishalgo.measures.fish_segment import crop_fish_images_from_frames

    video_id = args.video_id
    if not video_id:
        ids = list_video_ids(source=args.source)
        if not ids:
            print(f"[INFO] no video ids found for source={args.source!r}")
            print("video_id  = <none>")
            print("source    =", args.source)
            print("count     = 0")
            return 0
        video_id = ids[-1]

    from app.paths import DATA_DIR

    frame_dir = image_root_for_source(video_id, args.source, data_dir=DATA_DIR)

    preview_items = collect_video_images(
        video_id=video_id,
        source=args.source,
        data_dir=DATA_DIR,
        limit=args.limit,
        fallback_to_original=(args.source == "perspective"),
    )

    # 报告起始
    report: dict[str, Any] = {
        "video_id": video_id,
        "source": args.source,
        "crop_mode": args.crop_mode,
        "limit": args.limit,
        "save_debug_images": args.save_debug_images,
        "fallback_to_frame_each": args.fallback_to_frame_each,
        "frame_dir": str(frame_dir),
        "input_image_count": len(preview_items),
    }

    print("========== config ==========")
    print(f"video_id           = {video_id}")
    print(f"source             = {args.source}")
    print(f"crop_mode          = {args.crop_mode}")
    print(f"limit              = {args.limit}")
    print(f"save_debug_images  = {args.save_debug_images}")
    print(f"fallback_to_frame_each = {args.fallback_to_frame_each}")
    print(f"frame_dir          = {frame_dir}")
    print(
        f"input_image_count  = {min(args.limit, len(preview_items)) if preview_items else 0}"
    )

    if not frame_dir.exists():
        print(f"[WARN] frame_dir does not exist: {frame_dir}")
        print("count = 0")
        report["status"] = "SKIP"
        report["reason"] = "frame_dir does not exist"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    apply_perspective = args.source != "perspective"

    crop_items = crop_fish_images_from_frames(
        video_id=video_id,
        frame_dir=str(frame_dir),
        output_dir=None,
        limit=args.limit,
        apply_perspective=apply_perspective,
        allow_fallback=args.allow_fallback,
        crop_mode=args.crop_mode,
        save_debug_images=args.save_debug_images,
        fallback_to_frame_each=args.fallback_to_frame_each,
        input_source=args.source,
    )

    # 统计
    track_ids: list[str] = []
    for it in crop_items:
        tid = it.get("track_id")
        if tid is not None and str(tid) != "None":
            track_ids.append(str(tid))
    track_counter = Counter(track_ids)
    duplicate_track_ids = {tid: cnt for tid, cnt in track_counter.items() if cnt > 1}

    fish_id_patterns = Counter()
    for it in crop_items:
        fid = it.get("fish_id") or ""
        if "_track_" in fid:
            fish_id_patterns["video_track"] += 1
        elif "_frame_" in fid:
            fish_id_patterns["video_frame_N_fish_N"] += 1
        else:
            fish_id_patterns["other"] += 1

    unique_fish_count = len(set(track_ids)) if track_ids else 0
    track_id_count = len(track_ids)

    # 目录扫描
    y, m, d = "2026", "06", "24"
    if len(video_id) >= 8 and video_id[:8].isdigit():
        y, m, d = video_id[:4], video_id[4:6], video_id[6:8]

    crop_root = DATA_DIR / "results" / "crop_images" / y / m / d / video_id
    mask_root = DATA_DIR / "results" / "mask_images" / y / m / d / video_id
    crop_count = _scan_dir(crop_root, video_id)
    mask_count = _scan_dir(mask_root, video_id)

    report.update(
        {
            "status": "PASS" if (crop_count > 0 and len(duplicate_track_ids) == 0) else "FAIL",
            "fish_count": len(crop_items),
            "unique_fish_count": unique_fish_count,
            "track_id_count": track_id_count,
            "duplicate_track_ids": duplicate_track_ids,
            "fish_id_patterns": dict(fish_id_patterns),
            "crop_count": crop_count,
            "mask_count": mask_count,
            "sample": [
                {
                    "fish_id": it.get("fish_id"),
                    "track_id": it.get("track_id"),
                    "frame_index": it.get("frame_index"),
                    "crop_image_path": it.get("crop_image_path"),
                    "mask_image_path": it.get("mask_image_path"),
                    "source_frame_path": it.get("source_frame_path"),
                    "detector": it.get("detector"),
                }
                for it in crop_items[:5]
            ],
        }
    )

    print()
    print("========== result ==========")
    print(f"crop_count           = {crop_count}")
    print(f"mask_count           = {mask_count}")
    print(f"fish_count           = {len(crop_items)}")
    print(f"unique_fish_count    = {unique_fish_count}")
    print(f"track_id_count       = {track_id_count}")
    print(f"duplicate_track_ids  = {duplicate_track_ids}")
    print(f"fish_id_patterns     = {dict(fish_id_patterns)}")
    print(f"detector             = {crop_items[0].get('detector') if crop_items else None}")
    print()
    for idx, s in enumerate(report["sample"], start=1):
        print(
            f"  - [{idx}] fish_id={s['fish_id']!r} "
            f"track_id={s['track_id']!r} frame_index={s['frame_index']!r}"
        )
        print(f"        crop={s['crop_image_path']!r}")
        print(f"        mask={s['mask_image_path']!r}")
        print(f"        src ={s['source_frame_path']!r}")

    print()
    print("========== json report ==========")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if duplicate_track_ids:
        print(
            f"[FAIL] duplicate track_ids found: {list(duplicate_track_ids.keys())}"
        )
        return 2

    if crop_count == 0:
        print("[FAIL] no crop files written")
        return 1

    print("[OK] tracked_unique mode produces unique track_ids")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
