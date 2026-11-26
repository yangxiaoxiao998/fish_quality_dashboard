"""fish_condition.py - 由 ``long-height.py`` 重命名。

对 ``crop_images/complete_match`` 下的 ``id_<track>_<frame>_crop.jpg`` +
``id_<track>_<frame>_mask_crop_bw.png`` 对（兼容 6/12 位编号）执行：

1. 重新水平放置；
2. 估计鱼全长（含尾）和鱼体弧长（在尾部几何最窄处截断）；
3. 估计鱼最大高度（体高）；
4. 调体重模型 CatBoost 预测体重；
5. 计算肥满度 condition_factor；
6. 计算鱼腹 ``hex_color``；
7. 为后续 ``arched-back.py`` 弓背统计预留 ``arched_back_ratio`` 列；
8. 输出 ``measurements/long_height_weight_results.csv``、每条鱼的
   ``measurements.json`` 和可视化图片。

``arched-back.py`` 在后续阶段会调用本模块的坐标转换函数，
将弓背测量区间的两个端点也画入最终体长可视化图。

不完整鱼（``incomplete_match``）只进 CSV，不计算体长/体高/体重/肥满度。

无尾体长不使用鱼尾检测模型：程序先根据整鱼 mask 找到尾部
几何最窄处，再从该处沿中轴线向尾鳍末端搜索。程序穷举每个
候选截面鱼身侧和尾鳍侧的 Lab 颜色中位数，直接选择所有有效颜色
窗口中原始色差最大的点作为最终鱼身/尾鳍截断点；只有没有任何
有效颜色窗口时才保留几何截断点。

默认保留 ``measurements/fish_condition_images/`` 下的旋转结果、尾部宽度
搜索图和最终测量标注图，便于核对计算过程。

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
    make_fish_crop_name,
    parse_fish_file_name,
)
from config.paths import (  # noqa: E402  pylint: disable=wrong-import-position
    PROJECT_ROOT,
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

    from algorithms.core.all_model_catboost import measure  # type: ignore  # noqa: WPS433

    return measure


EPS = 1e-10

# 无尾体长的纯几何尾部截断参数。搜索比例和尖端排除比例均相对于
# 中轴线与鱼轮廓两个端点之间的水平长度。
TAIL_WIDTH_SEARCH_RATIO = 0.35
TAIL_TIP_EXCLUSION_RATIO = 0.05
TAIL_WIDTH_SMOOTH_RATIO = 0.015
TAIL_BODY_EXPANSION_RATIO = 1.15
TAIL_BODY_EXPANSION_SPAN_RATIO = 0.04
TAIL_MONOTONIC_EXPANSION_RATIO = 1.25
TAIL_MIN_CROSS_SECTIONS = 9

# 几何最窄点到尾端之间的 Lab 颜色突变参数。所有比例均相对于
# 鱼的水平长度；颜色窗口只使用 mask 内靠近中轴线的像素。
TAIL_COLOR_WINDOW_RATIO = 0.025
TAIL_COLOR_GAP_RATIO = 0.005
TAIL_COLOR_CENTER_HALF_WIDTH_RATIO = 0.30
TAIL_COLOR_MIN_PIXELS = 30
TAIL_COLOR_PROFILE_X_TICK_STEP = 4
TAIL_COLOR_PROFILE_MIN_X_SPAN = 60.0


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
    "arched_back_ratio",
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
        from algorithms.core.calibration_config import load_mm_per_pixel
        value = load_mm_per_pixel()
        if value is not None:
            return float(value)
    except Exception:
        pass
    return MM_PER_PIXEL


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


def _draw_quadratic_segment(image, coefficients, x1, x2, color, thickness):
    """在图片上画出一段二次曲线，只保留落在画布内的点。"""

    cv2 = _cv2()
    np = _np()
    height, width = image.shape[:2]
    point_count = max(2, int(abs(float(x2) - float(x1))) + 1)
    x_values = np.linspace(float(x1), float(x2), point_count)
    y_values = quadratic_function(x_values, coefficients)
    valid = (
        (x_values >= 0)
        & (x_values < width)
        & (y_values >= 0)
        & (y_values < height)
    )
    curve_points = np.column_stack((x_values[valid], y_values[valid]))
    if len(curve_points) < 2:
        return
    curve_points = np.rint(curve_points).astype(np.int32).reshape(-1, 1, 2)
    cv2.polylines(image, [curve_points], False, color, thickness, cv2.LINE_AA)


def _draw_dashed_quadratic_segment(
    image,
    coefficients,
    x1,
    x2,
    color,
    thickness,
    dash_length,
    gap_length,
):
    """沿二次曲线画虚线，用于从重叠的全长线上区分无尾体长。"""

    direction = 1.0 if float(x2) >= float(x1) else -1.0
    cursor = float(x1)
    end = float(x2)
    dash_step = max(1.0, float(dash_length))
    gap_step = max(1.0, float(gap_length))
    while (end - cursor) * direction > 0:
        dash_end = cursor + direction * dash_step
        if (dash_end - end) * direction > 0:
            dash_end = end
        _draw_quadratic_segment(
            image,
            coefficients,
            cursor,
            dash_end,
            color,
            thickness,
        )
        cursor = dash_end + direction * gap_step


def _shift_quadratic_coefficients(coefficients, offset_x: float, offset_y: float):
    """把 ``y=f(x)`` 平移为 ``y'=f(x'-offset_x)+offset_y``。"""

    np = _np()
    a, b, c = (float(value) for value in coefficients)
    return np.asarray(
        [
            a,
            b - 2.0 * a * offset_x,
            a * offset_x ** 2 - b * offset_x + c + offset_y,
        ],
        dtype=float,
    )


def _make_measurement_visualization(
    image,
    fish_contour,
    full_coefficients,
    body_coefficients,
    full_start,
    full_end,
    body_start,
    tail_cut,
    tail_cut_line=None,
    body_height_line=None,
):
    """生成全长、无尾体长和体高的无文字可视化图。"""

    cv2 = _cv2()
    source_height, source_width = image.shape[:2]

    # BGR：全长为紫色实线、无尾体长为青色虚线。
    # 无尾体长与全长大部分重合，虚线可让底层紫色全长同时保持可见。
    total_color = (255, 0, 255)
    body_color = (255, 255, 0)
    head_color = (0, 0, 255)
    # 鱼尾末端改用蓝色，避免与后续加入的橙色弓背关键点混淆。
    tail_endpoint_color = (255, 128, 0)
    tail_cut_color = (0, 255, 255)
    body_height_color = (80, 220, 80)
    outline_color = (0, 0, 0)

    short_side = max(1, min(source_height, source_width))
    # 测量线只需要标明路径，不应大面积遮挡鱼体纹理。
    # 旧值使用 short_side / 16，对常见的小鱼 crop 会生成
    # 5--7 px 的彩色线，再叠加 3 px 黑色描边后显得过粗。
    total_thickness = max(2, min(4, int(round(short_side / 26.0))))
    body_thickness = max(1, total_thickness - 1)
    dash_length = max(8, min(24, int(round(source_width / 14.0))))
    gap_length = max(5, dash_length // 2)
    # 鱼首、尾端和尾部截断点使用较小的标记，避免遮挡鱼体轮廓。
    marker_radius = max(3, min(6, short_side // 14))
    padding = marker_radius + 4

    # 旋转后的轮廓端点经常紧贴画布边缘。先扩充画布再绘制，避免端点圆环被裁掉。
    visual = cv2.copyMakeBorder(
        image,
        padding,
        padding,
        padding,
        padding,
        cv2.BORDER_CONSTANT,
        value=(0, 0, 0),
    )
    shifted_contour = fish_contour.copy()
    shifted_contour[:, :, 0] += padding
    shifted_contour[:, :, 1] += padding
    shifted_full_coefficients = _shift_quadratic_coefficients(
        full_coefficients, padding, padding
    )
    shifted_body_coefficients = _shift_quadratic_coefficients(
        body_coefficients, padding, padding
    )

    def shifted_point(point):
        return (
            int(round(float(point[0]))) + padding,
            int(round(float(point[1]))) + padding,
        )

    cv2.drawContours(visual, [shifted_contour], -1, (235, 235, 235), 1, cv2.LINE_AA)
    # 黑色描边让线条在亮色鱼体上仍然清晰。
    _draw_quadratic_segment(
        visual,
        shifted_full_coefficients,
        full_start[0] + padding,
        full_end[0] + padding,
        outline_color,
        total_thickness + 2,
    )
    _draw_quadratic_segment(
        visual,
        shifted_full_coefficients,
        full_start[0] + padding,
        full_end[0] + padding,
        total_color,
        total_thickness,
    )
    _draw_dashed_quadratic_segment(
        visual,
        shifted_body_coefficients,
        body_start[0] + padding,
        tail_cut[0] + padding,
        outline_color,
        body_thickness + 2,
        dash_length,
        gap_length,
    )
    _draw_dashed_quadratic_segment(
        visual,
        shifted_body_coefficients,
        body_start[0] + padding,
        tail_cut[0] + padding,
        body_color,
        body_thickness,
        dash_length,
        gap_length,
    )
    # 几何最窄位置的完整横截线，比只画中心点更容易核对是否落在
    # 鱼身与尾鳍的交界处。
    if tail_cut_line is not None:
        cv2.line(
            visual,
            shifted_point(tail_cut_line[0]),
            shifted_point(tail_cut_line[1]),
            outline_color,
            body_thickness + 2,
            cv2.LINE_AA,
        )
        cv2.line(
            visual,
            shifted_point(tail_cut_line[0]),
            shifted_point(tail_cut_line[1]),
            tail_cut_color,
            body_thickness,
            cv2.LINE_AA,
        )

    # 体高使用最小旋转外接矩形短边的两个端点。先画细连线，
    # 再画端点，既能看清测量方向，也不会大面积遮挡鱼体。
    if body_height_line is not None:
        height_start = shifted_point(body_height_line[0])
        height_end = shifted_point(body_height_line[1])
        cv2.line(
            visual,
            height_start,
            height_end,
            outline_color,
            body_thickness + 2,
            cv2.LINE_AA,
        )
        cv2.line(
            visual,
            height_start,
            height_end,
            body_height_color,
            body_thickness,
            cv2.LINE_AA,
        )
        height_marker_radius = max(2, marker_radius - 1)
        for height_point in (height_start, height_end):
            cv2.circle(
                visual,
                height_point,
                height_marker_radius + 2,
                outline_color,
                -1,
                cv2.LINE_AA,
            )
            cv2.circle(
                visual,
                height_point,
                height_marker_radius,
                body_height_color,
                -1,
                cv2.LINE_AA,
            )

    # 先画尾部截断点和无尾体长起点，最后画全长端点圆环。即使两个位置
    # 非常接近，黄色截断点和红色鱼首圆环也都能看见。
    tail_cut_center = shifted_point(tail_cut)
    cv2.circle(
        visual,
        tail_cut_center,
        marker_radius + 2,
        outline_color,
        -1,
        cv2.LINE_AA,
    )
    cv2.circle(
        visual,
        tail_cut_center,
        marker_radius,
        tail_cut_color,
        -1,
        cv2.LINE_AA,
    )

    # 无尾体长起点通常与鱼头的全长端点重合，用较小青点标出共享端点。
    cv2.circle(
        visual,
        shifted_point(body_start),
        max(2, marker_radius // 2),
        body_color,
        -1,
        cv2.LINE_AA,
    )
    # body_start 与鱼首的全长端点重合；用距离匹配以兼容浮点坐标。
    head_point = min(
        (full_start, full_end),
        key=lambda point: calculate_distance(point, body_start),
    )
    for point in (full_start, full_end):
        center = shifted_point(point)
        point_color = (
            head_color if point is head_point else tail_endpoint_color
        )
        cv2.circle(
            visual,
            center,
            marker_radius + 2,
            outline_color,
            3,
            cv2.LINE_AA,
        )
        cv2.circle(
            visual,
            center,
            marker_radius,
            point_color,
            2,
            cv2.LINE_AA,
        )
    return visual


def add_arched_back_keypoints_to_visualization(
    *,
    crop_path: Path | str,
    mask_path: Path | str,
    visualization_path: Path | str,
    keypoints,
) -> list[tuple[int, int]]:
    """将原始 crop 坐标系中的两个弓背区间端点写入体长可视化。

    ``arched-back.py`` 直接在已对齐 crop 上计算关键点，而
    ``*_with_line.jpg`` 使用本文件内的二次水平旋转结果，并在
    四周增加了边框。因此这里重用完全相同的旋转矩阵和边框
    计算，再用与弓背 overlay 一致的橙色圆环标出两个端点。

    返回两个点在最终体长图中的整数像素坐标。
    """

    cv2 = _cv2()
    np = _np()
    crop_path = Path(crop_path)
    mask_path = Path(mask_path)
    visualization_path = Path(visualization_path)

    crop_image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
    if crop_image is None:
        raise ValueError(f"cannot read crop image: {crop_path}")
    mask_image = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    if mask_image is None:
        raise ValueError(f"cannot read mask image: {mask_path}")
    if crop_image.shape[:2] != mask_image.shape[:2]:
        raise ValueError(
            f"image/mask size mismatch: {crop_image.shape[:2]} vs "
            f"{mask_image.shape[:2]}"
        )

    points = np.asarray(keypoints, dtype=float)
    if points.shape != (2, 2) or not np.all(np.isfinite(points)):
        raise ValueError(
            "arched-back keypoints must contain exactly two finite (x, y) points"
        )

    rotated_image, _rotated_mask, _contour, rotation_matrix = (
        rotate_image_and_mask_to_horizontal(
            crop_image,
            mask_image,
            return_matrix=True,
        )
    )
    visual = cv2.imread(str(visualization_path), cv2.IMREAD_COLOR)
    if visual is None:
        raise ValueError(
            f"cannot read fish-condition visualization: {visualization_path}"
        )

    rotated_height, rotated_width = rotated_image.shape[:2]
    short_side = max(1, min(rotated_height, rotated_width))
    measurement_marker_radius = max(3, min(6, short_side // 14))
    padding = measurement_marker_radius + 4
    expected_shape = (
        rotated_height + 2 * padding,
        rotated_width + 2 * padding,
    )
    if visual.shape[:2] != expected_shape:
        raise ValueError(
            "fish-condition visualization size does not match its crop/mask "
            f"transform: actual={visual.shape[:2]}, expected={expected_shape}"
        )

    homogeneous_points = np.column_stack(
        (points, np.ones(points.shape[0], dtype=float))
    )
    mapped_points = homogeneous_points @ np.asarray(rotation_matrix, dtype=float).T
    mapped_points += np.asarray([padding, padding], dtype=float)
    mapped_points = np.rint(mapped_points).astype(np.int32)

    interval_marker_radius = max(3, min(5, short_side // 40))
    for x_value, y_value in mapped_points:
        center = (int(x_value), int(y_value))
        if not (
            0 <= center[0] < visual.shape[1]
            and 0 <= center[1] < visual.shape[0]
        ):
            raise ValueError(
                f"mapped arched-back keypoint is outside visualization: {center}"
            )
        # 先画黑色外环，保证橙色关键点在鱼背和体长线上都清晰。
        cv2.circle(
            visual,
            center,
            interval_marker_radius + 2,
            (0, 0, 0),
            3,
            cv2.LINE_AA,
        )
        cv2.circle(
            visual,
            center,
            interval_marker_radius,
            (0, 128, 255),
            2,
            cv2.LINE_AA,
        )

    ensure_dir(visualization_path.parent)
    temp_path = visualization_path.with_name(
        f"{visualization_path.stem}.tmp{visualization_path.suffix}"
    )
    try:
        if not cv2.imwrite(str(temp_path), visual):
            raise OSError(
                f"cannot write fish-condition visualization: {temp_path}"
            )
        os.replace(temp_path, visualization_path)
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise

    return [tuple(int(value) for value in point) for point in mapped_points]


def _write_measurement_json(
    save_path: Path,
    *,
    body_length_cm: float,
    total_length_cm: float,
    body_height_cm: float,
) -> Path:
    """原子写入单条鱼的厘米制几何测量结果。"""

    measurement_path = save_path / "measurements.json"
    temp_path = measurement_path.with_suffix(measurement_path.suffix + ".tmp")
    payload = {
        "body_length_cm": round(float(body_length_cm), 4),
        "total_length_cm": round(float(total_length_cm), 4),
        "body_height_cm": round(float(body_height_cm), 4),
    }
    try:
        with open(temp_path, "w", encoding="utf-8") as json_file:
            json.dump(payload, json_file, ensure_ascii=False, indent=2)
            json_file.write("\n")
        os.replace(temp_path, measurement_path)
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return measurement_path


def _measure_mask_cross_section(fish_mask, coefficients, x_value):
    """测量中轴线在 ``x_value`` 处的垂直鱼体宽度。

    从中轴点沿法线向两侧采样，只使用与中轴点最接近的连通 mask
    区段，因此不会把远处背鳍/腹鳍的独立交点并入横截面。
    """

    np = _np()
    mask_bool = np.asarray(fish_mask) > 0
    image_height, image_width = mask_bool.shape[:2]
    a, b, _c = np.asarray(coefficients, dtype=float)
    center_x = float(x_value)
    center_y = float(quadratic_function(center_x, coefficients))
    if not (0.0 <= center_x < image_width and 0.0 <= center_y < image_height):
        return None

    slope = 2.0 * float(a) * center_x + float(b)
    normal = np.asarray([-slope, 1.0], dtype=float)
    normal_norm = float(np.linalg.norm(normal))
    if not math.isfinite(normal_norm) or normal_norm <= EPS:
        return None
    normal /= normal_norm

    radius = int(math.ceil(math.hypot(image_height, image_width)))
    distances = np.arange(-radius, radius + 1, dtype=float)
    sample_x = np.rint(center_x + distances * normal[0]).astype(np.int32)
    sample_y = np.rint(center_y + distances * normal[1]).astype(np.int32)
    valid = (
        (sample_x >= 0)
        & (sample_x < image_width)
        & (sample_y >= 0)
        & (sample_y < image_height)
    )
    inside = np.zeros(distances.shape, dtype=bool)
    inside[valid] = mask_bool[sample_y[valid], sample_x[valid]]

    transitions = np.diff(
        np.concatenate(
            [
                np.asarray([False]),
                inside,
                np.asarray([False]),
            ]
        ).astype(np.int8)
    )
    run_starts = np.flatnonzero(transitions == 1)
    run_ends = np.flatnonzero(transitions == -1) - 1
    if run_starts.size == 0:
        return None

    # 拟合中轴线可能因轮廓噪声偏离 mask 1~2 px，选择离中轴点
    # 最近的连通区段，但偏离过大时拒绝该横截面。
    closest_run = min(
        range(len(run_starts)),
        key=lambda index: float(
            np.min(np.abs(distances[run_starts[index] : run_ends[index] + 1]))
        ),
    )
    run_start = int(run_starts[closest_run])
    run_end = int(run_ends[closest_run])
    center_distance = float(np.min(np.abs(distances[run_start : run_end + 1])))
    maximum_center_offset = max(2.0, 0.01 * min(image_height, image_width))
    if center_distance > maximum_center_offset:
        return None

    point1 = np.asarray(
        [
            center_x + distances[run_start] * normal[0],
            center_y + distances[run_start] * normal[1],
        ],
        dtype=float,
    )
    point2 = np.asarray(
        [
            center_x + distances[run_end] * normal[0],
            center_y + distances[run_end] * normal[1],
        ],
        dtype=float,
    )
    width = float(np.linalg.norm(point2 - point1) + 1.0)
    if not math.isfinite(width) or width <= 0.0:
        return None
    return width, tuple(point1), tuple(point2)


def _median_smooth_widths(widths, window_size):
    """对宽度曲线做滑动中值滤波，消除单个 mask 毛刺。"""

    np = _np()
    values = np.asarray(widths, dtype=float)
    if values.ndim != 1 or values.size == 0:
        raise ValueError("tail width profile is empty")
    window_size = max(1, min(int(window_size), int(values.size)))
    if window_size % 2 == 0:
        window_size = window_size - 1 if window_size == values.size else window_size + 1
    window_size = max(1, min(window_size, int(values.size)))
    radius = window_size // 2
    return np.asarray(
        [
            np.median(values[max(0, index - radius) : min(values.size, index + radius + 1)])
            for index in range(values.size)
        ],
        dtype=float,
    )


def _find_tail_cut_from_width_profile(
    fish_mask,
    coefficients,
    left_x,
    right_x,
    tail_side,
    *,
    search_ratio=TAIL_WIDTH_SEARCH_RATIO,
    tip_exclusion_ratio=TAIL_TIP_EXCLUSION_RATIO,
):
    """从尾鳍末端向鱼身搜索稳定最窄的横截面。

    优先使用平滑宽度曲线的局部低谷，并要求继续向鱼身方向移动后
    的中位宽度至少增加 15%。若尾鳍宽度从尾端开始单调增加、不存在
    局部低谷，则以尾端窄段的中位宽度为基准，取宽度开始持续增大
    的位置。这样不会因为尾鳍末端本身窄而把截断线放在尾尖附近。

    整个选择过程只使用明确的宽度和持续增大条件，不使用加权评分。
    """

    np = _np()
    if tail_side not in {"left", "right"}:
        raise ValueError(f"invalid tail_side: {tail_side}")
    if not 0.0 < float(search_ratio) < 1.0:
        raise ValueError(f"invalid tail search ratio: {search_ratio}")
    if not 0.0 <= float(tip_exclusion_ratio) < float(search_ratio):
        raise ValueError(f"invalid tail tip exclusion ratio: {tip_exclusion_ratio}")

    left_x = float(left_x)
    right_x = float(right_x)
    fish_length = right_x - left_x
    if fish_length <= 0.0:
        raise ValueError(f"invalid fish length: left={left_x}, right={right_x}")

    if tail_side == "right":
        first_x = int(math.floor(right_x - fish_length * tip_exclusion_ratio))
        last_x = int(math.ceil(right_x - fish_length * search_ratio))
        scan_x_values = range(first_x, last_x - 1, -1)
    else:
        first_x = int(math.ceil(left_x + fish_length * tip_exclusion_ratio))
        last_x = int(math.floor(left_x + fish_length * search_ratio))
        scan_x_values = range(first_x, last_x + 1)

    records = []
    for x_value in scan_x_values:
        section = _measure_mask_cross_section(fish_mask, coefficients, x_value)
        if section is None:
            continue
        width, point1, point2 = section
        records.append(
            {
                "x": int(x_value),
                "width": float(width),
                "point1": point1,
                "point2": point2,
            }
        )

    if len(records) < TAIL_MIN_CROSS_SECTIONS:
        raise ValueError(
            "not enough valid tail cross-sections: "
            f"count={len(records)}, required={TAIL_MIN_CROSS_SECTIONS}"
        )

    smoothing_window = max(5, int(round(fish_length * TAIL_WIDTH_SMOOTH_RATIO)))
    if smoothing_window % 2 == 0:
        smoothing_window += 1
    smoothed_widths = _median_smooth_widths(
        [record["width"] for record in records],
        smoothing_window,
    )
    for record, smoothed_width in zip(records, smoothed_widths):
        record["smoothed_width"] = float(smoothed_width)

    expansion_span = max(
        3,
        int(round(fish_length * TAIL_BODY_EXPANSION_SPAN_RATIO)),
    )
    expansion_span = min(expansion_span, max(3, len(records) // 3))
    candidates = []
    for index in range(expansion_span, len(records) - expansion_span):
        current_width = float(smoothed_widths[index])
        is_local_minimum = (
            current_width <= float(smoothed_widths[index - 1])
            and current_width <= float(smoothed_widths[index + 1])
        )
        if not is_local_minimum:
            continue
        tailward_widths = smoothed_widths[index - expansion_span : index]
        tailward_median = float(np.median(tailward_widths))
        if tailward_median < current_width:
            # 尾端到鱼身一直单调变宽时，中值滤波形成的
            # 短平台不应误判为真实局部低谷。
            continue
        bodyward_widths = smoothed_widths[index + 1 : index + expansion_span + 1]
        bodyward_median = float(np.median(bodyward_widths))
        if bodyward_median < current_width * TAIL_BODY_EXPANSION_RATIO:
            continue
        candidates.append(index)

    # 不打分：有稳定低谷时在低谷中取最窄者。
    if candidates:
        selected_index = min(
            candidates,
            key=lambda index: float(smoothed_widths[index]),
        )
        selection_rule = "stable_local_minimum"
    else:
        # 对单调变宽的尾鳍，全局最小值几乎总在尾尖。先用最靠近
        # 尾端的一段横截面估计“窄尾基线”，再寻找向鱼身方向持续
        # 增宽到基线 125% 的第一段。回退半个窗口取增宽起点，
        # 而不是窗口中点。
        baseline_span = min(
            expansion_span,
            max(3, len(records) // 4),
        )
        tail_baseline = float(np.median(smoothed_widths[:baseline_span]))
        expansion_threshold = (
            tail_baseline * TAIL_MONOTONIC_EXPANSION_RATIO
        )
        selected_index = None
        for index in range(
            baseline_span,
            len(records) - expansion_span + 1,
        ):
            bodyward_median = float(
                np.median(smoothed_widths[index : index + expansion_span])
            )
            if bodyward_median < expansion_threshold:
                continue
            selected_index = max(0, index - expansion_span // 2)
            break

        if selected_index is None:
            # 整个尾部搜索区都没有明显变宽时才使用最窄点，
            # 保证特殊 mask 仍然可以完成测量。
            selected_index = int(np.argmin(smoothed_widths))
            selection_rule = "global_minimum_fallback"
        else:
            selection_rule = "monotonic_expansion_boundary"
    selected = dict(records[selected_index])
    selected.update(
        {
            "tail_side": tail_side,
            "search_start_x": int(first_x),
            "search_end_x": int(last_x),
            "smoothing_window": int(smoothing_window),
            "expansion_span": int(expansion_span),
            "selection_rule": selection_rule,
            "records": records,
        }
    )
    return selected


def _find_tail_color_transition(
    image,
    fish_mask,
    coefficients,
    geometry_cut_x,
    left_x,
    right_x,
    tail_side,
    *,
    tip_exclusion_ratio=TAIL_TIP_EXCLUSION_RATIO,
):
    """从几何截断点向尾端穷举并寻找最大 Lab 颜色突变。

    每个候选点都以当前中轴线切线为纵轴，在截断点的鱼身侧和
    尾鳍侧各取一个窗口。窗口只使用 fish mask 内、横截面中间 60%
    左右的像素，避免轮廓边缘、背鳍/腹鳍和传送带背景干扰。

    返回的 ``x`` 是最终截断位置。只要候选点能够取得满足最少像素数
    的完整鱼身侧和尾鳍侧窗口，就参与比较；不再应用颜色阈值、内部
    噪声、鱼身基准色、连续区间或尾向一致性筛选。程序直接选择全部
    有效窗口中原始 Delta E 最大的点；没有有效窗口时才使用
    ``geometry_cut_x`` 回退。
    """

    cv2 = _cv2()
    np = _np()
    if tail_side not in {"left", "right"}:
        raise ValueError(f"invalid tail_side: {tail_side}")

    mask_bool = np.asarray(fish_mask) > 0
    if mask_bool.ndim != 2:
        raise ValueError("fish mask must be a two-dimensional image")
    if image.shape[:2] != mask_bool.shape:
        raise ValueError(
            "image and fish mask shapes do not match: "
            f"image={image.shape[:2]}, mask={mask_bool.shape}"
        )

    left_x = float(left_x)
    right_x = float(right_x)
    fish_length = right_x - left_x
    if fish_length <= 0.0:
        raise ValueError(f"invalid fish length: left={left_x}, right={right_x}")

    geometry_cut_x = int(round(float(geometry_cut_x)))
    window_length = max(5, int(round(fish_length * TAIL_COLOR_WINDOW_RATIO)))
    gap_length = max(1, int(round(fish_length * TAIL_COLOR_GAP_RATIO)))
    direction = 1 if tail_side == "right" else -1
    # 候选点的尾侧颜色窗口也必须留在“排除尾尖”之外。
    tail_tip_padding = fish_length * float(tip_exclusion_ratio)
    if tail_side == "right":
        last_candidate_x = int(
            math.floor(
                right_x - tail_tip_padding - gap_length - window_length
            )
        )
        candidate_x_values = range(
            geometry_cut_x,
            last_candidate_x + 1,
        ) if geometry_cut_x <= last_candidate_x else range(0)
    else:
        last_candidate_x = int(
            math.ceil(
                left_x + tail_tip_padding + gap_length + window_length
            )
        )
        candidate_x_values = range(
            geometry_cut_x,
            last_candidate_x - 1,
            -1,
        ) if geometry_cut_x >= last_candidate_x else range(0)

    # OpenCV Lab 转为标准量纲：L in [0, 100]，a/b 以 0 为中心。
    lab_image = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab_image[:, :, 0] *= 100.0 / 255.0
    lab_image[:, :, 1:] -= 128.0
    mask_y, mask_x = np.nonzero(mask_bool)
    mask_colors = lab_image[mask_y, mask_x]

    records = []
    a, b, _c = np.asarray(coefficients, dtype=float)
    for x_value in candidate_x_values:
        section = _measure_mask_cross_section(
            fish_mask,
            coefficients,
            x_value,
        )
        if section is None:
            continue
        section_width, point1, point2 = section
        center_x = float(x_value)
        center_y = float(quadratic_function(center_x, coefficients))

        tangent = np.asarray(
            [1.0, 2.0 * float(a) * center_x + float(b)],
            dtype=float,
        )
        tangent_norm = float(np.linalg.norm(tangent))
        if not math.isfinite(tangent_norm) or tangent_norm <= EPS:
            continue
        tail_tangent = direction * tangent / tangent_norm
        normal = np.asarray([-tail_tangent[1], tail_tangent[0]], dtype=float)

        delta_x = mask_x.astype(float) - center_x
        delta_y = mask_y.astype(float) - center_y
        longitudinal = delta_x * tail_tangent[0] + delta_y * tail_tangent[1]
        normal_distance = delta_x * normal[0] + delta_y * normal[1]
        central_half_width = max(
            2.0,
            float(section_width) * TAIL_COLOR_CENTER_HALF_WIDTH_RATIO,
        )
        in_central_band = np.abs(normal_distance) <= central_half_width
        body_selector = (
            in_central_band
            & (longitudinal >= -(gap_length + window_length))
            & (longitudinal <= -gap_length)
        )
        tail_selector = (
            in_central_band
            & (longitudinal >= gap_length)
            & (longitudinal <= gap_length + window_length)
        )
        body_count = int(np.count_nonzero(body_selector))
        tail_count = int(np.count_nonzero(tail_selector))
        if (
            body_count < TAIL_COLOR_MIN_PIXELS
            or tail_count < TAIL_COLOR_MIN_PIXELS
        ):
            continue

        body_colors = mask_colors[body_selector]
        tail_colors = mask_colors[tail_selector]
        body_median = np.median(body_colors, axis=0)
        tail_median = np.median(tail_colors, axis=0)
        delta_e = float(np.linalg.norm(body_median - tail_median))
        records.append(
            {
                "x": int(x_value),
                "delta_e": delta_e,
                "body_pixel_count": body_count,
                "tail_pixel_count": tail_count,
                "body_lab": tuple(float(value) for value in body_median),
                "tail_lab": tuple(float(value) for value in tail_median),
                "point1": point1,
                "point2": point2,
                "width": float(section_width),
            }
        )

    fallback = {
        "x": int(geometry_cut_x),
        "geometry_cut_x": int(geometry_cut_x),
        "selection_rule": "geometry_fallback",
        "selection_strategy": "no_valid_color_window",
        "delta_e": None,
        "threshold": None,
        "window_length": int(window_length),
        "gap_length": int(gap_length),
        "peak_x": None,
        "peak_delta_e": None,
        "near_peak_threshold": None,
        "near_peak_ratio": None,
        "records": records,
    }
    if not records:
        return fallback

    # 不做可靠性筛选，直接在所有具有完整有效颜色窗口的候选点中
    # 选择原始 Lab Delta E 最大值。records 保持从几何点向尾端的
    # 搜索顺序，因此并列最大时自然选择最靠近鱼身的第一个点。
    peak_index = max(
        range(len(records)),
        key=lambda index: float(records[index]["delta_e"]),
    )
    peak_delta_e = float(records[peak_index]["delta_e"])
    selected = dict(records[peak_index])
    selected.update(
        {
            "geometry_cut_x": int(geometry_cut_x),
            "selection_rule": "color_transition",
            "selection_strategy": "maximum_raw_delta_e_no_reliability_filter",
            "threshold": None,
            "peak_x": int(records[peak_index]["x"]),
            "peak_delta_e": peak_delta_e,
            "near_peak_threshold": None,
            "near_peak_ratio": None,
            "window_length": int(window_length),
            "gap_length": int(gap_length),
            "records": records,
        }
    )
    return selected


def _save_tail_lab_color_profile(
    output_path: Path,
    *,
    records,
    geometry_cut_x: int,
    final_tail_cut_x: int,
    annotated_tail_cut_x: float | None,
    threshold: float | None,
) -> dict:
    """保存几何最窄点向尾端搜索的 Lab 色差曲线。

    横轴为中轴线候选点的 x 像素坐标，纵轴为候选点鱼身侧与
    尾鳍侧颜色窗口的原始 Lab Delta E。蓝色曲线表示原始色差，
    橙色叉号表示原始 Delta E 最大点，绿色圆环表示算法最终选择的
    截断点，红色实心点表示 JSON 人工标注的真实尾部截断点横坐标。
    不计算或绘制平滑色差曲线。
    """

    cv2 = _cv2()
    np = _np()
    canvas_width = 1100
    canvas_height = 650
    margin_left = 90
    margin_right = 40
    margin_top = 115
    margin_bottom = 90
    plot_width = canvas_width - margin_left - margin_right
    plot_height = canvas_height - margin_top - margin_bottom
    canvas = np.full(
        (canvas_height, canvas_width, 3),
        255,
        dtype=np.uint8,
    )

    samples = []
    for record in records:
        try:
            x_value = float(record["x"])
            raw_delta_e = float(record["delta_e"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(x_value) and math.isfinite(raw_delta_e)):
            continue
        samples.append(
            {
                "x": x_value,
                "raw_delta_e": raw_delta_e,
            }
        )
    samples.sort(key=lambda sample: sample["x"])

    x_values_for_range = [float(geometry_cut_x), float(final_tail_cut_x)]
    x_values_for_range.extend(sample["x"] for sample in samples)
    if annotated_tail_cut_x is not None and math.isfinite(
        float(annotated_tail_cut_x)
    ):
        x_values_for_range.append(float(annotated_tail_cut_x))
    x_min = min(x_values_for_range)
    x_max = max(x_values_for_range)
    if x_max <= x_min:
        x_min -= 1.0
        x_max += 1.0
    # 数据横坐标范围较小时扩展到至少 60 px，使每 4 px 的刻度在
    # 固定画布中排列得更紧密，同时给两端标记留出可视空白。
    x_span = x_max - x_min
    if x_span < TAIL_COLOR_PROFILE_MIN_X_SPAN:
        x_center = (x_min + x_max) / 2.0
        x_min = float(
            math.floor(x_center - TAIL_COLOR_PROFILE_MIN_X_SPAN / 2.0)
        )
        x_max = x_min + TAIL_COLOR_PROFILE_MIN_X_SPAN

    y_values = [0.0]
    for sample in samples:
        y_values.append(sample["raw_delta_e"])
    if threshold is not None and math.isfinite(float(threshold)):
        y_values.append(float(threshold))
    y_max = max(y_values)
    y_max = max(1.0, y_max * 1.12)

    def plot_point(x_value: float, y_value: float) -> tuple[int, int]:
        x_ratio = (float(x_value) - x_min) / (x_max - x_min)
        y_ratio = max(0.0, min(1.0, float(y_value) / y_max))
        return (
            int(round(margin_left + x_ratio * plot_width)),
            int(round(margin_top + (1.0 - y_ratio) * plot_height)),
        )

    font = cv2.FONT_HERSHEY_SIMPLEX
    grid_color = (225, 225, 225)
    axis_color = (40, 40, 40)
    # 横轴按真实像素坐标每 4 px 一个刻度，比原先固定 6 个刻度更密。
    first_x_tick = int(math.ceil(x_min))
    last_x_tick = int(math.floor(x_max))
    for x_value in range(
        first_x_tick,
        last_x_tick + 1,
        TAIL_COLOR_PROFILE_X_TICK_STEP,
    ):
        x_pixel = plot_point(float(x_value), 0.0)[0]
        cv2.line(
            canvas,
            (x_pixel, margin_top),
            (x_pixel, margin_top + plot_height),
            grid_color,
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            canvas,
            str(x_value),
            (x_pixel - 15, margin_top + plot_height + 28),
            font,
            0.48,
            axis_color,
            1,
            cv2.LINE_AA,
        )

    for tick_index in range(6):
        ratio = tick_index / 5.0
        y_value = ratio * y_max
        y_pixel = int(round(margin_top + (1.0 - ratio) * plot_height))
        cv2.line(
            canvas,
            (margin_left, y_pixel),
            (margin_left + plot_width, y_pixel),
            grid_color,
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            canvas,
            f"{y_value:.1f}",
            (15, y_pixel + 5),
            font,
            0.48,
            axis_color,
            1,
            cv2.LINE_AA,
        )

    cv2.line(
        canvas,
        (margin_left, margin_top),
        (margin_left, margin_top + plot_height),
        axis_color,
        2,
        cv2.LINE_AA,
    )
    cv2.line(
        canvas,
        (margin_left, margin_top + plot_height),
        (margin_left + plot_width, margin_top + plot_height),
        axis_color,
        2,
        cv2.LINE_AA,
    )

    raw_color = (210, 90, 30)
    if samples:
        raw_points = np.asarray(
            [
                plot_point(sample["x"], sample["raw_delta_e"])
                for sample in samples
            ],
            dtype=np.int32,
        ).reshape(-1, 1, 2)
        if len(samples) >= 2:
            cv2.polylines(
                canvas,
                [raw_points],
                False,
                raw_color,
                3,
                cv2.LINE_AA,
            )
        else:
            cv2.circle(
                canvas,
                tuple(raw_points[0, 0]),
                4,
                raw_color,
                -1,
            )

    if threshold is not None and math.isfinite(float(threshold)):
        threshold_y = plot_point(x_min, float(threshold))[1]
        dash_length = 12
        gap_length = 8
        cursor = margin_left
        while cursor < margin_left + plot_width:
            dash_end = min(cursor + dash_length, margin_left + plot_width)
            cv2.line(
                canvas,
                (cursor, threshold_y),
                (dash_end, threshold_y),
                (180, 0, 180),
                1,
                cv2.LINE_AA,
            )
            cursor = dash_end + gap_length

    maximum_sample = (
        max(samples, key=lambda sample: sample["raw_delta_e"])
        if samples
        else None
    )
    max_x = None
    max_delta_e = None
    if maximum_sample is not None:
        max_x = int(round(maximum_sample["x"]))
        max_delta_e = float(maximum_sample["raw_delta_e"])
        max_center = plot_point(
            maximum_sample["x"],
            maximum_sample["raw_delta_e"],
        )
        marker_size = 8
        cv2.line(
            canvas,
            (max_center[0] - marker_size, max_center[1] - marker_size),
            (max_center[0] + marker_size, max_center[1] + marker_size),
            (0, 140, 255),
            3,
            cv2.LINE_AA,
        )
        cv2.line(
            canvas,
            (max_center[0] - marker_size, max_center[1] + marker_size),
            (max_center[0] + marker_size, max_center[1] - marker_size),
            (0, 140, 255),
            3,
            cv2.LINE_AA,
        )

    # 算法最终截断点使用绿色圆环。若它与橙色最大值叉号重合，
    # 圆环位于叉号外侧，因此两个标记仍然都能看见。
    final_delta_e = None
    if samples:
        final_sample = min(
            samples,
            key=lambda sample: abs(sample["x"] - float(final_tail_cut_x)),
        )
        final_delta_e = float(final_sample["raw_delta_e"])
    algorithm_center = plot_point(
        float(final_tail_cut_x),
        0.0 if final_delta_e is None else final_delta_e,
    )
    cv2.circle(canvas, algorithm_center, 12, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.circle(canvas, algorithm_center, 9, (0, 180, 0), 3, cv2.LINE_AA)

    annotated_delta_e = None
    if annotated_tail_cut_x is not None and math.isfinite(
        float(annotated_tail_cut_x)
    ):
        if samples:
            annotated_sample = min(
                samples,
                key=lambda sample: abs(
                    sample["x"] - float(annotated_tail_cut_x)
                ),
            )
            annotated_delta_e = float(annotated_sample["raw_delta_e"])
        else:
            annotated_delta_e = 0.0
        annotated_center = plot_point(
            float(annotated_tail_cut_x),
            annotated_delta_e,
        )
        cv2.circle(canvas, annotated_center, 8, (0, 0, 0), -1, cv2.LINE_AA)
        cv2.circle(canvas, annotated_center, 5, (0, 0, 255), -1, cv2.LINE_AA)

    # 误差统一以“算法最终截断点 - 人工标注点”计算。
    # 即使没有有效 Lab 样本、算法回退到几何截断点，也仍然
    # 可以与人工标注比较。原始色差最大点的误差另行保留。
    algorithm_distance_px = (
        None
        if annotated_tail_cut_x is None
        else abs(float(final_tail_cut_x) - float(annotated_tail_cut_x))
    )
    raw_distance_px = (
        None
        if max_x is None or annotated_tail_cut_x is None
        else abs(float(max_x) - float(annotated_tail_cut_x))
    )
    cv2.putText(
        canvas,
        "Tail Lab Color Difference Profile",
        (margin_left, 32),
        font,
        0.82,
        axis_color,
        2,
        cv2.LINE_AA,
    )
    summary = (
        f"geometry x={int(geometry_cut_x)}   raw max x={max_x if max_x is not None else 'N/A'}"
        f"   algorithm x={int(final_tail_cut_x)}"
    )
    if annotated_tail_cut_x is None:
        legend_text = (
            "blue: raw   orange X: raw max   green ring: algorithm   "
            "red: no annotation"
        )
    else:
        distance_text = (
            "N/A"
            if algorithm_distance_px is None
            else f"{algorithm_distance_px:.1f}"
        )
        legend_text = (
            "blue: raw   orange X: raw max   green ring: algorithm   "
            f"red: annotated x={float(annotated_tail_cut_x):.1f}   "
            f"|algorithm-ann|={distance_text} px"
        )
    cv2.putText(
        canvas,
        summary,
        (margin_left, 64),
        font,
        0.58,
        axis_color,
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        legend_text,
        (margin_left, 94),
        font,
        0.40,
        axis_color,
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "Centerline x coordinate (pixel)",
        (margin_left + plot_width // 2 - 120, canvas_height - 18),
        font,
        0.58,
        axis_color,
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "Lab color difference (Delta E)",
        (margin_left, margin_top - 10),
        font,
        0.52,
        axis_color,
        1,
        cv2.LINE_AA,
    )
    if not samples:
        cv2.putText(
            canvas,
            "No valid Lab color samples",
            (margin_left + 280, margin_top + plot_height // 2),
            font,
            0.75,
            (0, 0, 180),
            2,
            cv2.LINE_AA,
        )

    ensure_dir(output_path.parent)
    if not cv2.imwrite(str(output_path), canvas):
        raise OSError(f"cannot write tail Lab color profile: {output_path}")
    return {
        "path": output_path,
        "max_x": max_x,
        "max_delta_e": max_delta_e,
        "raw_max_x": max_x,
        "raw_max_delta_e": max_delta_e,
        "final_tail_cut_x": int(final_tail_cut_x),
        "final_delta_e": final_delta_e,
        "annotated_tail_cut_x": (
            None
            if annotated_tail_cut_x is None
            else round(float(annotated_tail_cut_x), 4)
        ),
        "annotated_delta_e": annotated_delta_e,
        "distance_px": (
            None
            if algorithm_distance_px is None
            else round(float(algorithm_distance_px), 4)
        ),
        "algorithm_distance_px": (
            None
            if algorithm_distance_px is None
            else round(float(algorithm_distance_px), 4)
        ),
        "raw_distance_px": (
            None
            if raw_distance_px is None
            else round(float(raw_distance_px), 4)
        ),
    }


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


def rotate_image_and_mask_to_horizontal(image, mask, *, return_matrix=False):
    cv2 = _cv2()
    np = _np()
    if mask.ndim == 3:
        gray_mask = cv2.cvtColor(mask, cv2.COLOR_BGR2GRAY)
    else:
        gray_mask = mask.copy()

    _, thresh = cv2.threshold(gray_mask, 127, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        result = (image, thresh, None)
        if return_matrix:
            identity = np.asarray([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
            return (*result, identity)
        return result

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
    result = (rotated_image, rotated_mask, contour)
    if return_matrix:
        return (*result, matrix)
    return result


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
    """将点集旋转 ``rad`` 弧度，用于搜索最小外接矩形。"""

    matrix = _np().asarray(
        [
            [math.cos(rad), math.sin(rad)],
            [-math.sin(rad), math.cos(rad)],
        ]
    )
    return _np().dot(poly, matrix)


def min_rect(poly):
    """返回当前角度下的轴对齐外接矩形及其面积。"""

    np = _np()
    x_min, y_min = np.min(poly, axis=0)
    x_max, y_max = np.max(poly, axis=0)
    min_rect_ = np.asarray(
        [
            [x_min, y_min],
            [x_max, y_min],
            [x_max, y_max],
            [x_min, y_max],
        ]
    )
    area = (x_max - x_min) * (y_max - y_min)
    return min_rect_, area


def get_rad(poly):
    """取点集相邻边的倾斜角。"""

    rad = []
    for index in range(poly.shape[0]):
        vector = poly[index - 1] - poly[index]
        rad.append(math.atan(vector[1] / (vector[0] + EPS)))
    return rad


def min_rotate_rect(poly):
    """按 ``fish_condition_8.14.py`` 的方法计算旋转最小外接矩形。"""

    rect_min, area_min = min_rect(poly)
    rad_min = 0.0
    for radian in get_rad(poly):
        new_poly = rotate_poly(poly, -radian)
        rect, area = min_rect(new_poly)
        if area < area_min:
            rect_min, area_min, rad_min = rect, area, radian
    return rotate_poly(rect_min, rad_min)


# ---------------------------------------------------------------------------
# 单张鱼的处理
# ---------------------------------------------------------------------------


def process_single_image(
    image_path: Path,
    mask_path: Path,
    output_root: Path,
    mm_per_pixel: float,
    track_id: int,
    frame_index: int,
):
    """对一张完整鱼计算含尾全长、无尾体长、体高、体重和肥满度。"""

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

    # 重跑旧任务时删除同一鱼目录内上一版尾部模型的派生图，避免让
    # 调试结果误以为当前几何算法仍调用了尾部 Mask R-CNN。
    for obsolete_tail_artifact in (
        save_path / f"{filename}_mask_best.png",
        save_path / f"{filename}_pred.png",
        # Lab 色差曲线已停用；重跑时同时清理旧版遗留图。
        save_path / "03_tail_lab_color_profile.png",
    ):
        try:
            obsolete_tail_artifact.unlink()
        except FileNotFoundError:
            pass

    rotated_image, rotated_mask, _ = rotate_image_and_mask_to_horizontal(
        image,
        mask_image,
    )
    cv2.imwrite(str(save_path / "01_rotated_fish.jpg"), rotated_image)
    cv2.imwrite(str(save_path / "02_rotated_mask.png"), rotated_mask)
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

    # 全长与无尾体长必须沿同一条中轴线积分，避免分别重新拟合产生
    # “无尾体长大于全长”的数值矛盾。
    coeff_full = np.asarray(coefficients, dtype=float)
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

    def find_height(fit_min, fit_max, fit_min2, fit_max2):
        """用鱼身区域的旋转最小外接矩形短边计算体高。"""

        points_below_local = points_below[
            (points_below[:, 0] >= fit_min)
            & (points_below[:, 0] <= fit_max)
        ]
        if len(points_below_local) < 3:
            raise ValueError("not enough lower contour points for body height")

        a_, b_, c_ = fit_quadratic(points_below_local, 25)
        x_values = points_below_local[:, 0]
        y_curve = a_ * x_values**2 + b_ * x_values + c_
        points_below_local = points_below_local[
            points_below_local[:, 1] < y_curve
        ]

        points_above_local = points_above[
            (points_above[:, 0] >= fit_min2)
            & (points_above[:, 0] <= fit_max2)
        ]
        points_below_local = points_below_local[
            (points_below_local[:, 0] >= fit_min2)
            & (points_below_local[:, 0] <= fit_max2)
        ]
        if len(points_above_local) == 0 or len(points_below_local) == 0:
            raise ValueError("empty contour region for body-height rectangle")
        points_all = np.vstack((points_above_local, points_below_local))

        min_rect_r = min_rotate_rect(points_all)
        min_rect_r = min_rect_r.reshape(-1, 1, 2).astype(np.int32)
        cv2.polylines(
            debug_res_image,
            [min_rect_r],
            isClosed=True,
            color=(0, 255, 0),
            thickness=4,
        )

        shortest_distance = float("inf")
        shortest_points = None
        for index in range(4):
            point1 = min_rect_r[index][0]
            point2 = min_rect_r[(index + 1) % 4][0]
            distance = calculate_distance(point1, point2)
            if distance < shortest_distance:
                shortest_distance = distance
                shortest_points = (point1, point2)
        if shortest_points is None:
            raise ValueError("cannot determine body-height rectangle short edge")
        return {
            "point1": shortest_points[0],
            "point2": shortest_points[1],
            "width": float(shortest_distance),
            "selection_rule": "minimum_area_rotated_rectangle_short_edge",
        }

    if count_left >= count_right:
        tail_side = "right"
        body_height_result = find_height(
            int(left_point[0]),
            int(right_point[0] - length * 2 / 5),
            int(left_point[0] + length / 5),
            int(right_point[0] - length / 2),
        )
        (X3, Y3) = left_point
    else:
        tail_side = "left"
        body_height_result = find_height(
            int(left_point[0] + length * 2 / 5),
            int(right_point[0]),
            int(left_point[0] + length / 2),
            int(right_point[0] - length / 5),
        )
        (X3, Y3) = right_point

    (X6, Y6) = body_height_result["point1"]
    (X7, Y7) = body_height_result["point2"]

    geometry_tail_cut_result = _find_tail_cut_from_width_profile(
        fish_mask=mask,
        coefficients=coefficients,
        left_x=left_point[0],
        right_x=right_point[0],
        tail_side=tail_side,
    )
    color_tail_cut_result = _find_tail_color_transition(
        image=pic_image,
        fish_mask=mask,
        coefficients=coefficients,
        geometry_cut_x=geometry_tail_cut_result["x"],
        left_x=left_point[0],
        right_x=right_point[0],
        tail_side=tail_side,
    )
    intersection_x = int(color_tail_cut_result["x"])
    final_tail_section = _measure_mask_cross_section(
        mask,
        coefficients,
        intersection_x,
    )
    if final_tail_section is None:
        raise ValueError(
            f"cannot measure final tail cut cross-section: x={intersection_x}"
        )
    final_tail_width, tail_line_point1, tail_line_point2 = final_tail_section
    tail_cut_line = (tail_line_point1, tail_line_point2)
    geometry_tail_cut_line = (
        geometry_tail_cut_result["point1"],
        geometry_tail_cut_result["point2"],
    )

    # 03 图显示几何宽度和颜色突变的两阶段搜索过程：
    # 灰线是部分采样横截面，橙线是几何截断线，黄线是最终截断线。
    tail_search_visual = pic_image.copy()
    tail_records = geometry_tail_cut_result["records"]
    display_step = max(1, len(tail_records) // 24)
    for record in tail_records[::display_step]:
        cv2.line(
            tail_search_visual,
            tuple(int(round(value)) for value in record["point1"]),
            tuple(int(round(value)) for value in record["point2"]),
            (150, 150, 150),
            1,
            cv2.LINE_AA,
        )
    geometry_line_start = tuple(
        int(round(value)) for value in geometry_tail_cut_line[0]
    )
    geometry_line_end = tuple(
        int(round(value)) for value in geometry_tail_cut_line[1]
    )
    cv2.line(
        tail_search_visual,
        geometry_line_start,
        geometry_line_end,
        (0, 165, 255),
        2,
        cv2.LINE_AA,
    )
    tail_line_start = tuple(int(round(value)) for value in tail_cut_line[0])
    tail_line_end = tuple(int(round(value)) for value in tail_cut_line[1])
    cv2.line(
        tail_search_visual,
        tail_line_start,
        tail_line_end,
        (0, 255, 255),
        3,
        cv2.LINE_AA,
    )
    cv2.circle(
        tail_search_visual,
        (
            int(intersection_x),
            int(round(quadratic_function(intersection_x, coefficients))),
        ),
        5,
        (255, 0, 0),
        -1,
        cv2.LINE_AA,
    )
    if not cv2.imwrite(
        str(save_path / "03_tail_search_region.jpg"),
        tail_search_visual,
    ):
        raise OSError("cannot write tail width search visualization")

    left_bound = int(math.ceil(left_point[0]))
    right_bound = int(math.floor(right_point[0]))
    if left_bound >= right_bound:
        raise ValueError(
            f"invalid full-length bounds: left={left_point[0]}, right={right_point[0]}"
        )
    intersection_x = max(left_bound, min(int(intersection_x), right_bound))
    tail_endpoint_x = right_point[0] if tail_side == "right" else left_point[0]
    minimum_tail_span = max(2.0, float(length) * 0.01)
    if abs(float(intersection_x) - float(tail_endpoint_x)) < minimum_tail_span:
        raise ValueError(
            "tail cut point is too close to the full-length tail endpoint: "
            f"side={tail_side}, cut_x={intersection_x}, endpoint_x={tail_endpoint_x:.4f}"
        )
    (X4, Y4) = (intersection_x, quadratic_function(intersection_x, coefficients))

    # 尾部 Lab 色差曲线可视化已停用，不再生成
    # ``03_tail_lab_color_profile.png``。这里保留原调用作为
    # 注释，便于以后需要时恢复：
    # tail_lab_color_profile_path = save_path / "03_tail_lab_color_profile.png"
    # tail_lab_color_profile = _save_tail_lab_color_profile(
    #     tail_lab_color_profile_path,
    #     records=color_tail_cut_result["records"],
    #     geometry_cut_x=int(geometry_tail_cut_result["x"]),
    #     final_tail_cut_x=int(intersection_x),
    #     annotated_tail_cut_x=None,
    #     threshold=color_tail_cut_result.get("threshold"),
    # )

    cv2.line(
        debug_res_image,
        tail_line_start,
        tail_line_end,
        (0, 255, 255),
        3,
        cv2.LINE_AA,
    )
    cv2.circle(debug_res_image, (int(X4), int(Y4)), 7, (255, 0, 0), -1)
    cv2.circle(debug_res_image, (int(X3), int(Y3)), 10, (0, 0, 255), -1)
    coefficients2 = coeff_full
    arc_length = abs(calculate_quadratic_arc_length(coefficients2, X3, X4))
    if arc_length >= arc_length_full - 1e-6:
        raise ValueError(
            "invalid centerline lengths: body length must be smaller than total length; "
            f"body_px={arc_length:.6f}, total_px={arc_length_full:.6f}"
        )
    real_arc_length = arc_length * mm_per_pixel
    total_length_mm = arc_length_full * mm_per_pixel
    total_length_cm = total_length_mm / 10.0

    body_height = abs(math.sqrt((X6 - X7) ** 2 + (Y6 - Y7) ** 2))
    real_body_height = body_height * mm_per_pixel
    body_length_mm = real_arc_length
    body_height_mm = real_body_height
    body_length_cm = body_length_mm / 10.0
    body_height_cm = body_height_mm / 10.0
    # 中轴线已在几何最窄点之后用 Lab 颜色突变作了二次校正；
    # 未找到可靠颜色突变时会自动保留几何截断点。因此
    # `body_length_mm` 实际就是"无尾体长"。为了和数据库 / 文档约定对齐，下面同时再显式暴露
    # `body_length_no_tail_mm` / `body_length_no_tail_cm`，数值与 `body_length_*` 相同。
    body_length_no_tail_mm = float(body_length_mm)
    body_length_no_tail_cm = float(body_length_cm)

    process_visualization_path = save_path / "04_geometry_process.jpg"
    if not cv2.imwrite(str(process_visualization_path), debug_res_image):
        raise OSError(f"cannot write process visualization: {process_visualization_path}")

    measurement_visualization = _make_measurement_visualization(
        image=pic_image,
        fish_contour=fish_contour,
        full_coefficients=coeff_full,
        body_coefficients=coefficients2,
        full_start=left_point,
        full_end=right_point,
        body_start=(X3, Y3),
        tail_cut=(X4, Y4),
        tail_cut_line=tail_cut_line,
        body_height_line=((X6, Y6), (X7, Y7)),
    )
    visualization_path = save_path / f"{filename}_with_line.jpg"
    if not cv2.imwrite(str(visualization_path), measurement_visualization):
        raise OSError(f"cannot write visualization: {visualization_path}")

    measurement_json_path = _write_measurement_json(
        save_path,
        body_length_cm=body_length_cm,
        total_length_cm=total_length_cm,
        body_height_cm=body_height_cm,
    )

    predict_weight = _import_predict_weight()
    pred_weight = predict_weight(str(image_path), body_length_cm, body_height_cm)

    return {
        "body_length_mm": round(float(body_length_mm), 4),
        "body_height_mm": round(float(body_height_mm), 4),
        "body_length_cm": round(float(body_length_cm), 4),
        "total_length_mm": round(float(total_length_mm), 4),
        "total_length_cm": round(float(total_length_cm), 4),
        "body_length_no_tail_mm": round(float(body_length_no_tail_mm), 4),
        "body_length_no_tail_cm": round(float(body_length_no_tail_cm), 4),
        "body_height_cm": round(float(body_height_cm), 4),
        "body_height_method": body_height_result["selection_rule"],
        "body_height_width_px": round(float(body_height), 4),
        "pred_weight_g": round(float(pred_weight), 4),
        "debug_image_dir": save_path,
        "process_visualization_path": process_visualization_path,
        "visualization_path": visualization_path,
        "measurement_json_path": measurement_json_path,
        "arc_length_full": round(float(arc_length_full), 4),
        "tail_cut_method": color_tail_cut_result["selection_rule"],
        "tail_color_selection_strategy": color_tail_cut_result.get(
            "selection_strategy"
        ),
        "geometry_tail_cut_method": geometry_tail_cut_result["selection_rule"],
        "geometry_tail_cut_x": int(geometry_tail_cut_result["x"]),
        "tail_side": tail_side,
        "tail_cut_x": int(intersection_x),
        "tail_cut_width_px": round(float(final_tail_width), 4),
        "tail_color_delta_e": (
            None
            if color_tail_cut_result["delta_e"] is None
            else round(float(color_tail_cut_result["delta_e"]), 4)
        ),
        "tail_color_threshold": (
            None
            if color_tail_cut_result["threshold"] is None
            else round(float(color_tail_cut_result["threshold"]), 4)
        ),
        "tail_color_peak_x": color_tail_cut_result.get("peak_x"),
        "tail_color_peak_delta_e": (
            None
            if color_tail_cut_result.get("peak_delta_e") is None
            else round(
                float(color_tail_cut_result["peak_delta_e"]),
                4,
            )
        ),
        "tail_color_near_peak_threshold": (
            None
            if color_tail_cut_result.get("near_peak_threshold") is None
            else round(
                float(color_tail_cut_result["near_peak_threshold"]),
                4,
            )
        ),
    }


# ---------------------------------------------------------------------------
# CSV 行
# ---------------------------------------------------------------------------


def _run_belly_color_safe(*, crop_path: Path, mask_path: Path) -> dict:
    """计算单条完整鱼的腹部 HEX；失败不阻断主流程。"""

    try:
        from algorithms.core.belly_color import extract_belly_hex_color_from_images  # noqa: WPS433

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
        from algorithms.core.surface_damage import analyze_surface_damage  # noqa: WPS433
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
    total_length_cm: float | None = None,
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

    # 此处的内部 body_length_cm 仍是去尾体长；最终 CSV 的同名字段改写为含尾全长。
    condition_factor = (
        calculate_condition_factor(pred_weight_g, body_length_cm)
        if status == "complete"
        else None
    )

    # 长期字段约定：
    # - body_length_cm：完整鱼写入含尾中轴弧长；其它状态为空。
    # - body_length_no_tail_cm：完整鱼写入去尾体长；其它状态为空。
    # - status="complete"：其它数值字段写入数值；无伤口 surface_damage_ratio=0.0；
    # - status="incomplete" / "failed"：其它数值字段全部为空。
    # - HEX 是上述数值字段的例外：几何失败时仍保留已经成功提取的颜色。
    if status == "complete":
        ratio_str = "" if surface_damage_ratio is None else f"{float(surface_damage_ratio):.6f}"
        body_length_cm_str = "" if total_length_cm is None else round(float(total_length_cm), 4)
        body_length_no_tail_cm_str = "" if body_length_cm is None else round(float(body_length_cm), 4)
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
        # 由 pipeline 中紧随 fish_condition 的 arched-back 阶段回填。
        "arched_back_ratio": "",
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

    # ----------------- wound 模型批处理只加载一次 -----------------
    disable_wound = bool(getattr(args, "disable_wound", False))
    wound_device = str(getattr(args, "device", "cuda") or "cuda")
    wound_threshold = float(getattr(args, "wound_threshold", 0.5))
    wound_model = None
    if not disable_wound:
        try:
            from algorithms.core.surface_damage import load_wound_model  # noqa: WPS433
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
            total_length_cm=measurement.get("total_length_cm"),
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

    # 测量图属于可复核结果，默认保留；只有显式要求丢弃时才安全清理。
    keep = bool(getattr(args, "keep_measurement_debug_images", True))
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
                "如需删除请自行清理。"
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
                        help="测量过程可视化目录（默认 data/jobs/<video-id>/measurements/fish_condition_images/）")
    parser.add_argument("--csv-output", default=None,
                        help="CSV 输出路径（默认 data/jobs/<video-id>/measurements/long_height_weight_results.csv）")
    parser.add_argument("--mm-per-pixel", type=float, default=None,
                        help="像素到毫米的换算系数；缺省时尝试从 calibration/transform_matrix.json 读取")
    parser.add_argument(
        "--checkpoint",
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--keep-measurement-debug-images",
        dest="keep_measurement_debug_images",
        action="store_true",
        default=True,
        help="保留测量过程和最终标注图片（默认行为；保留旧参数兼容性）",
    )
    parser.add_argument(
        "--discard-measurement-debug-images",
        dest="keep_measurement_debug_images",
        action="store_false",
        help="CSV 写完后删除 fish_condition_images/（不建议，无法复核测量过程）",
    )
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
