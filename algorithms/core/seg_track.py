"""
    输入：视频 模型
    输出：每个ID第一次出现的原始裁剪图像和二值掩码裁剪图像
    运行命令：
    python seg_track.py --source <视频路径> --weights <模型路径> --output <输出目录> --device <设备号> --tracker <跟踪器配置文件> --conf <置信度阈值> --imgsz <图像尺寸> --max-frames <最大帧数>
"""

import argparse
import math
import shutil
import sys
import warnings
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_WEIGHTS = SCRIPT_DIR / "runs" / "fish_yolo26n_seg" / "weights" / "best.pt"
DEFAULT_SOURCE = SCRIPT_DIR / "video" / "fish_video.mp4"
DEFAULT_OUTPUT = SCRIPT_DIR / "track_first_match_crop_mask"
DEFAULT_IMGSZ = 640
LEFT_EDGE_IGNORE_RATIO = 0.02
RIGHT_EDGE_IGNORE_RATIO = 0.02
VIDEO_SUFFIXES = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Track fish instances and save first-seen per-ID original crops with binary mask crops."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="Video, image, or image directory.")
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS, help="YOLO segmentation weights.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Directory for first-seen ID crop/mask pairs.")
    parser.add_argument("--device", default="1", help="Inference device, e.g. 1, 0, cpu.")
    parser.add_argument("--tracker", default="bytetrack.yaml", help="Ultralytics tracker config.")
    parser.add_argument("--conf", type=float, default=0.25, help="Detection confidence threshold.")
    parser.add_argument("--imgsz", type=int, default=DEFAULT_IMGSZ, help="Inference image size.")
    parser.add_argument("--max-frames", type=int, default=None, help="Optional frame limit for quick tests.")
    return parser.parse_args()


def require_path(path, description):
    if not path.exists():
        raise FileNotFoundError(f"Cannot find {description}: {path}")


def validate_args(args):
    require_path(args.source, "tracking source")
    require_path(args.weights, "model weights")
    if args.conf < 0.0 or args.conf > 1.0:
        raise ValueError("--conf must be between 0.0 and 1.0")
    if args.imgsz <= 0:
        raise ValueError("--imgsz must be greater than 0")
    if args.max_frames is not None and args.max_frames <= 0:
        raise ValueError("--max-frames must be greater than 0")


def to_numpy(value):
    if value is None:
        return None
    if hasattr(value, "detach"):
        return value.detach().cpu().numpy()
    return np.asarray(value)


def normalize_track_ids(track_ids, count):
    if track_ids is None:
        return list(range(count))

    normalized = []
    for index in range(count):
        track_id = track_ids[index] if index < len(track_ids) else None
        if track_id is None:
            normalized.append(index)
            continue

        track_value = int(track_id)
        normalized.append(index if track_value < 0 else track_value)
    return normalized


def polygon_to_pixels(polygon, width, height):
    polygon = np.asarray(polygon, dtype=np.float32).copy()
    if polygon.ndim != 2 or polygon.shape[0] < 3 or polygon.shape[1] != 2:
        return None

    polygon[:, 0] = np.clip(polygon[:, 0], 0, width - 1)
    polygon[:, 1] = np.clip(polygon[:, 1], 0, height - 1)
    return np.rint(polygon).astype(np.int32)


def mask_from_polygon(polygon, image_shape):
    height, width = image_shape[:2]
    polygon = polygon_to_pixels(polygon, width, height)
    if polygon is None:
        return None

    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [polygon], 255)
    return mask.astype(bool)


def mask_touches_horizontal_edges(mask_bool):
    image_height, image_width = mask_bool.shape[:2]
    if image_width <= 0 or image_height <= 0:
        return False

    left_width = max(1, math.ceil(image_width * LEFT_EDGE_IGNORE_RATIO)) if LEFT_EDGE_IGNORE_RATIO > 0 else 0
    right_width = max(1, math.ceil(image_width * RIGHT_EDGE_IGNORE_RATIO)) if RIGHT_EDGE_IGNORE_RATIO > 0 else 0

    if left_width > 0 and mask_bool[:, :left_width].any():
        return True
    if right_width > 0 and mask_bool[:, max(0, image_width - right_width):].any():
        return True

    if left_width > 0:
        mask_bool[:, :left_width] = False
    if right_width > 0:
        mask_bool[:, max(0, image_width - right_width):] = False
    return bool(not np.any(mask_bool))


def crop_original_and_mask(image, mask_bool):
    ys, xs = np.where(mask_bool)
    if ys.size == 0 or xs.size == 0:
        return None, None

    y_min, y_max = int(ys.min()), int(ys.max())
    x_min, x_max = int(xs.min()), int(xs.max())

    scale = 1.2
    height = y_max - y_min + 1
    width = x_max - x_min + 1
    center_y = y_min + height / 2.0
    center_x = x_min + width / 2.0

    new_height = max(1, int(round(height * scale)))
    new_width = max(1, int(round(width * scale)))

    new_y_min = int(round(center_y - new_height / 2.0))
    new_y_max = new_y_min + new_height - 1
    new_x_min = int(round(center_x - new_width / 2.0))
    new_x_max = new_x_min + new_width - 1

    image_height, image_width = image.shape[:2]
    new_y_min = max(0, new_y_min)
    new_x_min = max(0, new_x_min)
    new_y_max = min(image_height - 1, new_y_max)
    new_x_max = min(image_width - 1, new_x_max)

    crop = image[new_y_min:new_y_max + 1, new_x_min:new_x_max + 1]
    mask_crop = mask_bool[new_y_min:new_y_max + 1, new_x_min:new_x_max + 1].astype(np.uint8) * 255
    return crop, mask_crop


def frame_name_for_result(result, frame_number, source):
    if source.is_file() and source.suffix.lower() in VIDEO_SUFFIXES:
        return f"frame{frame_number:06d}"

    result_path = getattr(result, "path", None)
    if result_path:
        return Path(result_path).stem
    return f"frame{frame_number:06d}"


def reset_output_dir(output_dir):
    output_dir = output_dir.expanduser().resolve()
    if output_dir == Path(output_dir.anchor):
        raise ValueError(f"Refusing to clear filesystem root: {output_dir}")
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def save_first_seen_sample(output_dir, track_id, frame_name, crop, mask_crop):
    crop_path = output_dir / f"id_{track_id}_{frame_name}_crop.jpg"
    mask_path = output_dir / f"id_{track_id}_{frame_name}_mask_crop_bw.png"

    if not cv2.imwrite(str(crop_path), crop):
        raise ValueError(f"Failed to save output image: {crop_path}")
    if not cv2.imwrite(str(mask_path), mask_crop):
        raise ValueError(f"Failed to save output image: {mask_path}")


def export_first_seen_samples(model, args, output_dir):
    seen_track_ids = set()
    stats = {
        "frames": 0,
        "instances": 0,
        "saved_ids": 0,
        "saved_files": 0,
        "skipped_no_mask": 0,
        "skipped_empty_mask": 0,
        "skipped_edge": 0,
        "skipped_seen_id": 0,
    }

    results = model.track(
        source=str(args.source),
        imgsz=args.imgsz,
        conf=args.conf,
        device=args.device,
        tracker=args.tracker,
        stream=True,
        persist=True,
        save=False,
        verbose=False,
    )

    for frame_number, result in enumerate(results, start=1):
        if args.max_frames is not None and frame_number > args.max_frames:
            break

        stats["frames"] += 1
        image = getattr(result, "orig_img", None)
        boxes = getattr(result, "boxes", None)
        masks = getattr(result, "masks", None)
        if image is None or boxes is None or masks is None:
            continue

        polygons_attr = getattr(masks, "xy", None)
        polygons = list(polygons_attr) if polygons_attr is not None else []
        if not polygons:
            continue

        raw_track_ids = to_numpy(getattr(boxes, "id", None))
        track_ids = normalize_track_ids(raw_track_ids, len(polygons))
        frame_name = frame_name_for_result(result, frame_number, args.source)

        for index, polygon in enumerate(polygons):
            stats["instances"] += 1
            if index >= len(track_ids):
                stats["skipped_no_mask"] += 1
                continue

            track_id = int(track_ids[index])
            if track_id in seen_track_ids:
                stats["skipped_seen_id"] += 1
                continue

            mask_bool = mask_from_polygon(polygon, image.shape)
            if mask_bool is None:
                stats["skipped_no_mask"] += 1
                continue
            if mask_touches_horizontal_edges(mask_bool):
                stats["skipped_edge"] += 1
                continue

            crop, mask_crop = crop_original_and_mask(image, mask_bool)
            if crop is None or mask_crop is None:
                stats["skipped_empty_mask"] += 1
                continue

            save_first_seen_sample(output_dir, track_id, frame_name, crop, mask_crop)
            seen_track_ids.add(track_id)
            stats["saved_ids"] += 1
            stats["saved_files"] += 2

    return stats


def main():
    warnings.filterwarnings("ignore")
    args = parse_args()

    try:
        validate_args(args)
        output_dir = reset_output_dir(args.output)
        model = YOLO(str(args.weights))
        stats = export_first_seen_samples(model, args, output_dir)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(
        "Tracked fish crops: "
        f"frames={stats['frames']}, "
        f"instances={stats['instances']}, "
        f"saved_ids={stats['saved_ids']}, "
        f"saved_files={stats['saved_files']}, "
        f"skipped_no_mask={stats['skipped_no_mask']}, "
        f"skipped_empty_mask={stats['skipped_empty_mask']}, "
        f"skipped_edge={stats['skipped_edge']}, "
        f"skipped_seen_id={stats['skipped_seen_id']}"
    )
    print(f"Output directory: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
