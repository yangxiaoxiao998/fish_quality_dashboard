# 输入：video_id、frame_dir、output_json
# 运行：fish_segment.crop_fish_images_from_frames()
# 输出：crop_items.json
# 退出：释放 GroundingDINO / SAM 占用的内存

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any


def _json_safe_value(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, (list, tuple)):
        return [_json_safe_value(v) for v in value]

    if isinstance(value, dict):
        return {
            str(k): _json_safe_value(v)
            for k, v in value.items()
            if not str(k).startswith("_")
        }

    # numpy 标量
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass

    # 不把大数组、图像、mask tensor 写进 json
    if hasattr(value, "shape"):
        return None

    return str(value)


def _sanitize_crop_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    keys = [
        "fish_id",
        "track_id",
        "bbox",
        "confidence",
        "frame_index",
        "crop_image_path",
        "mask_image_path",
        "source_frame_path",
        "perspective_frame_path",
        "crop_background",
        "crop_format",
    ]

    out = {}
    for key in keys:
        if key in item:
            out[key] = _json_safe_value(item.get(key))

    if not out.get("fish_id") or not out.get("crop_image_path"):
        return None

    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--frame-dir", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    backend_dir = project_root / "backend"
    algo_dir = project_root / "algorithms"
    grounding_dino_dir = algo_dir / "GroundingDINO"

    for p in [backend_dir, algo_dir, grounding_dino_dir]:
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    os.environ.setdefault("FISH_PROJECT_ROOT", str(project_root))
    os.environ.setdefault("FISH_ALGO_ROOT", str(algo_dir))
    os.environ.setdefault("FISH_DATA_ROOT", str(project_root / "data"))
    os.environ.setdefault("FISH_ALGO_MODE", "real")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)

    try:
        from fishalgo.measures.fish_segment import crop_fish_images_from_frames

        print(
            f"[subprocess-crop] start: "
            f"video_id={args.video_id!r}, frame_dir={args.frame_dir!r}"
        )

        crop_items = crop_fish_images_from_frames(
            video_id=args.video_id,
            frame_dir=args.frame_dir,
            output_dir=args.output_dir or None,
        )

        safe_items = []
        for item in crop_items or []:
            safe = _sanitize_crop_item(item)
            if safe is not None:
                safe_items.append(safe)

        output_json.write_text(
            json.dumps(safe_items, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(
            f"[subprocess-crop] done: "
            f"video_id={args.video_id!r}, items={len(safe_items)}, "
            f"output_json={str(output_json)!r}"
        )
        return 0

    except Exception:
        print("[subprocess-crop] failed")
        traceback.print_exc()
        return 1

    finally:
        # FISH_FREE_MEMORY_EACH_FRAME only frees temporary tensors; it must
        # not clear model caches per frame. Models are loaded once at startup
        # (GroundingDINO + SAM) and reused across all images in this video.
        # This subprocess exits naturally after the job, so model memory
        # is released by the OS, not by clearing caches.
        try:
            import gc
            gc.collect()
        except Exception:
            pass

        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())