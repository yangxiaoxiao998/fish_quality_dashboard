"""fish_condition.py - 由 ``long-height.py`` 重命名。

对 ``crop_images/complete_match`` 下的 ``id_<track>_<frame>_crop.jpg`` +
``id_<track>_<frame>_mask_crop_bw.png`` 对（兼容 6/12 位编号）执行：

1. 重新水平放置；
2. 估计鱼弧长（剔除尾部）；
3. 估计鱼最大高度（体高）；
4. 调体重模型 CatBoost 预测体重；
5. 计算肥满度 condition_factor；
6. 计算鱼腹 ``hex_color``；
7. 输出 ``measurements/long_height_weight_results.csv`` + 可视化图片。

不完整鱼（``incomplete_match``）只进 CSV，不计算体长/体高/体重/肥满度。

默认情况下，CSV 写完后会安全删除 ``measurements/fish_condition_images/`` 调试
图片目录（默认是过程产物，工程师不需要）；传入
``--keep-measurement-debug-images`` 才会保留。

所有路径从 ``config/paths.py`` 读取；CSV / JSON 中出现的图片路径全部使用
相对项目根目录的 POSIX 形式。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型检查时引用，运行时延后加载
    import cv2  # noqa: F401
    import numpy as np  # noqa: F401


THIS_DIR = Path(__file__).resolve().parent
ALGORITHMS_ROOT = THIS_DIR.parent
if str(ALGORITHMS_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS_ROOT))

from config.cleanup import remove_dir_contents  # noqa: E402  pylint: disable=wrong-import-position
from config.naming import (  # noqa: E402  pylint: disable=wrong-import-position
    FISH_CROP_SUFFIX,
    FISH_MASK_SUFFIX,
    make_fish_crop_name,
    make_fish_mask_name,
    parse_fish_file_name,
)
from config.paths import (  # noqa: E402  pylint: disable=wrong-import-position
    ALGORITHMS_CUIYIFEI_DIR,
    ALGORITHMS_GAOYUXI_DIR,
    CATBOOST_FULL_MODEL_PATH,
    PROJECT_ROOT,
    TAIL_DETECTOR_CHECKPOINT,
    ensure_dir,
    to_project_relative,
    video_complete_match_dir,
    video_fish_condition_images_dir,
    video_incomplete_match_dir,
    video_long_height_csv,
    video_measurements_dir,
)


def _cv2():
    import cv2  # type: ignore

    return cv2


def _np():
    import numpy as np  # type: ignore

    return np


# ---------------------------------------------------------------------------
# SciPy / 模型延迟加载
# ---------------------------------------------------------------------------


def _scipy_quad():
    """``scipy.integrate.quad`` 的延迟加载。"""

    from scipy.integrate import quad  # type: ignore

    return quad


def _scipy_curve_fit():
    """``scipy.optimize.curve_fit`` 的延迟加载。"""

    from scipy.optimize import curve_fit  # type: ignore

    return curve_fit


def _import_predict_weight():
    """CatBoost 体重预测器。"""

    sys.path.insert(0, str(ALGORITHMS_CUIYIFEI_DIR))
    from all_model_catboost import measure  # type: ignore  # noqa: WPS433

    return measure


def _import_tail_detector_cls():
    """Mask R-CNN 尾部检测器类。"""

    sys.path.insert(0, str(ALGORITHMS_GAOYUXI_DIR))
    from inference_tail import TailDetector  # type: ignore  # noqa: WPS433

    return TailDetector


EPS = 1e-10


# 注意：透视矩阵已删除硬编码默认值。
# 任何需要 transform_matrix 的算法都应通过
# ``core.calibration_config.load_system_transform_matrix`` 读取系统
# 「视觉矫正」保存的 JSON 配置。
# 不再允许使用任何写死默认矩阵；缺矩阵直接抛错，让用户去
# 系统设置 → 视觉矫正 完成标定。


MM_PER_PIXEL = 0.9829

CSV_FIELDNAMES = [
    "video_id",
    "track_id",
    "fish_uid",
    "frame_index",
    "crop_image_path",
    "body_length_cm",
    "body_length_no_tail_cm",
    "body_height_cm",
    "pred_weight_g",
    "condition_factor",
    "hex_color",
    "surface_damage_ratio",
    "status",
    "error_message",
]


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------


def _resolve_project_relative(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


def _log_path(p: Path) -> str:
    return to_project_relative(p)


def resolve_dirs(args) -> dict:
    """根据 CLI 参数解析输入/输出/CSV 路径。"""

    video_id = getattr(args, "video_id", None)

    if video_id:
        input_dir = (
            _resolve_project_relative(args.input_dir)
            if getattr(args, "input_dir", None)
            else video_complete_match_dir(video_id)
        )
        incomplete_input_dir = (
            _resolve_project_relative(args.incomplete_input_dir)
            if getattr(args, "incomplete_input_dir", None)
            else video_incomplete_match_dir(video_id)
        )
        debug_dir = (
            _resolve_project_relative(args.output_dir)
            if getattr(args, "output_dir", None)
            else video_fish_condition_images_dir(video_id)
        )
        csv_path = (
            _resolve_project_relative(args.csv_output)
            if getattr(args, "csv_output", None)
            else video_long_height_csv(video_id)
        )
    else:
        if not args.input_dir or not args.csv_output:
            raise SystemExit("未传 --video-id 时，必须显式传入 --input-dir 和 --csv-output。")
        input_dir = _resolve_project_relative(args.input_dir)
        incomplete_input_dir = _resolve_project_relative(args.incomplete_input_dir)
        debug_dir = _resolve_project_relative(args.output_dir) if getattr(args, "output_dir", None) else None
        csv_path = _resolve_project_relative(args.csv_output)

    if debug_dir is None:
        debug_dir = csv_path.parent / "fish_condition_images"

    return {
        "video_id": video_id or "",
        "input_dir": input_dir,
        "incomplete_input_dir": incomplete_input_dir,
        "debug_dir": debug_dir,
        "csv_path": csv_path,
    }


# ---------------------------------------------------------------------------
# 数值 / 模型工具
# ---------------------------------------------------------------------------


def _mm_per_pixel_from_calibration(video_id: str | None) -> float:
    """读取系统「视觉矫正」JSON 中的 ``mm_per_pixel``。

    数据真实路径：data/calibration/current_calibration.json
    （系统视觉矫正页面写入的实际文件）。
    """

    try:
        from core.calibration_config import load_mm_per_pixel
        value = load_mm_per_pixel()
        if value is not None:
            return float(value)
    except Exception:
        pass
    return MM_PER_PIXEL


def _build_detector(checkpoint_path: Path | None = None):
    TailDetector = _import_tail_detector_cls()
    ckpt = Path(checkpoint_path) if checkpoint_path else Path(TAIL_DETECTOR_CHECKPOINT)
    if not ckpt.exists():
        raise FileNotFoundError(
            f"找不到尾部检测模型权重：{_log_path(ckpt)}。"
            "请确认 algorithms/pth/maskrcnn_resnet50_fpn_v2-tail.pth 存在。"
        )
    return TailDetector(checkpoint_path=str(ckpt))


def calculate_condition_factor(pred_weight_g: float | None, body_length_cm: float | None) -> float | None:
    """肥满度 = pred_weight_g / body_length_cm^3 * 100；输入不合法时返回 None。"""

    if pred_weight_g is None or body_length_cm is None:
        return None
    if body_length_cm <= 0:
        return None
    try:
        value = float(pred_weight_g) / (float(body_length_cm) ** 3) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return round(value, 4)


# ---------------------------------------------------------------------------
# 输入扫描
# ---------------------------------------------------------------------------


def _iter_crop_mask_pairs(directory: Path):
    """扫描 ``directory`` 中 ``id_<track>_<frame>_crop.jpg`` /
    ``_mask_crop_bw.png`` 对，按 (track_id, frame_index) 排序。"""

    pairs: dict[tuple[int, int], dict[str, Path]] = {}
    if not directory or not directory.exists():
        return
    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        info = parse_fish_file_name(entry.name)
        if info is None:
            continue
        key = (info.track_id, info.frame_index)
        bucket = pairs.setdefault(key, {})
        if info.kind == "crop":
            bucket["crop"] = entry
        elif info.kind == "mask":
            bucket["mask"] = entry
    for key in sorted(pairs.keys()):
        parts = pairs[key]
        crop = parts.get("crop")
        mask = parts.get("mask")
        if crop is None or mask is None:
            continue
        track_id, frame_index = key
        yield track_id, frame_index, crop, mask


# ---------------------------------------------------------------------------
# 鱼体几何 / 弧长 / 高度计算
# ---------------------------------------------------------------------------


def calculate_distance(point1, point2):
    x1, y1 = point1
    x2, y2 = point2
    return abs(math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2))


def line_intersection(line1, line2):
    x1, y1 = line1[0]
    x2, y2 = line1[1]
    x3, y3 = line2[0]
    x4, y4 = line2[1]

    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if denom == 0:
        return None

    p = ((x1 * y2 - y1 * x2) * (x3 - x4) -
         (x1 - x2) * (x3 * y4 - y3 * x4)) / denom
    q = ((x1 * y2 - y1 * x2) * (y3 - y4) -
         (y1 - y2) * (x3 * y4 - y3 * x4)) / denom

    if p < (min(x1, x2) - 5) or p > (max(x1, x2) + 5) or p < (min(x3, x4) - 5) or p > (max(x3, x4) + 5):
        return None
    if q < (min(y1, y2) - 5) or q > (max(y1, y2) + 5) or q < (min(y3, y4) - 5) or q > (max(y3, y4) + 5):
        return None

    return (p, q)


def perpendicular(x, a, b, c):
    slope = 2 * a * x + b
    if slope == 0:
        return (1, 0)
    return (-slope, 1)


def quadratic_function(x, t_coefficients):
    a, b, c = t_coefficients
    return a * x ** 2 + b * x + c


def quadratic_model(x, a, b, c):
    return a * x ** 2 + b * x + c


def find_segment_intersection(coefficients, contour_edge, within_the_scope):
    x1, y1 = contour_edge[0]
    x2, y2 = contour_edge[1]

    if x2 == x1:
        x = x1
        y = quadratic_function(x, coefficients)
        if (min(y1, y2) <= y) and (y <= max(y1, y2)):
            return [(x, y)]
        return []

    a1 = (y2 - y1) / (x2 - x1)
    b1 = y1 - a1 * x1
    a2, b2, c2 = coefficients

    A = a2
    B = b2 - a1
    C = c2 - b1
    discriminant = B ** 2 - 4 * A * C

    if discriminant >= 0:
        quad = _scipy_quad()
        sqrt_d = math.sqrt(discriminant)
        root1 = (-B + sqrt_d) / (2 * A)
        root2 = (-B - sqrt_d) / (2 * A)
        y1_ = a1 * root1 + b1
        y2_ = a1 * root2 + b1
        intersections = [(root1, y1_), (root2, y2_)]
    else:
        intersections = []

    unique_intersections = list(set(intersections))
    if within_the_scope:
        real_intersections = []
        for (x, y) in unique_intersections:
            if (min(x1, x2) <= x) and (x <= max(x1, x2)):
                real_intersections.append((x, y))
    else:
        real_intersections = list(unique_intersections)

    return real_intersections


def calculate_quadratic_arc_length(coefficients, x1, x2):
    a, b, c = coefficients
    quad = _scipy_quad()

    def derivative(x):
        return 2 * a * x + b

    def integrand(x):
        return math.sqrt(1 + (derivative(x)) ** 2)

    arc_length, _ = quad(integrand, x1, x2)
    return arc_length


def get_points_in_contour_with_x_range(image, fish_contour, min_num, max_num):
    cv2 = _cv2()
    np = _np()
    height, width = image.shape[:2]
    mask = np.zeros((height, width), dtype=np.uint8)
    mask = mask.astype(np.uint8) if mask.dtype != np.uint8 else mask
    cv2.drawContours(mask, [fish_contour], -1, 255, thickness=cv2.FILLED)
    mask[:, :min_num] = 0
    mask[:, max_num:] = 0
    return cv2.bitwise_and(image, image, mask=mask)


def rotate_image_and_mask_to_horizontal(image, mask):
    cv2 = _cv2()
    if mask.ndim == 3:
        gray_mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
    else:
        gray_mask = mask.copy()

    _, thresh = cv2.threshold(gray_mask, 127, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return image, thresh, None

    contour = max(contours, key=cv2.contourArea)
    (cx, cy), (w, h), angle = cv2.minAreaRect(contour)
    if w < h:
        angle += 90

    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
    cos_value = abs(matrix[0, 0])
    sin_value = abs(matrix[0, 1])
    new_width = int((height * sin_value) + (width * cos_value))
    new_height = int((height * cos_value) + (width * sin_value))

    matrix[0, 2] += new_width / 2 - cx
    matrix[1, 2] += new_height / 2 - cy

    rotated_image = cv2.warpAffine(image, matrix, (new_width, new_height), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
    rotated_mask = cv2.warpAffine(thresh, matrix, (new_width, new_height), flags=cv2.INTER_NEAREST, borderValue=0)
    return rotated_image, rotated_mask, contour


def fit_quadratic(point_list, weight):
    curve_fit = _scipy_curve_fit()
    X = point_list[:, 0]
    Y = point_list[:, 1]
    p0 = [0.000005, 0.0, 0.0]
    coefficients, _ = curve_fit(quadratic_model, X, Y, p0=p0)
    Y_fit = quadratic_function(X, coefficients)
    weights_arr = np.where(Y > Y_fit, 1, weight) if False else _np().where(Y > Y_fit, 1, weight)
    coefficients, _ = curve_fit(quadratic_model, X, Y, p0=p0, sigma=1 / weights_arr)
    return coefficients


def rotate_poly(poly, rad):
    matrix = _np().asarray([[math.cos(rad), math.sin(rad)],
                             [-math.sin(rad), math.cos(rad)]])
    return _np().dot(poly, matrix)


def min_rect(poly):
    np = _np()
    x_min, y_min = np.min(poly, axis=0)
    x_max, y_max = np.max(poly, axis=0)
    min_rect_ = np.asarray([[x_min, y_min],
                            [x_max, y_min],
                            [x_max, y_max],
                            [x_min, y_max]])
    area = (x_max - x_min) * (y_max - y_min)
    return min_rect_, area


def get_rad(poly):
    rad = []
    for i in range(poly.shape[0]):
        vector = poly[i - 1] - poly[i]
        rad.append(math.atan(vector[1] / (vector[0] + EPS)))
    return rad


def min_rotate_rect(poly):
    np = _np()
    rect_min, area_min = min_rect(poly)
    rad_min = 0.0
    for r in get_rad(poly):
        new_poly = rotate_poly(poly, -r)
        rect, area = min_rect(new_poly)
        if area < area_min:
            rect_min, area_min, rad_min = rect, area, r
    return rotate_poly(rect_min, rad_min)


# ---------------------------------------------------------------------------
# 单张鱼的处理
# ---------------------------------------------------------------------------


def process_single_image(
    detector,
    image_path: Path,
    mask_path: Path,
    output_root: Path,
    mm_per_pixel: float,
    track_id: int,
    frame_index: int,
):
    """对一张完整鱼图片计算体长/体高/体重/肥满度；返回 dict。"""

    cv2 = _cv2()
    np = _np()

    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"cannot read image: {image_path}")
    mask_image = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    if mask_image is None:
        raise ValueError(f"cannot read mask: {mask_path}")

    filename = make_fish_crop_name(track_id, frame_index).replace(FISH_CROP_SUFFIX, "")

    save_path = ensure_dir(output_root / filename)

    rotated_image, rotated_mask, _ = rotate_image_and_mask_to_horizontal(image, mask_image)
    contours, _ = cv2.findContours(rotated_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError("empty rotated mask")

    fish_contour = max(contours, key=cv2.contourArea)
    pic_image = rotated_image
    debug_res_image = rotated_image.copy()
    height, width = pic_image.shape[:2]

    mask = np.zeros_like(rotated_mask)
    cv2.drawContours(mask, [fish_contour], -1, (255), thickness=cv2.FILLED)
    points = cv2.findNonZero(mask)
    if points is None:
        raise ValueError("no contour points")

    points = np.array(points).squeeze()
    if points.ndim == 1:
        points = points[np.newaxis, :]
    points = np.stack((points[:, 0], points[:, 1]), axis=-1)

    coefficients = fit_quadratic(points, 1.2)
    for x_val in range(width):
        y_val = int(quadratic_function(x_val, coefficients))
        if 0 <= y_val < height:
            cv2.line(debug_res_image, (x_val, y_val), (x_val, y_val), (0, 0, 255), 3)

    intersection_points = []
    for i in range(fish_contour.shape[0]):
        contour_edge = [
            (fish_contour[i][0][0], fish_contour[i][0][1]),
            (fish_contour[(i + 1) % fish_contour.shape[0]][0][0],
             fish_contour[(i + 1) % fish_contour.shape[0]][0][1])
        ]
        intersection = find_segment_intersection(coefficients, contour_edge, True)
        if intersection is not None:
            if isinstance(intersection, list):
                intersection_points.extend(intersection)
            else:
                intersection_points.append(intersection)

    if not intersection_points:
        raise ValueError("no intersection points with the quadratic curve")

    left_point = min(intersection_points, key=lambda p: p[0])
    right_point = max(intersection_points, key=lambda p: p[0])

    X_mid_full = (left_point[0] + right_point[0]) / 2.0
    Y_mid_full = quadratic_function(X_mid_full, coefficients)
    coeff_full = np.polyfit([left_point[0], right_point[0], X_mid_full],
                            [left_point[1], right_point[1], Y_mid_full], 2)
    arc_length_full = abs(calculate_quadratic_arc_length(coeff_full, left_point[0], right_point[0]))

    points_below = {}
    points_above = {}
    for point in points:
        x, y = point
        if x not in points_below or y > points_below[x]:
            points_below[x] = y
        if x not in points_above or y < points_above[x]:
            points_above[x] = y
    points_below = np.array([(x, y) for x, y in points_below.items()])
    points_above = np.array([(x, y) for x, y in points_above.items()])

    for point in points_above:
        cv2.circle(debug_res_image, (int(point[0]), int(point[1])), 1, (0, 255, 255), -1)
    for point in points_below:
        cv2.circle(debug_res_image, (int(point[0]), int(point[1])), 1, (255, 255, 0), -1)

    X = points[:, 0]
    X_middle = (left_point[0] + right_point[0]) / 2
    count_left = np.sum(X < X_middle)
    count_right = np.sum(X > X_middle)
    length = right_point[0] - left_point[0]

    pic_image_height = height
    pic_image_width = width

    def find_length(min_num, max_num, center_x, tail_side, coeffs=None):
        a, b, c = coeffs if coeffs is not None else coefficients
        min_x = min_num
        min_length = pic_image_height
        search_end = max_num - 20 if max_num - 20 > min_num else max_num
        for xi in range(min_num, search_end):
            yi = quadratic_model(xi, a, b, c)
            midpoint = (xi, yi)
            direction = perpendicular(xi, a, b, c)
            direction = np.array(direction, dtype=float)
            direction = direction / np.linalg.norm(direction)
            distance = pic_image_width
            perpendicular_vector = (int(distance * direction[0]), int(distance * direction[1]))
            perpendicular_start = (midpoint[0] + perpendicular_vector[0], midpoint[1] + perpendicular_vector[1])
            perpendicular_end = (midpoint[0] - perpendicular_vector[0], midpoint[1] - perpendicular_vector[1])
            intersection_start = None
            intersection_end = None
            for i in range(fish_contour.shape[0]):
                start_point = tuple(fish_contour[i, 0, :])
                end_point = tuple(fish_contour[(i + 1) % fish_contour.shape[0], 0, :])
                intersection = line_intersection((midpoint, perpendicular_start), (start_point, end_point))
                if intersection:
                    intersection_start = (int(intersection[0]), int(intersection[1]))
                    break
            for i in range(fish_contour.shape[0]):
                start_point = tuple(fish_contour[i, 0, :])
                end_point = tuple(fish_contour[(i + 1) % fish_contour.shape[0], 0, :])
                intersection = line_intersection((midpoint, perpendicular_end), (start_point, end_point))
                if intersection:
                    intersection_end = (int(intersection[0]), int(intersection[1]))
                    break
            if intersection_start and intersection_end:
                len_dist = calculate_distance(intersection_start, intersection_end)
                if len_dist < min_length:
                    min_length = len_dist
                    min_x = xi
        whole_target = get_points_in_contour_with_x_range(pic_image, fish_contour, min_num, max_num)
        cv2.imwrite(str(save_path / "debug_crop_tail.png"), whole_target)
        detector.predict(image_np=whole_target, out_dir=save_path, file_stem=filename)
        tail_image_path = save_path / f"{filename}_mask_best.png"
        tail_mask = cv2.imread(str(tail_image_path), cv2.IMREAD_GRAYSCALE)
        if tail_mask is None:
            raise ValueError(f"missing tail mask: {tail_image_path}")
        intersection_candidates = []
        if tail_side == "right":
            search_range = range(min_x, max_num)
        else:
            search_range = range(min_x, min_num - 1, -1)
        for xi in search_range:
            yi = int(quadratic_model(xi, a, b, c))
            if 0 <= yi < tail_mask.shape[0] and 0 <= xi < tail_mask.shape[1]:
                if tail_mask[yi, xi] > 0:
                    intersection_candidates.append(xi)
        if not intersection_candidates:
            return min_x
        if center_x is not None:
            intersection_x = min(intersection_candidates, key=lambda xi: abs(xi - center_x))
        else:
            intersection_x = intersection_candidates[0]
        yi = int(quadratic_model(intersection_x, a, b, c))
        cv2.circle(debug_res_image, (int(intersection_x), yi), 10, (0, 255, 255), -1)
        return intersection_x

    def find_height(fit_min, fit_max, fit_min2, fit_max2):
        nonlocal points_above, points_below, debug_res_image
        points_below_local = points_below[(points_below[:, 0] >= fit_min) & (points_below[:, 0] <= fit_max)]
        a_, b_, c_ = fit_quadratic(points_below_local, 25)
        x_l = points_below_local[:, 0]
        y_curve = a_ * x_l ** 2 + b_ * x_l + c_
        points_below_local = points_below_local[points_below_local[:, 1] < y_curve]

        points_above_local = points_above[(points_above[:, 0] >= fit_min2) & (points_above[:, 0] <= fit_max2)]
        points_below_local = points_below_local[(points_below_local[:, 0] >= fit_min2) & (points_below_local[:, 0] <= fit_max2)]
        points_all = np.vstack((points_above_local, points_below_local))

        min_rect_r = min_rotate_rect(points_all)
        min_rect_r = min_rect_r.reshape(-1, 1, 2).astype(np.int32)
        cv2.polylines(debug_res_image, [min_rect_r], isClosed=True, color=(0, 255, 0), thickness=4)

        shortest_dist = float('inf')
        shortest_points = None
        for i in range(4):
            p1 = min_rect_r[i][0]
            p2 = min_rect_r[(i + 1) % 4][0]
            dist = calculate_distance(p1, p2)
            if dist < shortest_dist:
                shortest_dist = dist
                shortest_points = (p1, p2)
        return shortest_points

    if count_left >= count_right:
        intersection_x = find_length(
            int(left_point[0] + length * 0.7),
            int(right_point[0]),
            X_middle,
            tail_side="right",
            coeffs=coefficients,
        )
        max_intersection_start, max_intersection_end = find_height(
            int(left_point[0]), int(right_point[0] - length * 2 / 5),
            int(left_point[0] + length / 5), int(right_point[0] - length / 2),
        )
        (X3, Y3) = left_point
        (X4, Y4) = (intersection_x, quadratic_function(intersection_x, coefficients))
        (X6, Y6) = max_intersection_start
        (X7, Y7) = max_intersection_end
    else:
        intersection_x = find_length(
            int(left_point[0]),
            int(right_point[0] - length * 0.7),
            X_middle,
            tail_side="left",
            coeffs=coefficients,
        )
        max_intersection_start, max_intersection_end = find_height(
            int(left_point[0] + length * 2 / 5), int(right_point[0]),
            int(left_point[0] + length / 2), int(right_point[0] - length / 5),
        )
        (X3, Y3) = right_point
        (X4, Y4) = (intersection_x, quadratic_function(intersection_x, coefficients))
        (X6, Y6) = max_intersection_start
        (X7, Y7) = max_intersection_end

    cv2.circle(debug_res_image, (int(X3), int(Y3)), 10, (0, 0, 255), -1)
    X5 = int((X3 + X4) / 2)
    Y5 = quadratic_function(X5, coefficients)
    TX2 = [X3, X4, X5]
    TY2 = [Y3, Y4, Y5]
    coefficients2 = np.polyfit(TX2, TY2, 2)
    arc_length = abs(calculate_quadratic_arc_length(coefficients2, X3, X4))
    real_arc_length = arc_length * mm_per_pixel

    body_height = abs(math.sqrt((X6 - X7) ** 2 + (Y6 - Y7) ** 2))
    real_body_height = body_height * mm_per_pixel
    body_length_mm = real_arc_length
    body_height_mm = real_body_height
    body_length_cm = body_length_mm / 10.0
    body_height_cm = body_height_mm / 10.0
    # `find_length` 已经用 mask 识别并截断到尾巴相交点，因此 `body_length_mm`
    # 实际就是"无尾体长"。为了和数据库 / 文档约定对齐，下面同时再显式暴露
    # `body_length_no_tail_mm` / `body_length_no_tail_cm`，数值与 `body_length_*` 相同。
    body_length_no_tail_mm = float(body_length_mm)
    body_length_no_tail_cm = float(body_length_cm)

    predict_weight = _import_predict_weight()
    pred_weight = predict_weight(str(image_path), body_length_cm, body_height_cm)

    visualization_path = save_path / f"{filename}_with_line.jpg"
    cv2.imwrite(str(visualization_path), debug_res_image)

    return {
        "body_length_mm": round(float(body_length_mm), 4),
        "body_height_mm": round(float(body_height_mm), 4),
        "body_length_cm": round(float(body_length_cm), 4),
        "body_length_no_tail_mm": round(float(body_length_no_tail_mm), 4),
        "body_length_no_tail_cm": round(float(body_length_no_tail_cm), 4),
        "body_height_cm": round(float(body_height_cm), 4),
        "pred_weight_g": round(float(pred_weight), 4),
        "debug_image_dir": save_path,
        "arc_length_full": arc_length_full,
    }


# ---------------------------------------------------------------------------
# CSV 行
# ---------------------------------------------------------------------------


def _run_belly_color_safe(*, crop_path: Path, mask_path: Path) -> dict:
    """计算单条完整鱼的腹部 HEX；失败不阻断主流程。"""

    try:
        from core.belly_color import extract_belly_hex_color_from_images  # noqa: WPS433

        cv2 = _cv2()
        crop_image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
        if crop_image is None:
            raise ValueError(f"cannot read crop image: {crop_path}")
        mask_image = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        if mask_image is None:
            raise ValueError(f"cannot read mask image: {mask_path}")
        return {
            "hex_color": extract_belly_hex_color_from_images(crop_image, mask_image),
            "status": "done",
            "error_message": "",
        }
    except Exception as exc:
        return {
            "hex_color": None,
            "status": "failed",
            "error_message": f"{type(exc).__name__}: {exc}",
        }


def _join_error_messages(*messages: str | None) -> str:
    return "; ".join(message.strip() for message in messages if message and message.strip())


def _run_wound_safe(
    *,
    crop_path: Path,
    mask_path: Path,
    model,
    device: str,
    threshold: float,
    disabled: bool,
) -> dict:
    """对单条完整鱼调用 wound 模型。失败返回 ``status="failed"`` 但不抛异常。"""

    if disabled or model is None:
        return {
            "surface_damage_ratio": None,
            "surface_damage_percent": None,
            "wound_area_px": None,
            "fish_area_px": None,
            "status": "failed",
            "error_message": "wound model disabled or not loaded",
        }

    try:
        from core.surface_damage import analyze_surface_damage  # noqa: WPS433
        return analyze_surface_damage(
            image_path=crop_path,
            mask_path=mask_path,
            model=model,
            device=device,
            threshold=threshold,
        )
    except Exception as exc:
        return {
            "surface_damage_ratio": None,
            "surface_damage_percent": None,
            "wound_area_px": None,
            "fish_area_px": None,
            "status": "failed",
            "error_message": f"wound inference exception: {type(exc).__name__}: {exc}",
        }


def _make_csv_row(
    *,
    video_id: str,
    track_id: int,
    frame_index: int,
    crop_path: Path | None,
    status: str,
    body_length_cm: float | None = None,
    body_length_no_tail_cm: float | None = None,
    body_height_cm: float | None = None,
    pred_weight_g: float | None = None,
    error_message: str = "",
    hex_color: str | None = None,
    surface_damage_ratio: float | None = None,
) -> dict:
    crop_name = crop_path.name if crop_path else make_fish_crop_name(track_id, frame_index)

    if crop_path is not None:
        crop_rel = _log_path(crop_path)
    else:
        # 没有 crop_path 时按 status 推测子目录。
        if status == "complete":
            sub = "complete_match"
        elif status == "failed":
            sub = "complete_match"
        else:
            sub = "incomplete_match"
        crop_rel = to_project_relative(
            Path("data") / "jobs" / video_id / "crop_images" / sub / crop_name
        )

    condition_factor = (
        calculate_condition_factor(pred_weight_g, body_length_cm)
        if status == "complete"
        else None
    )

    # 长期字段约定：
    # - status="complete"：写入数值；无伤口 surface_damage_ratio=0.0；
    # - status="incomplete" / "failed"：数值字段全部为空。
    # - HEX 是上述数值字段的例外：几何失败时仍保留已经成功提取的颜色。
    if status == "complete":
        ratio_str = "" if surface_damage_ratio is None else f"{float(surface_damage_ratio):.6f}"
        body_length_cm_str = "" if body_length_cm is None else round(float(body_length_cm), 4)
        body_length_no_tail_cm_str = (
            "" if body_length_no_tail_cm is None else round(float(body_length_no_tail_cm), 4)
        )
        body_height_cm_str = "" if body_height_cm is None else round(float(body_height_cm), 4)
        pred_weight_g_str = "" if pred_weight_g is None else round(float(pred_weight_g), 4)
    else:
        ratio_str = ""
        body_length_cm_str = ""
        body_length_no_tail_cm_str = ""
        body_height_cm_str = ""
        pred_weight_g_str = ""

    hex_color_str = "" if status == "incomplete" or not hex_color else str(hex_color)

    return {
        "video_id": video_id,
        "track_id": track_id,
        "fish_uid": f"{video_id}_id_{track_id}" if video_id else f"id_{track_id}",
        "frame_index": int(frame_index),
        "crop_image_path": crop_rel,
        "body_length_cm": body_length_cm_str,
        "body_length_no_tail_cm": body_length_no_tail_cm_str,
        "body_height_cm": body_height_cm_str,
        "pred_weight_g": pred_weight_g_str,
        "condition_factor": "" if condition_factor is None else condition_factor,
        "hex_color": hex_color_str,
        "surface_damage_ratio": ratio_str,
        "status": status,
        "error_message": error_message,
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run(args) -> int:
    paths = resolve_dirs(args)
    video_id = paths["video_id"]
    input_dir: Path = paths["input_dir"]
    incomplete_input_dir: Path | None = paths["incomplete_input_dir"]
    debug_dir: Path = paths["debug_dir"]
    csv_path: Path = paths["csv_path"]

    if not input_dir.exists():
        print(f"[ERROR] 输入目录不存在：{_log_path(input_dir)}")
        return 2

    ensure_dir(debug_dir)
    ensure_dir(csv_path.parent)

    mm_per_pixel = (
        float(args.mm_per_pixel) if getattr(args, "mm_per_pixel", None) is not None
        else _mm_per_pixel_from_calibration(video_id or None)
    )

    checkpoint = Path(args.checkpoint) if getattr(args, "checkpoint", None) else None
    detector = _build_detector(checkpoint)

    # ----------------- wound 模型批处理只加载一次 -----------------
    disable_wound = bool(getattr(args, "disable_wound", False))
    wound_device = str(getattr(args, "device", "cuda") or "cuda")
    wound_threshold = float(getattr(args, "wound_threshold", 0.5))
    wound_model = None
    if not disable_wound:
        try:
            from core.surface_damage import analyze_surface_damage, load_wound_model  # noqa: WPS433
            wound_checkpoint = getattr(args, "wound_checkpoint", None)
            wound_model = load_wound_model(wound_checkpoint, device=wound_device)
        except FileNotFoundError as exc:
            print(f"[ERROR] {exc}")
            return 2
        except Exception as exc:
            print(f"[ERROR] wound 模型加载失败：{exc}")
            return 2

    rows: list[dict] = []
    complete_pairs = list(_iter_crop_mask_pairs(input_dir))
    for track_id, frame_index, crop_path, mask_path in complete_pairs:
        color_result = _run_belly_color_safe(
            crop_path=crop_path,
            mask_path=mask_path,
        )
        color_row_error = (
            ""
            if color_result.get("status") == "done"
            else f"belly_color_error: {color_result.get('error_message', 'unknown')}"
        )
        try:
            measurement = process_single_image(
                detector=detector,
                image_path=crop_path,
                mask_path=mask_path,
                output_root=debug_dir,
                mm_per_pixel=mm_per_pixel,
                track_id=track_id,
                frame_index=frame_index,
            )
        except Exception as exc:
            print(f"[fail] id_{track_id}_{frame_index:012d}: {exc}")
            traceback.print_exc(limit=2)
            # 几何/重量失败：CSV 写入 status=failed；数值字段全部为空；
            # 即便后面伤口分析能跑，也不写入数值，避免假阳性。
            rows.append(_make_csv_row(
                video_id=video_id,
                track_id=track_id,
                frame_index=frame_index,
                crop_path=crop_path,
                status="failed",
                hex_color=color_result.get("hex_color"),
                error_message=_join_error_messages(color_row_error, str(exc)),
            ))
            continue

        # wound 分析：单条鱼失败不能影响整体 pipeline。
        wound_result = _run_wound_safe(
            crop_path=crop_path,
            mask_path=mask_path,
            model=wound_model,
            device=wound_device,
            threshold=wound_threshold,
            disabled=disable_wound,
        )
        if wound_result.get("status") == "done":
            wound_row_error = ""
        else:
            wound_row_error = f"wound_error: {wound_result.get('error_message', 'unknown')}"

        rows.append(_make_csv_row(
            video_id=video_id,
            track_id=track_id,
            frame_index=frame_index,
            crop_path=crop_path,
            status="complete",
            body_length_cm=measurement["body_length_cm"],
            body_length_no_tail_cm=measurement["body_length_no_tail_cm"],
            body_height_cm=measurement["body_height_cm"],
            pred_weight_g=measurement["pred_weight_g"],
            hex_color=color_result.get("hex_color"),
            surface_damage_ratio=wound_result.get("surface_damage_ratio"),
            error_message=_join_error_messages(color_row_error, wound_row_error),
        ))

    if incomplete_input_dir is not None and incomplete_input_dir.exists():
        for track_id, frame_index, crop_path, mask_path in _iter_crop_mask_pairs(incomplete_input_dir):
            rows.append(_make_csv_row(
                video_id=video_id,
                track_id=track_id,
                frame_index=frame_index,
                crop_path=crop_path,
                status="incomplete",
            ))
    elif incomplete_input_dir is not None:
        print(f"[INFO] incomplete_input_dir 不存在，跳过：{_log_path(incomplete_input_dir)}")

    rows.sort(key=lambda r: (r["frame_index"], r["track_id"]))

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    # 默认情况下，CSV 写完后删除调试图片目录
    keep = bool(getattr(args, "keep_measurement_debug_images", False))
    if not keep:
        # 安全删除：仅在 debug_dir 位于 video_measurements_dir 之内时
        if video_id:
            expected = video_measurements_dir(video_id)
            try:
                debug_dir.resolve().relative_to(expected.resolve())
            except ValueError:
                print(
                    f"[WARN] 调试目录 {_log_path(debug_dir)} 不在 {video_id} 的 measurements/ 内，跳过自动删除。"
                )
            else:
                remove_dir_contents(debug_dir)
                print(f"[INFO] 已清理调试图片目录：{_log_path(debug_dir)}")
        else:
            # 没有 video_id 时不自动删除，避免误操作
            print(
                f"[INFO] 未指定 --video-id，不自动删除调试目录 {_log_path(debug_dir)}。"
                "如需保留调试图片请传入 --keep-measurement-debug-images 或自行清理。"
            )
    else:
        print(f"[INFO] 保留调试图片目录：{_log_path(debug_dir)}")

    # 即便 wound 流程曾经生成过临时图片（默认不生成），
    # 也必须在 CSV 写完后清理，避免被 publish 到 results/。
    if video_id:
        wound_debug_dir = video_measurements_dir(video_id) / "wound_debug"
        if wound_debug_dir.exists():
            try:
                remove_dir_contents(wound_debug_dir)
                print(f"[INFO] 已清理 wound 临时图片目录：{_log_path(wound_debug_dir)}")
            except Exception as exc:
                print(f"[WARN] 清理 wound 临时图片目录失败：{exc}")

    complete_rows = [r for r in rows if r["status"] == "complete"]
    incomplete_rows = [r for r in rows if r["status"] == "incomplete"]
    failed_rows = [r for r in rows if r["status"] == "failed"]
    print()
    print("==== fish_condition 完成 ====")
    print(f"video_id         : {video_id or '-'}")
    print(f"complete_dir     : {_log_path(input_dir)}")
    if incomplete_input_dir is not None:
        print(f"incomplete_dir   : {_log_path(incomplete_input_dir)}")
    print(f"debug_dir        : {_log_path(debug_dir)}")
    print(f"csv_path         : {_log_path(csv_path)}")
    print(f"rows_total       : {len(rows)}")
    print(f"  complete       : {len(complete_rows)}")
    print(f"  incomplete     : {len(incomplete_rows)}")
    print(f"  failed         : {len(failed_rows)}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "鱼体长度/高度/体重 + 肥满度计算；"
            "输入完整鱼目录，输出 measurements/long_height_weight_results.csv"
        )
    )
    parser.add_argument("--video-id", default=None,
                        help="视频 ID；传入时所有目录自动按 data/jobs/<video-id>/... 推导")
    parser.add_argument("--input-dir", default=None,
                        help="完整鱼目录（默认 data/jobs/<video-id>/crop_images/complete_match）")
    parser.add_argument("--incomplete-input-dir", default=None,
                        help="不完整鱼目录（可选；默认 data/jobs/<video-id>/crop_images/incomplete_match）")
    parser.add_argument("--output-dir", default=None,
                        help="调试图片目录（默认 data/jobs/<video-id>/measurements/fish_condition_images/）")
    parser.add_argument("--csv-output", default=None,
                        help="CSV 输出路径（默认 data/jobs/<video-id>/measurements/long_height_weight_results.csv）")
    parser.add_argument("--mm-per-pixel", type=float, default=None,
                        help="像素到毫米的换算系数；缺省时尝试从 calibration/transform_matrix.json 读取")
    parser.add_argument("--checkpoint", default=None,
                        help="Tail detector 权重路径；缺省使用 config.paths.TAIL_DETECTOR_CHECKPOINT")
    parser.add_argument("--keep-measurement-debug-images", action="store_true",
                        help="保留调试图片目录；不传则 CSV 写完后自动清理")
    parser.add_argument("--device", default="cuda",
                        help="兼容旧参数：cuda / cpu，默认 cuda；同时传给 wound 模型")
    parser.add_argument(
        "--disable-wound",
        action="store_true",
        help="【内部测试用】跳过 wound 体表破损计算。正式生产流程**不要**使用；没有 wound 字段的鱼不算完整指标。",
    )
    parser.add_argument(
        "--wound-checkpoint",
        default=None,
        help="wound 模型权重路径；不传则使用 FISH_WOUND_CHECKPOINT 或 algorithms/pth/maskrcnn_resnet50_fpn_v2-wound.pth 默认路径",
    )
    parser.add_argument(
        "--wound-threshold",
        type=float,
        default=0.5,
        help="wound 检测 score 阈值，默认 0.5",
    )
    return parser


def parse_args(argv=None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
