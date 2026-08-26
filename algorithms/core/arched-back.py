"""计算已对齐完整鱼的弓背指标。

输入与 ``core.fish_condition`` 一致：
``crop_images/complete_match`` 中的 crop + mask 图像对。这些图像已由
``core.filter_complete_fish`` 完成水平对齐和腹背方向校正，因此本模块不再
估计旋转角，也不对 crop 或 mask 做任何旋转。

指标计算沿用原始脚本的定义：提取鱼背上轮廓，判断鱼尾方向并在
二次曲线拟合前去除尾部 20% 的点，再以拟合曲线的极值为中心，
在鱼体宽度左右各 15% 的区间内计算：

    arched_back_ratio = 上轮廓弧长 / 区间两端直线距离

结果按 ``track_id + frame_index`` 写回
``measurements/long_height_weight_results.csv`` 的 ``arched_back_ratio`` 列。
每条鱼的全部中间数值和可视化保存到
``measurements/arched_back/``，并由 pipeline 发布到
``data/results/<video_id>/fish_images/arched_back/``。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np


THIS_DIR = Path(__file__).resolve().parent
ALGORITHMS_ROOT = THIS_DIR.parent
if str(ALGORITHMS_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS_ROOT))

from config.naming import parse_fish_file_name  # noqa: E402
from config.paths import (  # noqa: E402
    PROJECT_ROOT,
    ensure_dir,
    to_project_relative,
    video_arched_back_dir,
    video_complete_match_dir,
    video_long_height_csv,
)


ARCHED_BACK_FIELD = "arched_back_ratio"
DEFAULT_WINDOW_PERCENT = 0.15
DEFAULT_TAIL_REMOVE_RATIO = 0.20


def _resolve_project_relative(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = PROJECT_ROOT / resolved
    return resolved.resolve()


def _log_path(path: Path) -> str:
    return to_project_relative(path)


def resolve_paths(args) -> tuple[Path, Path, Path]:
    """解析完整鱼输入目录、已有测量 CSV 和中间结果目录。"""

    video_id = getattr(args, "video_id", None)
    input_dir = _resolve_project_relative(getattr(args, "input_dir", None))
    csv_path = _resolve_project_relative(getattr(args, "csv_output", None))
    output_dir = _resolve_project_relative(getattr(args, "output_dir", None))

    if video_id:
        input_dir = input_dir or video_complete_match_dir(video_id)
        csv_path = csv_path or video_long_height_csv(video_id)
        output_dir = output_dir or video_arched_back_dir(video_id)
    elif input_dir is None or csv_path is None:
        raise SystemExit(
            "未传 --video-id 时，必须同时指定 --input-dir 和 --csv-output。"
        )

    output_dir = output_dir or csv_path.parent / "arched_back"
    return input_dir, csv_path, output_dir


def iter_crop_mask_pairs(input_dir: Path):
    """按 ``(track_id, frame_index)`` 遍历完整鱼 crop/mask 对。"""

    pairs: dict[tuple[int, int], dict[str, Path]] = {}
    if not input_dir.exists():
        return

    for entry in input_dir.iterdir():
        if not entry.is_file():
            continue
        info = parse_fish_file_name(entry.name)
        if info is None:
            continue
        bucket = pairs.setdefault((info.track_id, info.frame_index), {})
        bucket[info.kind] = entry

    for (track_id, frame_index), paths in sorted(pairs.items()):
        crop_path = paths.get("crop")
        mask_path = paths.get("mask")
        if crop_path is not None and mask_path is not None:
            yield track_id, frame_index, crop_path, mask_path


def _filled_fish_mask(mask_image: np.ndarray) -> np.ndarray:
    """将 mask 二值化，并仅保留面积最大的鱼体轮廓。"""

    if mask_image.ndim == 3:
        gray = cv2.cvtColor(mask_image, cv2.COLOR_BGR2GRAY)
    elif mask_image.ndim == 2:
        gray = mask_image
    else:
        raise ValueError(f"unsupported mask dimensions: {mask_image.shape}")

    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        raise ValueError("empty fish mask")

    contour = max(contours, key=cv2.contourArea)
    image_area = int(gray.shape[0] * gray.shape[1])
    if cv2.contourArea(contour) > 0.9 * image_area:
        binary = cv2.bitwise_not(binary)
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contours:
            raise ValueError("empty fish mask after foreground inversion")
        contour = max(contours, key=cv2.contourArea)

    filled = np.zeros(gray.shape, dtype=np.uint8)
    cv2.drawContours(filled, [contour], -1, 255, thickness=cv2.FILLED)
    return filled


def extract_upper_curve(mask_image: np.ndarray) -> tuple[np.ndarray, str]:
    """返回按 x 升序排列的鱼背上轮廓与鱼头所在方向。"""

    fish_mask = _filled_fish_mask(mask_image)
    y_coords, x_coords = np.where(fish_mask == 255)
    if x_coords.size < 3:
        raise ValueError("fish mask has too few foreground pixels")

    left_x = int(x_coords.min())
    right_x = int(x_coords.max())
    midpoint_of_extents = (left_x + right_x) / 2.0
    centroid_x = float(x_coords.mean())
    head_direction = "left" if midpoint_of_extents >= centroid_x else "right"

    unique_x, inverse = np.unique(x_coords, return_inverse=True)
    mask_height = fish_mask.shape[0]
    upper_y = np.full(len(unique_x), mask_height, dtype=np.int32)
    np.minimum.at(upper_y, inverse, y_coords)
    if np.any(upper_y == mask_height):
        raise ValueError("failed to extract a continuous upper contour")
    upper_y = upper_y.astype(float)
    upper_curve = np.column_stack((unique_x.astype(float), upper_y))
    if len(upper_curve) < 3:
        raise ValueError("upper contour has too few points")
    return upper_curve, head_direction


def compute_arched_back_metrics(
    mask_image: np.ndarray,
    *,
    window_percent: float = DEFAULT_WINDOW_PERCENT,
    tail_remove_ratio: float = DEFAULT_TAIL_REMOVE_RATIO,
) -> dict:
    """从已对齐 mask 计算弓背比值及中间距离。"""

    if not 0 < float(window_percent) <= 0.5:
        raise ValueError("window_percent must be in (0, 0.5]")
    if not 0 <= float(tail_remove_ratio) < 1:
        raise ValueError("tail_remove_ratio must be in [0, 1)")

    points, head_direction = extract_upper_curve(mask_image)
    x = points[:, 0]

    remove_count = int(math.ceil(float(tail_remove_ratio) * len(points)))
    if remove_count > 0 and len(points) - remove_count >= 3:
        # 鱼头在左则鱼尾在右，反之亦然。
        fit_points = points[:-remove_count] if head_direction == "left" else points[remove_count:]
    else:
        fit_points = points

    a, b, c = np.polyfit(fit_points[:, 0], fit_points[:, 1], 2)
    if abs(float(a)) <= np.finfo(float).eps:
        extreme_x = (float(x.min()) + float(x.max())) / 2.0
    else:
        extreme_x = float(-b / (2.0 * a))
    extreme_x = float(np.clip(extreme_x, x.min(), x.max()))
    extreme_index = int(np.argmin(np.abs(x - extreme_x)))
    extreme_point = points[extreme_index]

    x_radius = float(window_percent) * float(x.max() - x.min())
    left_index = int(np.searchsorted(x, extreme_x - x_radius, side="left"))
    right_index = int(np.searchsorted(x, extreme_x + x_radius, side="right") - 1)
    left_index = max(0, left_index)
    right_index = min(len(points) - 1, right_index)
    if right_index <= left_index:
        raise ValueError("arched-back measurement interval has fewer than two points")

    segment = points[left_index:right_index + 1]
    straight_distance = float(np.linalg.norm(segment[-1] - segment[0]))
    if straight_distance <= np.finfo(float).eps:
        raise ValueError("arched-back straight distance is zero")
    curve_distance = float(
        np.sqrt(((segment[1:] - segment[:-1]) ** 2).sum(axis=1)).sum()
    )

    return {
        ARCHED_BACK_FIELD: curve_distance / straight_distance,
        "d_straight": straight_distance,
        "d_curve": curve_distance,
        "head_direction": head_direction,
        "fit_coefficients": (float(a), float(b), float(c)),
        "extreme_x": extreme_x,
        "extreme_point": extreme_point.copy(),
        "left_index": left_index,
        "right_index": right_index,
        "interval_start_point": segment[0].copy(),
        "interval_end_point": segment[-1].copy(),
        "upper_curve_points": points.copy(),
        "fit_points": fit_points.copy(),
        "selected_curve_points": segment.copy(),
        "tail_removed_points": int(len(points) - len(fit_points)),
        "window_percent": float(window_percent),
        "tail_remove_ratio": float(tail_remove_ratio),
    }


def analyze_arched_back(
    crop_path: Path | str,
    mask_path: Path | str,
    *,
    window_percent: float = DEFAULT_WINDOW_PERCENT,
    tail_remove_ratio: float = DEFAULT_TAIL_REMOVE_RATIO,
) -> dict:
    """读取一对已对齐的完整鱼图像，计算弓背指标。"""

    crop_image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
    if crop_image is None:
        raise ValueError(f"cannot read crop image: {crop_path}")
    mask_image = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    if mask_image is None:
        raise ValueError(f"cannot read mask image: {mask_path}")
    if crop_image.shape[:2] != mask_image.shape[:2]:
        raise ValueError(
            f"image/mask size mismatch: {crop_image.shape[:2]} vs {mask_image.shape[:2]}"
        )
    return compute_arched_back_metrics(
        mask_image,
        window_percent=window_percent,
        tail_remove_ratio=tail_remove_ratio,
    )


def _point_list(points: np.ndarray) -> list[list[float]]:
    return [[float(x), float(y)] for x, y in np.asarray(points)]


def _write_json_atomic(path: Path, payload: dict) -> None:
    ensure_dir(path.parent)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as output_file:
            json.dump(payload, output_file, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def save_intermediate_results(
    *,
    crop_path: Path,
    mask_path: Path,
    output_dir: Path,
    track_id: int,
    frame_index: int,
    metrics: dict,
) -> dict[str, str]:
    """保存原脚本可视化和完整的逐鱼数值明细。"""

    stem = f"id_{track_id}_{frame_index:012d}"
    ensure_dir(output_dir / "overlays")
    ensure_dir(output_dir / "masks")
    ensure_dir(output_dir / "details")

    crop_image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
    if crop_image is None:
        raise ValueError(f"cannot read crop image for visualization: {crop_path}")
    mask_image = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    if mask_image is None:
        raise ValueError(f"cannot read mask image for visualization: {mask_path}")

    upper_curve = np.asarray(metrics["upper_curve_points"], dtype=float)
    selected_curve = np.asarray(metrics["selected_curve_points"], dtype=float)
    a, b, c = metrics["fit_coefficients"]
    fit_x = np.linspace(upper_curve[:, 0].min(), upper_curve[:, 0].max(), 300)
    fit_y = a * fit_x ** 2 + b * fit_x + c
    fit_curve = np.column_stack((fit_x, fit_y))

    # overlay2 保留“上轮廓点 + 拟合曲线”；overlay 在此基础上
    # 再保留极值点、实际参与计算的曲线段、区间端点和直线弦。
    overlay2 = crop_image.copy()
    for x_value, y_value in upper_curve.astype(np.int32):
        cv2.circle(overlay2, (int(x_value), int(y_value)), 2, (0, 0, 255), -1)

    image_height = crop_image.shape[0]
    fit_draw = fit_curve.copy()
    fit_draw[:, 1] = np.clip(fit_draw[:, 1], -2 * image_height, 3 * image_height)
    fit_draw = fit_draw.astype(np.int32)
    cv2.polylines(overlay2, [fit_draw], False, (0, 255, 255), 2)

    overlay = overlay2.copy()
    selected_draw = selected_curve.astype(np.int32)
    cv2.polylines(overlay, [selected_draw], False, (0, 255, 0), 3)

    extreme_point = tuple(np.rint(metrics["extreme_point"]).astype(int))
    interval_start = tuple(np.rint(metrics["interval_start_point"]).astype(int))
    interval_end = tuple(np.rint(metrics["interval_end_point"]).astype(int))
    cv2.circle(overlay, extreme_point, 6, (200, 100, 100), -1)

    # 用橙色小圆环标出弓背测量区间的两个端点。
    short_side = min(crop_image.shape[:2])
    interval_marker_radius = max(3, min(5, short_side // 40))
    for interval_point in (interval_start, interval_end):
        cv2.circle(
            overlay,
            interval_point,
            interval_marker_radius,
            (0, 128, 255),
            2,
            cv2.LINE_AA,
        )

    overlay_rel = Path("overlays") / f"{stem}_overlay.png"
    overlay2_rel = Path("overlays") / f"{stem}_overlay2.png"
    filled_mask_rel = Path("masks") / f"{stem}_filled_mask.png"
    detail_rel = Path("details") / f"{stem}.json"

    if not cv2.imwrite(str(output_dir / overlay_rel), overlay):
        raise OSError(f"failed to write overlay: {output_dir / overlay_rel}")
    if not cv2.imwrite(str(output_dir / overlay2_rel), overlay2):
        raise OSError(f"failed to write overlay2: {output_dir / overlay2_rel}")
    if not cv2.imwrite(str(output_dir / filled_mask_rel), _filled_fish_mask(mask_image)):
        raise OSError(f"failed to write filled mask: {output_dir / filled_mask_rel}")

    detail_payload = {
        "track_id": int(track_id),
        "frame_index": int(frame_index),
        "filename": stem,
        "crop_image_name": crop_path.name,
        "mask_image_name": mask_path.name,
        "status": "done",
        "error_message": "",
        "arched_back_ratio": round(float(metrics[ARCHED_BACK_FIELD]), 9),
        "d_straight": round(float(metrics["d_straight"]), 9),
        "d_curve": round(float(metrics["d_curve"]), 9),
        "head_direction": metrics["head_direction"],
        "fit_coefficients": {
            "a": float(a),
            "b": float(b),
            "c": float(c),
        },
        "extreme_x": float(metrics["extreme_x"]),
        "extreme_point": _point_list(np.asarray([metrics["extreme_point"]]))[0],
        "selected_interval": {
            "left_index": int(metrics["left_index"]),
            "right_index": int(metrics["right_index"]),
            "start_point": _point_list(
                np.asarray([metrics["interval_start_point"]])
            )[0],
            "end_point": _point_list(
                np.asarray([metrics["interval_end_point"]])
            )[0],
        },
        "parameters": {
            "window_percent": float(metrics["window_percent"]),
            "tail_remove_ratio": float(metrics["tail_remove_ratio"]),
            "tail_removed_points": int(metrics["tail_removed_points"]),
        },
        "upper_curve_points": _point_list(upper_curve),
        "fit_input_points": _point_list(np.asarray(metrics["fit_points"])),
        "fitted_curve_points": _point_list(fit_curve),
        "selected_curve_points": _point_list(selected_curve),
        "artifacts": {
            "overlay": overlay_rel.as_posix(),
            "overlay2": overlay2_rel.as_posix(),
            "filled_mask": filled_mask_rel.as_posix(),
        },
    }
    _write_json_atomic(output_dir / detail_rel, detail_payload)

    return {
        "overlay_path": overlay_rel.as_posix(),
        "overlay2_path": overlay2_rel.as_posix(),
        "filled_mask_path": filled_mask_rel.as_posix(),
        "detail_path": detail_rel.as_posix(),
    }


SUMMARY_FIELDNAMES = [
    "video_id",
    "track_id",
    "frame_index",
    "filename",
    "d_straight",
    "d_curve",
    ARCHED_BACK_FIELD,
    "head_direction",
    "fit_a",
    "fit_b",
    "fit_c",
    "extreme_x",
    "extreme_y",
    "interval_left_x",
    "interval_left_y",
    "interval_right_x",
    "interval_right_y",
    "left_index",
    "right_index",
    "window_percent",
    "tail_remove_ratio",
    "tail_removed_points",
    "upper_curve_point_count",
    "fit_point_count",
    "selected_curve_point_count",
    "overlay_path",
    "overlay2_path",
    "filled_mask_path",
    "detail_path",
    "status",
    "error_message",
]


def _success_summary_row(
    *,
    video_id: str,
    track_id: int,
    frame_index: int,
    metrics: dict,
    artifacts: dict[str, str],
) -> dict:
    a, b, c = metrics["fit_coefficients"]
    extreme_x, extreme_y = metrics["extreme_point"]
    left_x, left_y = metrics["interval_start_point"]
    right_x, right_y = metrics["interval_end_point"]
    return {
        "video_id": video_id,
        "track_id": track_id,
        "frame_index": frame_index,
        "filename": f"id_{track_id}_{frame_index:012d}",
        "d_straight": f"{float(metrics['d_straight']):.9f}",
        "d_curve": f"{float(metrics['d_curve']):.9f}",
        ARCHED_BACK_FIELD: f"{float(metrics[ARCHED_BACK_FIELD]):.9f}",
        "head_direction": metrics["head_direction"],
        "fit_a": f"{float(a):.12g}",
        "fit_b": f"{float(b):.12g}",
        "fit_c": f"{float(c):.12g}",
        "extreme_x": f"{float(extreme_x):.6f}",
        "extreme_y": f"{float(extreme_y):.6f}",
        "interval_left_x": f"{float(left_x):.6f}",
        "interval_left_y": f"{float(left_y):.6f}",
        "interval_right_x": f"{float(right_x):.6f}",
        "interval_right_y": f"{float(right_y):.6f}",
        "left_index": int(metrics["left_index"]),
        "right_index": int(metrics["right_index"]),
        "window_percent": metrics["window_percent"],
        "tail_remove_ratio": metrics["tail_remove_ratio"],
        "tail_removed_points": metrics["tail_removed_points"],
        "upper_curve_point_count": len(metrics["upper_curve_points"]),
        "fit_point_count": len(metrics["fit_points"]),
        "selected_curve_point_count": len(metrics["selected_curve_points"]),
        **artifacts,
        "status": "done",
        "error_message": "",
    }


def _failure_summary_row(
    *,
    video_id: str,
    track_id: int,
    frame_index: int,
    error_message: str,
    detail_path: str,
) -> dict:
    row = {field: "" for field in SUMMARY_FIELDNAMES}
    row.update(
        {
            "video_id": video_id,
            "track_id": track_id,
            "frame_index": frame_index,
            "filename": f"id_{track_id}_{frame_index:012d}",
            "detail_path": detail_path,
            "status": "failed",
            "error_message": error_message,
        }
    )
    return row


def _write_summary_files(output_dir: Path, rows: list[dict]) -> None:
    ensure_dir(output_dir)
    csv_path = output_dir / "all_results.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=SUMMARY_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    # 保留原始脚本的 all_results.txt 交付形式，同时增加新的中间字段。
    text_fields = [
        "filename",
        "d_straight",
        "d_curve",
        ARCHED_BACK_FIELD,
        "head_direction",
        "extreme_x",
        "extreme_y",
        "interval_left_x",
        "interval_left_y",
        "interval_right_x",
        "interval_right_y",
        "status",
        "error_message",
    ]
    with open(output_dir / "all_results.txt", "w", encoding="utf-8") as output_file:
        output_file.write("\t".join(text_fields) + "\n")
        for row in rows:
            output_file.write(
                "\t".join(str(row.get(field, "")) for field in text_fields) + "\n"
            )


def _append_error(existing: str | None, message: str) -> str:
    values = [value.strip() for value in (existing, message) if value and value.strip()]
    return "; ".join(values)


def _write_csv_atomic(csv_path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    temp_path = csv_path.with_suffix(csv_path.suffix + ".tmp")
    try:
        with open(temp_path, "w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp_path, csv_path)
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def run(args) -> int:
    try:
        input_dir, csv_path, output_dir = resolve_paths(args)
    except SystemExit as exc:
        print(f"[ERROR] {exc}")
        return 2

    if not input_dir.is_dir():
        print(f"[ERROR] 完整鱼输入目录不存在：{_log_path(input_dir)}")
        return 2
    if not csv_path.is_file():
        print(f"[ERROR] 测量 CSV 不存在：{_log_path(csv_path)}")
        return 2

    window_percent = float(getattr(args, "window_percent", DEFAULT_WINDOW_PERCENT))
    tail_remove_ratio = float(
        getattr(args, "tail_remove_ratio", DEFAULT_TAIL_REMOVE_RATIO)
    )
    video_id = str(getattr(args, "video_id", None) or "")
    ensure_dir(output_dir)
    ensure_dir(output_dir / "overlays")
    ensure_dir(output_dir / "masks")
    ensure_dir(output_dir / "details")

    results: dict[tuple[int, int], dict] = {}
    failures: dict[tuple[int, int], str] = {}
    summary_rows: list[dict] = []
    for track_id, frame_index, crop_path, mask_path in iter_crop_mask_pairs(input_dir):
        key = (track_id, frame_index)
        stem = f"id_{track_id}_{frame_index:012d}"
        try:
            metrics = analyze_arched_back(
                crop_path,
                mask_path,
                window_percent=window_percent,
                tail_remove_ratio=tail_remove_ratio,
            )
            artifacts = save_intermediate_results(
                crop_path=crop_path,
                mask_path=mask_path,
                output_dir=output_dir,
                track_id=track_id,
                frame_index=frame_index,
                metrics=metrics,
            )
            results[key] = metrics
            summary_rows.append(
                _success_summary_row(
                    video_id=video_id,
                    track_id=track_id,
                    frame_index=frame_index,
                    metrics=metrics,
                    artifacts=artifacts,
                )
            )
            print(
                f"[arched-back] id_{track_id}_{frame_index:012d}: "
                f"{ARCHED_BACK_FIELD}={float(metrics[ARCHED_BACK_FIELD]):.6f}"
            )
        except Exception as exc:
            failures[key] = f"arched_back_error: {type(exc).__name__}: {exc}"
            detail_rel = Path("details") / f"{stem}.json"
            _write_json_atomic(
                output_dir / detail_rel,
                {
                    "track_id": track_id,
                    "frame_index": frame_index,
                    "filename": stem,
                    "crop_image_name": crop_path.name,
                    "mask_image_name": mask_path.name,
                    "status": "failed",
                    "error_message": failures[key],
                },
            )
            summary_rows.append(
                _failure_summary_row(
                    video_id=video_id,
                    track_id=track_id,
                    frame_index=frame_index,
                    error_message=failures[key],
                    detail_path=detail_rel.as_posix(),
                )
            )
            print(f"[WARN] id_{track_id}_{frame_index:012d}: {failures[key]}")

    _write_summary_files(output_dir, summary_rows)

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        rows = [dict(row) for row in reader]
        fieldnames = list(reader.fieldnames or [])

    if not fieldnames:
        print(f"[ERROR] 测量 CSV 没有表头：{_log_path(csv_path)}")
        return 2
    if ARCHED_BACK_FIELD not in fieldnames:
        insert_at = fieldnames.index("status") if "status" in fieldnames else len(fieldnames)
        fieldnames.insert(insert_at, ARCHED_BACK_FIELD)

    updated_count = 0
    for row in rows:
        row.setdefault(ARCHED_BACK_FIELD, "")
        try:
            key = (int(row.get("track_id", "")), int(row.get("frame_index", "")))
        except (TypeError, ValueError):
            continue
        if key in results:
            row[ARCHED_BACK_FIELD] = (
                f"{float(results[key][ARCHED_BACK_FIELD]):.6f}"
            )
            updated_count += 1
        elif key in failures:
            row[ARCHED_BACK_FIELD] = ""
            row["error_message"] = _append_error(
                row.get("error_message"), failures[key]
            )

    _write_csv_atomic(csv_path, fieldnames, rows)

    print()
    print("==== arched-back 完成 ====")
    print(f"input_dir       : {_log_path(input_dir)}")
    print(f"csv_path        : {_log_path(csv_path)}")
    print(f"results_dir     : {_log_path(output_dir)}")
    print(f"complete_pairs  : {len(results) + len(failures)}")
    print(f"updated_rows    : {updated_count}")
    print(f"failed_metrics  : {len(failures)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="计算已对齐完整鱼的弓背比值，并写回鱼体测量 CSV"
    )
    parser.add_argument(
        "--video-id",
        default=None,
        help="视频 ID；传入时自动使用该任务的 complete_match 和测量 CSV",
    )
    parser.add_argument(
        "--input-dir",
        default=None,
        help="完整鱼输入目录（默认 data/jobs/<video-id>/crop_images/complete_match）",
    )
    parser.add_argument(
        "--csv-output",
        default=None,
        help="已有测量 CSV（默认 data/jobs/<video-id>/measurements/long_height_weight_results.csv）",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="中间数据和可视化目录（默认 data/jobs/<video-id>/measurements/arched_back）",
    )
    parser.add_argument(
        "--window-percent",
        type=float,
        default=DEFAULT_WINDOW_PERCENT,
        help="极值左右各取鱼体宽度的比例，默认 0.15",
    )
    parser.add_argument(
        "--tail-remove-ratio",
        type=float,
        default=DEFAULT_TAIL_REMOVE_RATIO,
        help="二次曲线拟合前去掉的尾部轮廓比例，默认 0.20",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())