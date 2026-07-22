from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
import sys

import cv2
import joblib
import numpy as np
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
ALGORITHMS_ROOT = SCRIPT_DIR.parents[1]
if str(ALGORITHMS_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS_ROOT))

from config.paths import BELLY_TREE_MODEL_PATH  # noqa: E402


BELLY_MODEL_PATH = BELLY_TREE_MODEL_PATH

DEFAULT_THRESHOLD = 1
DEFAULT_MIN_AREA = 20.0
DEFAULT_ANGLE_THRESHOLD = 1.0

MODEL_FEATURE_COLUMNS = [
    "L_up",
    "A_up",
    "B_up",
    "H_up",
    "S_up",
    "V_up",
    "L_down",
    "A_down",
    "B_down",
    "H_down",
    "S_down",
    "V_down",
    "delta_L",
    "delta_A",
    "delta_B",
    "delta_H",
    "delta_S",
    "delta_V",
]

_BELLY_MODEL_BUNDLE: dict | None = None


class MergeFishError(RuntimeError):
    """Raised when the merged fish processing pipeline cannot continue."""


@dataclass(frozen=True)
class RotateResult:
    image: np.ndarray
    level_angle: float
    head_position: str
    head_level_angle: float
    head_level_rotated: bool


@dataclass(frozen=True)
class BellyResult:
    image: np.ndarray
    belly_position: str
    back_position: str
    flipped: bool


# Completeness classification is implemented by vit_completeness.py.
# This module only contains rotation, head-orientation, and belly/back correction.


def get_fish_mask(image: np.ndarray, threshold: int = DEFAULT_THRESHOLD) -> np.ndarray:
    if image.ndim == 2:
        gray = image
    elif image.shape[2] == 4:
        alpha = image[:, :, 3]
        alpha_mask = alpha > threshold
        if 0 < np.count_nonzero(alpha_mask) < alpha_mask.size:
            return alpha_mask.astype(np.uint8) * 255
        gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
    else:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    _, mask = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
    return mask.astype(np.uint8)


def clean_fish_mask(mask: np.ndarray, kernel_size: int = 5) -> np.ndarray:
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    cleaned = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    if cv2.countNonZero(cleaned) == 0:
        return mask
    return cleaned

"""
    从鱼体的二值 mask 图里找到“最大的一块前景轮廓”，并确认它足够大，可以当作鱼体来继续处理
    流程：
        1. 使用 cv2.findContours 找到所有轮廓
        2. 如果没有找到轮廓，抛出异常
        3. 从所有轮廓中选面积最大的一个
        4. 计算最大轮廓的面积，如果小于 min_area，抛出异常
        5. 返回最大轮廓
"""
def find_largest_contour(mask: np.ndarray, min_area: float = DEFAULT_MIN_AREA) -> np.ndarray:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise MergeFishError("No contour found in image mask.")

    contour = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(contour)
    if area < min_area:
        raise MergeFishError(
            f"Largest contour area {area:.2f} is smaller than min_area {min_area:.2f}."
        )
    return contour


def normalize_angle(angle: float) -> float:
    while angle > 90.0:
        angle -= 180.0
    while angle <= -90.0:
        angle += 180.0
    return angle

"""
    估计整条鱼当前相对水平线倾斜了多少度
"""
def estimate_level_angle(
    image: np.ndarray,
    threshold: int = DEFAULT_THRESHOLD,
    min_area: float = DEFAULT_MIN_AREA,
) -> float:
    mask = get_fish_mask(image, threshold=threshold)
    contour = find_largest_contour(mask, min_area=min_area)
    rect = cv2.minAreaRect(contour)
    box = cv2.boxPoints(rect)

    longest_length = -1.0
    longest_angle = 0.0
    for index in range(4):
        point_a = box[index]
        point_b = box[(index + 1) % 4]
        dx = float(point_b[0] - point_a[0])
        dy = float(point_b[1] - point_a[1])
        length = math.hypot(dx, dy)
        angle = normalize_angle(math.degrees(math.atan2(dy, dx)))

        if length > longest_length:
            longest_length = length
            longest_angle = angle

    return longest_angle

"""
    旋转图片，并且自动扩大画布，避免旋转后鱼体被裁掉
"""
def rotate_bound(
    image: np.ndarray,
    angle: float,
    interpolation: int = cv2.INTER_LINEAR,
) -> np.ndarray:
    height, width = image.shape[:2]
    center = (width / 2.0, height / 2.0)

    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    cos = abs(matrix[0, 0])
    sin = abs(matrix[0, 1])

    new_width = int((height * sin) + (width * cos))
    new_height = int((height * cos) + (width * sin))

    matrix[0, 2] += (new_width / 2.0) - center[0]
    matrix[1, 2] += (new_height / 2.0) - center[1]

    if image.ndim == 2:
        border_value: int | tuple[int, ...] = 0
    else:
        border_value = tuple(0 for _ in range(image.shape[2]))

    return cv2.warpAffine(
        image,
        matrix,
        (new_width, new_height),
        flags=interpolation,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border_value,
    )


def level_image(
    image: np.ndarray,
    threshold: int = DEFAULT_THRESHOLD,
    min_area: float = DEFAULT_MIN_AREA,
) -> tuple[np.ndarray, float]:
    angle = estimate_level_angle(image, threshold=threshold, min_area=min_area)
    mask = get_fish_mask(image, threshold=threshold)
    rotated = rotate_bound(image, angle, interpolation=cv2.INTER_LINEAR)
    rotated_mask = rotate_bound(mask, angle, interpolation=cv2.INTER_NEAREST)
    _, rotated_mask = cv2.threshold(rotated_mask, 1, 255, cv2.THRESH_BINARY)

    if cv2.countNonZero(rotated_mask) == 0:
        raise MergeFishError("No fish pixels found after rotation.")

    rotated = cv2.bitwise_and(rotated, rotated, mask=rotated_mask)
    x, y, width, height = cv2.boundingRect(rotated_mask)
    tight_rotated = rotated[y : y + height, x : x + width]

    return tight_rotated, angle

"""
    计算水平校正后图片的左右像素数量，判断鱼头位置
"""
def compute_left_right_pixels(mask: np.ndarray) -> tuple[int, int, float]:
    _height, width = mask.shape
    mid = width // 2
    left_count = int(np.sum(mask[:, :mid] > 0))
    right_count = int(np.sum(mask[:, mid:] > 0))
    total = left_count + right_count
    if total == 0:
        return 0, 0, 0.0
    score = (left_count - right_count) / total
    return left_count, right_count, float(score)


def describe_head_reason(left_count: int, right_count: int, head_on_left: bool) -> str:
    if head_on_left:
        return f"左侧{left_count}像素 > 右侧{right_count}像素"
    return f"右侧{right_count}像素 > 左侧{left_count}像素"

"""
    根据左右像素数量和得分，判断鱼头位置，并给出理由和置信度等级
"""
def determine_head(
    score: float,
    left_count: int,
    right_count: int,
    high_diff: float = 0.10,
    low_diff: float = 0.03,
) -> tuple[str, str, str, str, str]:
    abs_score = abs(score)

    if abs_score < low_diff:
        return "不确定", "不确定", "不确定", "左右像素数量接近，无法判断", "低"

    head_on_left = score > 0
    head_position = "左侧" if head_on_left else "右侧"
    head_direction = "朝左" if head_on_left else "朝右"
    reason = describe_head_reason(left_count, right_count, head_on_left)

    if abs_score >= high_diff:
        confidence = "高"
    elif abs_score >= low_diff * 2:
        confidence = "中"
    else:
        confidence = "低"

    return "是", head_position, head_direction, reason, confidence

"""
    判断鱼头在图片左边还是右边
"""
def compute_head_orientation(
    image: np.ndarray,
    threshold: int = DEFAULT_THRESHOLD,
) -> tuple[int, int, float, str, str, str, str, str]:
    mask = get_fish_mask(image, threshold=threshold)
    clean_mask = clean_fish_mask(mask)

    fish_pixels = cv2.countNonZero(clean_mask)
    if fish_pixels < 100:
        return 0, 0, 0.0, "不确定", "不确定", "不确定", "前景像素过少", "低"

    left_count, right_count, score = compute_left_right_pixels(clean_mask)
    can_judge, head_position, head_direction, reason, confidence = determine_head(
        score,
        left_count,
        right_count,
    )

    return (
        left_count,
        right_count,
        score,
        can_judge,
        head_position,
        head_direction,
        reason,
        confidence,
    )

"""
    只看鱼头所在的那半边，估计鱼头部分的倾斜角度
    用于第二次更精细的水平校正
"""
def estimate_head_half_angle(
    image: np.ndarray,
    head_position: str,
    threshold: int = DEFAULT_THRESHOLD,
    min_area: float = DEFAULT_MIN_AREA,
) -> float:
    mask = get_fish_mask(image, threshold=threshold)

    _height, width = mask.shape
    mid = width // 2
    if head_position == "左侧":
        mask[:, mid:] = 0
    elif head_position == "右侧":
        mask[:, :mid] = 0
    else:
        raise MergeFishError(f"Cannot choose head half for position: {head_position}")

    contour = find_largest_contour(mask, min_area=min_area)
    rect = cv2.minAreaRect(contour)
    box = cv2.boxPoints(rect)

    longest_length = -1.0
    longest_angle = 0.0
    for index in range(4):
        point_a = box[index]
        point_b = box[(index + 1) % 4]
        dx = float(point_b[0] - point_a[0])
        dy = float(point_b[1] - point_a[1])
        length = math.hypot(dx, dy)
        angle = normalize_angle(math.degrees(math.atan2(dy, dx)))

        if length > longest_length:
            longest_length = length
            longest_angle = angle

    return longest_angle


def level_image_by_head(
    image: np.ndarray,
    head_position: str,
    threshold: int = DEFAULT_THRESHOLD,
    min_area: float = DEFAULT_MIN_AREA,
) -> tuple[np.ndarray, float]:
    angle = estimate_head_half_angle(
        image,
        head_position=head_position,
        threshold=threshold,
        min_area=min_area,
    )
    rotated = rotate_bound(image, angle, interpolation=cv2.INTER_LINEAR)
    return rotated, angle


"""
    旋转校正
    流程：
        1.先按整条鱼做水平校正
        2.再判断鱼头位置
        3.如果能判断鱼头，就按鱼头半边再估计一次角度
        4.只有角度超过阈值才真正二次旋转
"""
def process_rotate_image(
    image: np.ndarray,
    threshold: int = DEFAULT_THRESHOLD,
    min_area: float = DEFAULT_MIN_AREA,
    angle_threshold: float = DEFAULT_ANGLE_THRESHOLD,
) -> RotateResult:
    leveled_image, level_angle = level_image(
        image,
        threshold=threshold,
        min_area=min_area,
    )
    (
        _left,
        _right,
        _score,
        _can_judge,
        head_position,
        _head_direction,
        _reason,
        _confidence,
    ) = compute_head_orientation(leveled_image, threshold=threshold)

    final_image = leveled_image
    head_level_angle = 0.0
    head_level_rotated = False
    if head_position == "不确定":
        return RotateResult(
            image=final_image,
            level_angle=level_angle,
            head_position=head_position,
            head_level_angle=head_level_angle,
            head_level_rotated=head_level_rotated,
        )

    head_leveled_image, head_level_angle = level_image_by_head(
        leveled_image,
        head_position=head_position,
        threshold=threshold,
        min_area=min_area,
    )
    if abs(head_level_angle) > angle_threshold:
        final_image = head_leveled_image
        head_level_rotated = True

    return RotateResult(
        image=final_image,
        level_angle=level_angle,
        head_position=head_position,
        head_level_angle=head_level_angle,
        head_level_rotated=head_level_rotated,
    )


# =============================================================================
# Part 3: 腹背方向判断和翻转校正
# 输入：水平校正后的图片   输出：腹背方向判断结果和翻转校正后的图片
# =============================================================================

def load_belly_model() -> dict:
    global _BELLY_MODEL_BUNDLE
    if _BELLY_MODEL_BUNDLE is not None:
        return _BELLY_MODEL_BUNDLE
    if not BELLY_MODEL_PATH.is_file():
        raise MergeFishError(f"Belly decision tree model not found: {BELLY_MODEL_PATH}")

    bundle = joblib.load(BELLY_MODEL_PATH)
    if not isinstance(bundle, dict) or "model" not in bundle:
        raise MergeFishError(f"Invalid belly decision tree model: {BELLY_MODEL_PATH}")

    feature_columns = bundle.get("feature_columns", MODEL_FEATURE_COLUMNS)
    missing_columns = [
        column for column in feature_columns if column not in MODEL_FEATURE_COLUMNS
    ]
    if missing_columns:
        raise MergeFishError(f"Model requires unknown feature columns: {missing_columns}")

    _BELLY_MODEL_BUNDLE = bundle
    return bundle


def _compute_principal_axis(
    mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    coords = np.column_stack(np.where(mask > 0))
    if len(coords) < 10:
        return None
    mean = np.mean(coords, axis=0)
    centered = coords - mean
    cov = np.cov(centered.T)
    if np.any(np.isnan(cov)) or np.any(np.isinf(cov)):
        return None
    eigenvalues, eigenvectors = np.linalg.eig(cov)
    idx = np.argsort(eigenvalues)[::-1]
    return eigenvectors[:, idx[0]], eigenvectors[:, idx[1]], mean

"""
    找到一条近似的“中线”，把鱼体分成上下两部分
"""
def split_by_midline(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    coords = np.column_stack(np.where(mask > 0))
    if len(coords) < 10:
        return np.empty((0, 2), dtype=np.intp), np.empty((0, 2), dtype=np.intp)

    height, width = mask.shape

    pca_result = _compute_principal_axis(mask)
    if pca_result is not None:
        principal_axis, _, _ = pca_result
        is_horizontal = abs(principal_axis[1]) >= abs(principal_axis[0])
    else:
        rows, cols = np.where(mask > 0)
        is_horizontal = (cols.max() - cols.min()) >= (rows.max() - rows.min())

    midline_pts: list[tuple[int, float]] = []
    if is_horizontal:
        for col in range(width):
            rows_in_col = np.where(mask[:, col] > 0)[0]
            if len(rows_in_col) >= 5:
                midline_pts.append((col, float(np.median(rows_in_col))))
    else:
        for row in range(height):
            cols_in_row = np.where(mask[row, :] > 0)[0]
            if len(cols_in_row) >= 5:
                midline_pts.append((row, float(np.median(cols_in_row))))

    if len(midline_pts) < 10:
        median_row = np.median(coords[:, 0])
        is_upper = coords[:, 0] <= median_row
        coords_upper = coords[is_upper]
        coords_lower = coords[~is_upper]
    else:
        midline_arr = np.array(midline_pts)
        if is_horizontal:
            midline_rows = np.interp(
                coords[:, 1], midline_arr[:, 0], midline_arr[:, 1]
            )
            is_upper = coords[:, 0] < midline_rows
        else:
            midline_cols = np.interp(
                coords[:, 0], midline_arr[:, 0], midline_arr[:, 1]
            )
            is_upper = coords[:, 1] < midline_cols

        coords_upper = coords[is_upper]
        coords_lower = coords[~is_upper]

    if len(coords_upper) < 10 or len(coords_lower) < 10:
        return np.empty((0, 2), dtype=np.intp), np.empty((0, 2), dtype=np.intp)

    if np.mean(coords_upper[:, 0]) > np.mean(coords_lower[:, 0]):
        coords_upper, coords_lower = coords_lower, coords_upper

    return coords_upper, coords_lower


def compute_color_features(
    l_channel: np.ndarray,
    a_channel: np.ndarray,
    b_channel: np.ndarray,
    h_channel: np.ndarray,
    s_channel: np.ndarray,
    v_channel: np.ndarray,
    upper_coords: np.ndarray,
    lower_coords: np.ndarray,
) -> dict[str, float]:
    upper_rows, upper_cols = upper_coords[:, 0], upper_coords[:, 1]
    lower_rows, lower_cols = lower_coords[:, 0], lower_coords[:, 1]

    upper_l = float(np.median(l_channel[upper_rows, upper_cols]))
    lower_l = float(np.median(l_channel[lower_rows, lower_cols]))
    upper_a = float(np.median(a_channel[upper_rows, upper_cols]))
    lower_a = float(np.median(a_channel[lower_rows, lower_cols]))
    upper_b = float(np.median(b_channel[upper_rows, upper_cols]))
    lower_b = float(np.median(b_channel[lower_rows, lower_cols]))
    upper_h = float(np.median(h_channel[upper_rows, upper_cols]))
    lower_h = float(np.median(h_channel[lower_rows, lower_cols]))
    upper_s = float(np.median(s_channel[upper_rows, upper_cols]))
    lower_s = float(np.median(s_channel[lower_rows, lower_cols]))
    upper_v = float(np.median(v_channel[upper_rows, upper_cols]))
    lower_v = float(np.median(v_channel[lower_rows, lower_cols]))

    return {
        "upper_l": upper_l,
        "lower_l": lower_l,
        "upper_a": upper_a,
        "lower_a": lower_a,
        "upper_b": upper_b,
        "lower_b": lower_b,
        "upper_h": upper_h,
        "lower_h": lower_h,
        "upper_s": upper_s,
        "lower_s": lower_s,
        "upper_v": upper_v,
        "lower_v": lower_v,
    }

"""
    根据决策树模型判断鱼的腹背位置
"""
def determine_belly_position(scores: dict[str, float]) -> str:
    bundle = load_belly_model()
    model = bundle["model"]
    feature_columns = bundle.get("feature_columns", MODEL_FEATURE_COLUMNS)

    model_features = {
        "L_up": scores["upper_l"],
        "A_up": scores["upper_a"],
        "B_up": scores["upper_b"],
        "H_up": scores["upper_h"],
        "S_up": scores["upper_s"],
        "V_up": scores["upper_v"],
        "L_down": scores["lower_l"],
        "A_down": scores["lower_a"],
        "B_down": scores["lower_b"],
        "H_down": scores["lower_h"],
        "S_down": scores["lower_s"],
        "V_down": scores["lower_v"],
        "delta_L": scores["lower_l"] - scores["upper_l"],
        "delta_A": scores["lower_a"] - scores["upper_a"],
        "delta_B": scores["lower_b"] - scores["upper_b"],
        "delta_H": scores["lower_h"] - scores["upper_h"],
        "delta_S": scores["lower_s"] - scores["upper_s"],
        "delta_V": scores["lower_v"] - scores["upper_v"],
    }
    feature_row = pd.DataFrame(
        [[model_features[column] for column in feature_columns]],
        columns=feature_columns,
    )

    predicted_label = int(model.predict(feature_row)[0])
    if predicted_label == 0:
        return "top"
    if predicted_label == 1:
        return "bottom"
    raise MergeFishError(f"Unexpected belly model label: {predicted_label}")

"""
    判断腹背方向
"""
def compute_belly_orientation(image: np.ndarray) -> tuple[str, dict[str, float]]:
    mask = get_fish_mask(image)
    clean_mask = clean_fish_mask(mask)

    if cv2.countNonZero(clean_mask) == 0:
        return "uncertain", {}

    upper_coords, lower_coords = split_by_midline(clean_mask)
    if len(upper_coords) < 10 or len(lower_coords) < 10:
        return "uncertain", {}

    if image.ndim == 2:
        bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.shape[2] == 4:
        bgr = image[:, :, :3]
    else:
        bgr = image

    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    scores = compute_color_features(
        lab[:, :, 0].astype(np.float32),
        lab[:, :, 1].astype(np.float32),
        lab[:, :, 2].astype(np.float32),
        hsv[:, :, 0].astype(np.float32),
        hsv[:, :, 1].astype(np.float32),
        hsv[:, :, 2].astype(np.float32),
        upper_coords,
        lower_coords,
    )
    return determine_belly_position(scores), scores


def apply_belly_correction(image: np.ndarray) -> BellyResult:
    belly_position, _scores = compute_belly_orientation(image)

    if belly_position == "top":
        back_position = "bottom"
        result_image = cv2.flip(image, 0)
        flipped = True
    elif belly_position == "bottom":
        back_position = "top"
        result_image = image
        flipped = False
    else:
        back_position = "uncertain"
        result_image = image
        flipped = False

    return BellyResult(
        image=result_image,
        belly_position=belly_position,
        back_position=back_position,
        flipped=flipped,
    )
