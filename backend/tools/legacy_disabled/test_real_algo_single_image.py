from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def _env_path(name: str) -> Path | None:
    value = os.getenv(name)
    if not value:
        return None
    return Path(value).expanduser().resolve()


# 当前文件：
#   <project>/backend/tools/test_real_algo_single_image.py
# 所以：
#   PROJECT_ROOT = parents[2]
DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]

PROJECT_ROOT = _env_path("FISH_PROJECT_ROOT") or DEFAULT_PROJECT_ROOT
BACKEND_ROOT = PROJECT_ROOT / "backend"
ALGO_ROOT = _env_path("FISH_ALGO_ROOT") or (PROJECT_ROOT / "algorithms").resolve()
DATA_ROOT = _env_path("FISH_DATA_ROOT") or (PROJECT_ROOT / "data").resolve()

sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(ALGO_ROOT))


def title(name: str) -> None:
    print("\n" + "=" * 20 + f" {name} " + "=" * 20)


def json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)

    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]

    try:
        import numpy as np

        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        if isinstance(value, np.ndarray):
            return value.tolist()
    except Exception:
        pass

    return value


def find_latest_image() -> Path | None:
    roots = [
        DATA_ROOT / "uploads" / "original_images",
        DATA_ROOT / "results" / "perspective_images",
        DATA_ROOT / "uploads",
    ]

    suffixes = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        candidates.extend(
            p for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in suffixes
        )

    if not candidates:
        return None

    return max(candidates, key=lambda p: p.stat().st_mtime)


def make_video_id() -> str:
    return datetime.now().strftime("%Y%m%d_test_%H%M%S")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--image",
        type=str,
        default=None,
        help="待测试的单张图片路径。不填则自动查找 data/jobs/<video_id>/frames/raw/ 下最新图片。",
    )
    parser.add_argument(
        "--video-id",
        type=str,
        default=None,
        help="测试用 video_id。不填则自动生成。",
    )
    parser.add_argument(
        "--frame-index",
        type=int,
        default=1,
        help="测试用 frame_index。",
    )
    parser.add_argument(
        "--max-fish",
        type=int,
        default=5,
        help="最多测试几条鱼。",
    )
    parser.add_argument(
        "--no-perspective",
        action="store_true",
        help="关闭整帧视角矫正。",
    )

    args = parser.parse_args()

    os.environ.setdefault("FISH_PROJECT_ROOT", str(PROJECT_ROOT))
    os.environ.setdefault("FISH_ALGO_ROOT", str(ALGO_ROOT))
    os.environ.setdefault("FISH_DATA_ROOT", str(DATA_ROOT))

    title("Paths")
    print("PROJECT_ROOT:", PROJECT_ROOT)
    print("ALGO_ROOT:", ALGO_ROOT)
    print("DATA_ROOT:", DATA_ROOT)
    print("python:", sys.executable)

    if args.image:
        image_path = Path(args.image).expanduser().resolve()
    else:
        image_path = find_latest_image()

    if image_path is None:
        print("[FAIL] 没有找到可测试图片，请使用 --image 指定一张鱼图。")
        return 1

    if not image_path.exists():
        print(f"[FAIL] 图片不存在: {image_path}")
        return 1

    video_id = args.video_id or make_video_id()

    title("Input")
    print("image:", image_path)
    print("video_id:", video_id)
    print("frame_index:", args.frame_index)

    title("Import Algorithms")
    try:
        from fishalgo.measures import (
            segment_fish_from_frame,
            correct_fish_image,
            calculate_fish_length_height,
            calculate_fish_weight,
            calculate_fish_condition_factor,
            analyze_fish_surface_damage_with_ratio,
        )

        print("[OK] fishalgo.measures import")
    except Exception as e:
        print("[FAIL] import algorithms:", repr(e))
        return 1

    title("Step 1: Segment")
    try:
        segment_items = segment_fish_from_frame(
            video_id=video_id,
            frame_path=str(image_path),
            frame_index=args.frame_index,
            apply_perspective=not args.no_perspective,
            max_fish=args.max_fish,
        )
    except Exception as e:
        print("[FAIL] segment_fish_from_frame:", repr(e))
        return 1

    print("segment count:", len(segment_items))

    if not segment_items:
        print("[OK] 未检测到鱼。")
        print("这不是程序错误。如果当前图片是空景，真实算法返回 0 是正确结果。")
        return 0

    all_results: list[dict[str, Any]] = []

    for idx, item in enumerate(segment_items, start=1):
        title(f"Fish {idx}")

        fish_id = item.get("fish_id") or f"{video_id}_fish_{idx:06d}"
        crop_image_path = item.get("crop_image_path")
        mask_image_path = item.get("mask_image_path")

        print("fish_id:", fish_id)
        print("crop:", crop_image_path)
        print("mask:", mask_image_path)
        print("bbox:", item.get("bbox"))
        print("confidence:", item.get("confidence"))

        if not crop_image_path:
            print("[WARN] 缺少 crop_image_path，跳过")
            continue

        fish_result: dict[str, Any] = {
            "fish_id": fish_id,
            "segment": item,
            "correct": None,
            "metrics": None,
            "weight": None,
            "condition_factor": None,
            "wound": None,
            "error": None,
        }

        try:
            corrected = correct_fish_image(
                video_id=video_id,
                fish_id=fish_id,
                crop_image_path=crop_image_path,
                mask_image_path=mask_image_path,
                frame_index=item.get("frame_index") or args.frame_index,
            )
            fish_result["correct"] = corrected
            corrected_image_path = corrected.get("corrected_image_path")
            print("corrected:", corrected_image_path)

            if not corrected_image_path:
                raise RuntimeError("corrected_image_path is empty")

            metrics = calculate_fish_length_height(
                corrected_image_path,
                mask_image_path=mask_image_path,
            )
            fish_result["metrics"] = metrics
            print("metrics:", metrics)

            body_length = metrics.get("body_length")
            body_height = metrics.get("body_height")
            body_length_without_tail = metrics.get("body_length_without_tail")

            weight = calculate_fish_weight(
                corrected_image_path,
                body_length=metrics.get("body_length"),
                body_length_without_tail=metrics.get("body_length_without_tail"),
                body_height=metrics.get("body_height"),
            )
            fish_result["weight"] = weight
            print("weight:", weight)

            condition_factor = calculate_fish_condition_factor(
                body_length=body_length,
                body_length_without_tail=body_length_without_tail,
                body_weight=weight,
                use_without_tail=False,
            )
            fish_result["condition_factor"] = condition_factor
            print("condition_factor:", condition_factor)

            wound = analyze_fish_surface_damage_with_ratio(
                corrected_image_path,
                mask_image_path=mask_image_path,
                video_id=video_id,
                fish_id=fish_id,
                frame_index=item.get("frame_index") or args.frame_index,
            )
            fish_result["wound"] = wound
            print("wound:", wound)

        except Exception as e:
            print("[FAIL] fish pipeline error:", repr(e))
            fish_result["error"] = repr(e)

        all_results.append(fish_result)

    title("Save Result")
    output_dir = DATA_ROOT / "test_outputs" / "real_algo_single_image" / video_id
    output_dir.mkdir(parents=True, exist_ok=True)

    result_path = output_dir / "result.json"

    payload = {
        "project_root": str(PROJECT_ROOT),
        "algo_root": str(ALGO_ROOT),
        "data_root": str(DATA_ROOT),
        "image_path": str(image_path),
        "video_id": video_id,
        "frame_index": args.frame_index,
        "fish_count": len(all_results),
        "items": all_results,
    }

    result_path.write_text(
        json.dumps(json_safe(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("result:", result_path)

    title("Done")
    print("single image real algorithm test finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
