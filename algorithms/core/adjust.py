"""传送带矫正（标定）核心算法。

支持两种运行模式：

``calibrate``
    从单张拍摄图片生成一次标定会话，结果写入
    ``data/calibration/sessions/<calibration_id>/`` 并刷新
    ``data/calibration/current_calibration.json``。
    ``<calibration_id>`` 形如 ``calib_YYYYMMDD_HHMMSS_<short_id>``。
    每次标定都会保留为历史会话，**绝不删除用户传入的原始标定图**。

``batch``
    按帧序号读取 ``--input-dir`` 下的图片（兼容 6 位 / 12 位命名），
    使用 ``current_calibration.json`` 或 ``--calibration-json`` 提供的
    透视变换矩阵与最终画布尺寸矫正，写入 ``--output-dir``，
    输出全部使用 12 位命名。
    默认情况下，矫正成功后通过 ``config.cleanup`` 安全删除 raw/，
    传入 ``--keep-raw-frames`` 时保留。

所有路径来自 ``config/paths``。所有写入到 JSON 的路径都自动转换成相对
项目根目录的 POSIX 路径，避免绝对路径泄漏。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import secrets
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型检查时引用，避免运行时强依赖
    import cv2  # noqa: F401
    import numpy as np  # noqa: F401

THIS_DIR = Path(__file__).resolve().parent
ALGORITHMS_ROOT = THIS_DIR.parent
if str(ALGORITHMS_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS_ROOT))


def _np():
    """懒加载 numpy，便于 ``--help`` 在缺少 numpy 的环境中也能工作。"""

    import numpy as np  # type: ignore

    return np


def _cv2():
    """懒加载 cv2，便于 ``--help`` 在缺少 opencv 的环境中也能工作。"""

    import cv2  # type: ignore

    return cv2


from config.cleanup import remove_dir_contents  # noqa: E402
from config.naming import (  # noqa: E402
    format_frame_index,
    iter_matching_files,
    parse_frame_index,
)
from config.paths import (  # noqa: E402
    BERT_BASE_UNCASED_DIR,
    CALIBRATION_CURRENT_JSON,
    CALIBRATION_DIR,
    CALIBRATION_SESSIONS_DIR,
    GROUNDED_SAM_DIR,
    GROUNDING_DINO_CHECKPOINT,
    GROUNDING_DINO_CONFIG_PATH,
    GROUNDING_DINO_DIR,
    PROJECT_ROOT,
    SAM_HQ_VIT_H_CHECKPOINT,
    SAM_VIT_H_CHECKPOINT,
    SEGMENT_ANYTHING_DIR,
    calibration_session_dir,
    ensure_dir,
    resolve_input_path,
    to_project_relative,
    video_frames_raw_dir,
)


# ---------------------------------------------------------------------------
# GroundingDINO / SAM 按需导入
# ---------------------------------------------------------------------------


def _bootstrap_dependencies() -> None:
    """把外部包路径加入 ``sys.path``。"""

    for path in [
        str(GROUNDING_DINO_DIR),
        str(SEGMENT_ANYTHING_DIR),
    ]:
        if path not in sys.path:
            sys.path.insert(0, path)


def _ensure_dependencies() -> tuple:
    _bootstrap_dependencies()
    try:
        import groundingdino.datasets.transforms as T  # type: ignore  # noqa: E402
        from groundingdino.models import build_model  # type: ignore  # noqa: E402
        from groundingdino.util.slconfig import SLConfig  # type: ignore  # noqa: E402
        from groundingdino.util.utils import clean_state_dict  # type: ignore  # noqa: E402
        from segment_anything import (  # type: ignore  # noqa: E402
            SamPredictor,
            sam_hq_model_registry,
            sam_model_registry,
        )
    except ImportError as exc:  # pragma: no cover - 环境相关
        raise ImportError(
            "adjust.py 需要 GroundingDINO 与 segment_anything，"
            "请先在当前 conda 环境中安装这些依赖。"
        ) from exc
    return (
        T, build_model, SLConfig, clean_state_dict,
        SamPredictor, sam_hq_model_registry, sam_model_registry,
    )


BOX_THRESHOLD = 0.3
TEXT_PROMPT = "conveyor belt."
SAM_VERSION = "vit_h"
USE_SAM_HQ = True
DEVICE = "cuda"

# ===========================================================================
# MODIFIED VERSION
# ===========================================================================
# 本版本不再：
#   1) 把输出紧裁到传送带本体；
#   2) 也不再为了保留整幅原图而建立超大的完整透视画布。
#
# 当前策略：
#   传送带标准矩形 + 左右安全边界 + 少量上下安全边界。
#
# 该版本标记可用于确认服务器实际加载的是修改后的 adjust.py。
ADJUST_CANVAS_VERSION = "belt_margin_v3_20260811"


# ---------------------------------------------------------------------------
# calibrate 模式：生成 calibration_id / session
# ---------------------------------------------------------------------------


def generate_calibration_id(timestamp: _dt.datetime | None = None) -> str:
    """生成 ``calib_YYYYMMDD_HHMMSS_<short_id>`` 形式的会话 ID。"""

    now = timestamp or _dt.datetime.now()
    short_id = secrets.token_hex(3)  # 6 hex chars
    return f"calib_{now.strftime('%Y%m%d_%H%M%S')}_{short_id}"


def make_calibration_id() -> str:
    return generate_calibration_id()


def load_image(image_path):
    T, _, _, _, _, _, _ = _ensure_dependencies()
    from PIL import Image  # local import to keep base imports light

    image_pil = Image.open(image_path).convert("RGB")
    transform = T.Compose([
        T.RandomResize([800], max_size=1333),
        T.ToTensor(),
        T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])
    image, _ = transform(image_pil, None)
    return image_pil, image


def load_model(model_config_path, model_checkpoint_path, bert_base_uncased_path, device):
    _, build_model, SLConfig, clean_state_dict, _, _, _ = _ensure_dependencies()
    args = SLConfig.fromfile(str(model_config_path))
    args.device = device
    args.bert_base_uncased_path = str(bert_base_uncased_path)
    model = build_model(args)
    checkpoint = torch_load(model_checkpoint_path)
    model.load_state_dict(clean_state_dict(checkpoint["model"]), strict=False)
    model.eval()
    return model


def torch_load(path):
    import torch

    try:
        return torch.load(str(path), map_location="cpu", weights_only=False)
    except TypeError:
        return torch.load(str(path), map_location="cpu")


def get_boxes_only(model, image, caption, box_threshold, device):
    import torch

    caption = caption.lower().strip()
    if not caption.endswith("."):
        caption = caption + "."
    model = model.to(device)
    image = image.to(device)
    with torch.no_grad():
        outputs = model(image[None], captions=[caption])
    logits = outputs["pred_logits"].cpu().sigmoid()[0]
    boxes = outputs["pred_boxes"].cpu()[0]
    filt_mask = logits.max(dim=1)[0] > box_threshold
    return boxes[filt_mask]


def order_points(pts):
    rect = _np().zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[_np().argmin(s)]
    rect[2] = pts[_np().argmax(s)]
    diff = _np().diff(pts, axis=1)
    rect[1] = pts[_np().argmin(diff)]
    rect[3] = pts[_np().argmax(diff)]
    return rect


def _move_points_inside_image(points, image_shape):
    """将四个源角点保持形状不变地收缩到原图范围内。

    当 ``minAreaRect`` 产生位于原图外部的角点时，透视变换会采样到
    不存在的像素，从而产生黑边。本函数以四边形中心为基准，统一缩放
    四个角点，直到所有角点都落在原图范围内。

    如果四个点原本就在图像内，则不会改变它们。
    """

    np = _np()

    points = np.asarray(points, dtype=np.float32)
    image_height, image_width = image_shape[:2]

    if points.shape != (4, 2):
        raise ValueError(
            f"透视变换需要四个二维角点，当前形状为 {points.shape}"
        )

    if not np.all(np.isfinite(points)):
        raise ValueError("透视变换角点中包含无效数值。")

    x_min = 0.0
    y_min = 0.0
    x_max = float(image_width - 1)
    y_max = float(image_height - 1)

    already_inside = (
        np.all(points[:, 0] >= x_min)
        and np.all(points[:, 0] <= x_max)
        and np.all(points[:, 1] >= y_min)
        and np.all(points[:, 1] <= y_max)
    )
    if already_inside:
        return points

    center = np.mean(points, axis=0)
    directions = points - center

    scale = 1.0
    for dx, dy in directions:
        dx = float(dx)
        dy = float(dy)

        if dx > 0:
            scale = min(scale, (x_max - float(center[0])) / dx)
        elif dx < 0:
            scale = min(scale, (x_min - float(center[0])) / dx)

        if dy > 0:
            scale = min(scale, (y_max - float(center[1])) / dy)
        elif dy < 0:
            scale = min(scale, (y_min - float(center[1])) / dy)

    # 留出极小安全余量，避免插值时访问到图像边界外。
    scale = max(0.0, min(1.0, scale)) * 0.999

    adjusted = center + directions * scale
    adjusted[:, 0] = np.clip(adjusted[:, 0], x_min, x_max)
    adjusted[:, 1] = np.clip(adjusted[:, 1], y_min, y_max)
    return adjusted.astype(np.float32)


DEFAULT_SIDE_MARGIN_RATIO = 0.25
DEFAULT_VERTICAL_MARGIN_RATIO = 0.05


def _validate_margin_ratio(value: float, name: str) -> float:
    """校验安全边界比例。"""

    value = float(value)
    if value < 0:
        raise ValueError(f"{name} 不能小于 0，当前值为 {value}")
    if value > 2.0:
        raise ValueError(
            f"{name}={value} 过大；该比例是相对于矫正后传送带尺寸，"
            "通常建议使用 0~1 范围。"
        )
    return value


def _validate_margin_px(value: int | None, name: str) -> int | None:
    """校验显式像素安全边界。"""

    if value is None:
        return None
    value = int(value)
    if value < 0:
        raise ValueError(f"{name} 不能小于 0，当前值为 {value}")
    return value


def _resolve_canvas_margins(
    belt_width: int,
    belt_height: int,
    *,
    side_margin_ratio: float = DEFAULT_SIDE_MARGIN_RATIO,
    vertical_margin_ratio: float = DEFAULT_VERTICAL_MARGIN_RATIO,
    margin_left_px: int | None = None,
    margin_right_px: int | None = None,
    margin_top_px: int | None = None,
    margin_bottom_px: int | None = None,
) -> tuple[int, int, int, int]:
    """计算传送带四周的安全边界。

    默认左右各保留 ``belt_width * 0.25``，上下各保留
    ``belt_height * 0.05``。若某一侧显式传入像素值，则该侧使用像素值。
    """

    side_margin_ratio = _validate_margin_ratio(
        side_margin_ratio, "side_margin_ratio"
    )
    vertical_margin_ratio = _validate_margin_ratio(
        vertical_margin_ratio, "vertical_margin_ratio"
    )

    margin_left_px = _validate_margin_px(margin_left_px, "margin_left_px")
    margin_right_px = _validate_margin_px(margin_right_px, "margin_right_px")
    margin_top_px = _validate_margin_px(margin_top_px, "margin_top_px")
    margin_bottom_px = _validate_margin_px(margin_bottom_px, "margin_bottom_px")

    auto_side = int(round(float(belt_width) * side_margin_ratio))
    auto_vertical = int(round(float(belt_height) * vertical_margin_ratio))

    left = auto_side if margin_left_px is None else margin_left_px
    right = auto_side if margin_right_px is None else margin_right_px
    top = auto_vertical if margin_top_px is None else margin_top_px
    bottom = auto_vertical if margin_bottom_px is None else margin_bottom_px

    return int(left), int(right), int(top), int(bottom)


def rectify_conveyor_belt(
    image_bgr,
    mask_np,
    *,
    side_margin_ratio: float = DEFAULT_SIDE_MARGIN_RATIO,
    vertical_margin_ratio: float = DEFAULT_VERTICAL_MARGIN_RATIO,
    margin_left_px: int | None = None,
    margin_right_px: int | None = None,
    margin_top_px: int | None = None,
    margin_bottom_px: int | None = None,
):
    """透视矫正传送带，并在传送带四周保留安全边界。

    旧实现把传送带四边形直接映射到一个紧贴传送带的矩形，
    ``warpPerspective`` 的输出尺寸也等于该矩形，因此传送带左右边缘
    的完整鱼可能被裁成不完整鱼。

    另一种“保留整幅原图透视结果”的做法虽然不会裁鱼，但会生成过大的
    画布和大量黑色三角区域，使鱼在后续模型输入中被过度缩小。

    本实现采用折中且更适合鱼类分割的策略：

        传送带标准矩形 + 左右较大的安全边界 + 少量上下安全边界

    ``vertical_distance`` / ``pixel_length`` 仍表示传送带本体的矫正后
    高度和宽度，用于 ``mm_per_pixel``；最终输出尺寸由安全边界决定。
    """

    np = _np()
    cv2 = _cv2()

    contours, _ = cv2.findContours(
        mask_np.astype(np.uint8),
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    if not contours:
        return None, 0, 0, None, None, None

    contour = max(contours, key=cv2.contourArea)
    epsilon = 0.02 * cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, epsilon, True)

    if len(approx) == 4:
        pts = approx.reshape(4, 2)
    else:
        rect = cv2.minAreaRect(contour)
        pts = cv2.boxPoints(rect)

    # 防止 minAreaRect 角点落到原图外。
    pts = _move_points_inside_image(pts, image_bgr.shape)

    rect_pts = order_points(np.asarray(pts, dtype=np.float32))
    src_pts = np.asarray(rect_pts, dtype=np.float32)
    tl, tr, br, bl = rect_pts

    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)

    # 使用 ceil，避免 int() 向下取整导致边界再损失 1~2 像素。
    belt_width = max(2, int(np.ceil(max(width_a, width_b))))
    belt_height = max(2, int(np.ceil(max(height_a, height_b))))

    left, right, top, bottom = _resolve_canvas_margins(
        belt_width,
        belt_height,
        side_margin_ratio=side_margin_ratio,
        vertical_margin_ratio=vertical_margin_ratio,
        margin_left_px=margin_left_px,
        margin_right_px=margin_right_px,
        margin_top_px=margin_top_px,
        margin_bottom_px=margin_bottom_px,
    )

    # 传送带本体先映射到从 (0, 0) 开始的标准矩形。
    belt_dst = np.asarray(
        [
            [0.0, 0.0],
            [float(belt_width - 1), 0.0],
            [float(belt_width - 1), float(belt_height - 1)],
            [0.0, float(belt_height - 1)],
        ],
        dtype=np.float32,
    )

    base_transform = cv2.getPerspectiveTransform(src_pts, belt_dst)

    # 再通过固定平移把传送带放入带安全边界的最终画布中。
    translation = np.asarray(
        [
            [1.0, 0.0, float(left)],
            [0.0, 1.0, float(top)],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    transform_matrix = (
        translation
        @ np.asarray(base_transform, dtype=np.float64)
    )

    output_width = int(belt_width + left + right)
    output_height = int(belt_height + top + bottom)

    if output_width <= 0 or output_height <= 0:
        raise RuntimeError(
            f"计算得到无效输出画布：{output_width}x{output_height}"
        )

    # 防止错误 margin 参数造成异常大的内存分配。
    image_height, image_width = image_bgr.shape[:2]
    max_width = max(int(image_width) * 4, 4096)
    max_height = max(int(image_height) * 4, 4096)
    if output_width > max_width or output_height > max_height:
        raise RuntimeError(
            "安全边界导致输出画布异常大："
            f"{output_width}x{output_height}；原图为 "
            f"{image_width}x{image_height}。请减小 margin。"
        )

    # 目标四角必须记录在“最终画布坐标系”中。
    target_points = cv2.perspectiveTransform(
        src_pts.reshape(-1, 1, 2),
        transform_matrix,
    ).reshape(-1, 2).astype(np.float32)

    warped_img = cv2.warpPerspective(
        image_bgr,
        transform_matrix,
        (output_width, output_height),
        flags=cv2.INTER_LINEAR,
        # 输出画布中若有源图像没有覆盖的位置，使用黑色填充。
        # 不使用 BORDER_REPLICATE，避免复制边缘鱼形成假目标。
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )

    return (
        warped_img,
        belt_height,
        belt_width,
        transform_matrix,
        src_pts,
        target_points,
    )


# ---------------------------------------------------------------------------
# 数据类：标定结果
# ---------------------------------------------------------------------------


@dataclass
class CalibrationResult:
    calibration_id: str
    camera_id: str
    created_at: str
    image_width: int
    image_height: int
    transform_matrix: list
    inverse_transform_matrix: list
    source_points: list
    target_points: list
    vertical_distance: int | None
    pixel_length: int | None
    source_image: Path
    belt_mask: Path
    adjusted_preview: Path
    note: str = ""

    # 最终矫正图画布尺寸。旧字段 pixel_length / vertical_distance
    # 仍只表示传送带本体尺寸，不能再作为 batch 输出尺寸。
    output_width: int | None = None
    output_height: int | None = None

    # 画布安全边界元数据，便于历史追溯。
    canvas_mode: str = "belt_margin"
    canvas_margin_left_px: int = 0
    canvas_margin_right_px: int = 0
    canvas_margin_top_px: int = 0
    canvas_margin_bottom_px: int = 0
    canvas_side_margin_ratio: float = DEFAULT_SIDE_MARGIN_RATIO
    canvas_vertical_margin_ratio: float = DEFAULT_VERTICAL_MARGIN_RATIO
    canvas_version: str = ADJUST_CANVAS_VERSION

    def to_dict(self) -> dict:
        return {
            "calibration_id": self.calibration_id,
            "camera_id": self.camera_id,
            "created_at": self.created_at,
            "image_width": int(self.image_width),
            "image_height": int(self.image_height),
            "output_width": (
                None if self.output_width is None else int(self.output_width)
            ),
            "output_height": (
                None if self.output_height is None else int(self.output_height)
            ),
            "transform_matrix": _np().asarray(
                self.transform_matrix, dtype=float
            ).tolist(),
            "inverse_transform_matrix": _np().asarray(
                self.inverse_transform_matrix, dtype=float
            ).tolist(),
            "source_points": _np().asarray(
                self.source_points, dtype=float
            ).tolist(),
            "target_points": _np().asarray(
                self.target_points, dtype=float
            ).tolist(),
            "vertical_distance": (
                None
                if self.vertical_distance is None
                else int(self.vertical_distance)
            ),
            "pixel_length": (
                None
                if self.pixel_length is None
                else int(self.pixel_length)
            ),
            "canvas_mode": self.canvas_mode,
            "canvas_margin_left_px": int(self.canvas_margin_left_px),
            "canvas_margin_right_px": int(self.canvas_margin_right_px),
            "canvas_margin_top_px": int(self.canvas_margin_top_px),
            "canvas_margin_bottom_px": int(self.canvas_margin_bottom_px),
            "canvas_side_margin_ratio": float(self.canvas_side_margin_ratio),
            "canvas_vertical_margin_ratio": float(self.canvas_vertical_margin_ratio),
            "canvas_version": str(self.canvas_version),
            "source_image": to_project_relative(self.source_image),
            "belt_mask": to_project_relative(self.belt_mask),
            "adjusted_preview": to_project_relative(self.adjusted_preview),
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# calibrate 模式主流程
# ---------------------------------------------------------------------------


def run_calibration(
    source_image: Path | str,
    camera_id: str = "default",
    calibration_id: str | None = None,
    real_length_mm: float = 1376.0,
    note: str = "",
    side_margin_ratio: float = DEFAULT_SIDE_MARGIN_RATIO,
    vertical_margin_ratio: float = DEFAULT_VERTICAL_MARGIN_RATIO,
    margin_left_px: int | None = None,
    margin_right_px: int | None = None,
    margin_top_px: int | None = None,
    margin_bottom_px: int | None = None,
) -> CalibrationResult:
    """读取 ``source_image``，执行标定，并把会话写入 sessions 目录。

    - ``source_image`` 可以是绝对路径或相对于项目根的相对路径。
    - 原始图片**不会被删除或移动**；本函数只在
      ``data/calibration/sessions/<id>/`` 下生成 ``source.jpg`` 副本。
    - 标定画布采用“传送带 + 安全边界”策略。
    - ``pixel_length`` / ``vertical_distance`` 仍表示传送带本体尺寸；
      ``output_width`` / ``output_height`` 表示最终矫正图尺寸。
    - 同时刷新 ``data/calibration/current_calibration.json``。
    """

    resolved = resolve_input_path(source_image)
    if resolved is None:
        raise FileNotFoundError(f"标定图片路径无效: {source_image!r}")
    source_image_abs = resolved
    if not source_image_abs.exists():
        raise FileNotFoundError(f"标定图片不存在: {source_image_abs}")

    image_bgr = _cv2().imread(str(source_image_abs))
    if image_bgr is None:
        raise ValueError(f"无法读取标定图片: {source_image_abs}")

    calibration_id = calibration_id or make_calibration_id()
    session_dir = calibration_session_dir(calibration_id)
    ensure_dir(session_dir)

    # 把原始图片复制为 session/source.jpg；绝不删除用户传入的原图。
    session_source = session_dir / "source.jpg"
    shutil.copyfile(str(source_image_abs), str(session_source))

    now = _dt.datetime.now()
    created_at = now.strftime("%Y-%m-%d %H:%M:%S")
    height, width = image_bgr.shape[:2]

    # 加载 GroundingDINO + SAM
    (
        _,
        build_model,
        SLConfig,
        clean_state_dict,
        SamPredictor,
        sam_hq_model_registry,
        sam_model_registry,
    ) = _ensure_dependencies()

    model = load_model(
        GROUNDING_DINO_CONFIG_PATH,
        GROUNDING_DINO_CHECKPOINT,
        BERT_BASE_UNCASED_DIR,
        DEVICE,
    )

    if USE_SAM_HQ and sam_hq_model_registry is not None:
        predictor = SamPredictor(
            sam_hq_model_registry[SAM_VERSION](
                checkpoint=str(SAM_HQ_VIT_H_CHECKPOINT)
            ).to(DEVICE)
        )
    else:
        predictor = SamPredictor(
            sam_model_registry[SAM_VERSION](
                checkpoint=str(SAM_VIT_H_CHECKPOINT)
            ).to(DEVICE)
        )

    image_pil, image_tensor = load_image(session_source)
    boxes_filt = get_boxes_only(
        model,
        image_tensor,
        TEXT_PROMPT,
        BOX_THRESHOLD,
        DEVICE,
    )
    if len(boxes_filt) == 0:
        raise RuntimeError(
            "标定失败：未在图片中检测到传送带，请重新拍摄或更换角度。"
        )

    image_rgb = _cv2().cvtColor(
        image_bgr,
        _cv2().COLOR_BGR2RGB,
    )
    predictor.set_image(image_rgb)

    H, W = image_pil.size[1], image_pil.size[0]
    boxes_filt = boxes_filt.clone()
    for i in range(boxes_filt.size(0)):
        boxes_filt[i] = boxes_filt[i] * _torch_Tensor(
            [W, H, W, H]
        )
        boxes_filt[i][:2] -= boxes_filt[i][2:] / 2
        boxes_filt[i][2:] += boxes_filt[i][:2]

    transformed_boxes = predictor.transform.apply_boxes_torch(
        boxes_filt.cpu(),
        image_rgb.shape[:2],
    ).to(DEVICE)

    masks, _, _ = predictor.predict_torch(
        point_coords=None,
        point_labels=None,
        boxes=transformed_boxes,
        multimask_output=False,
    )
    best_mask = (
        masks[0][0]
        .cpu()
        .numpy()
        .astype(_np().uint8)
        * 255
    )

    (
        warped_belt,
        vertical_distance,
        pixel_length,
        transform_matrix,
        source_points,
        target_points,
    ) = rectify_conveyor_belt(
        image_bgr,
        best_mask,
        side_margin_ratio=side_margin_ratio,
        vertical_margin_ratio=vertical_margin_ratio,
        margin_left_px=margin_left_px,
        margin_right_px=margin_right_px,
        margin_top_px=margin_top_px,
        margin_bottom_px=margin_bottom_px,
    )

    if warped_belt is None:
        raise RuntimeError(
            "标定失败：无法从掩码中提取传送带多边形，请重新拍摄。"
        )

    # 最终画布不再等于 pixel_length x vertical_distance。
    output_height, output_width = warped_belt.shape[:2]

    print(
        "[adjust] canvas_version="
        f"{ADJUST_CANVAS_VERSION}, "
        f"belt={int(pixel_length)}x{int(vertical_distance)}, "
        f"output={int(output_width)}x{int(output_height)}"
    )

    # 对最终矩阵直接求逆，确保逆矩阵与“透视 + margin 平移”完全一致。
    try:
        inverse_matrix = _np().linalg.inv(
            _np().asarray(
                transform_matrix,
                dtype=_np().float64,
            )
        )
    except _np().linalg.LinAlgError as exc:
        raise RuntimeError(
            "标定失败：最终透视矩阵不可逆。"
        ) from exc

    # 从最终 target_points 反推实际安全边界。
    # 这样即使外部入口临时替换 rectify_conveyor_belt，也能记录真实 margin。
    target_arr = _np().asarray(
        target_points,
        dtype=_np().float64,
    )
    target_left = int(round(float(_np().min(target_arr[:, 0]))))
    target_top = int(round(float(_np().min(target_arr[:, 1]))))

    actual_left = max(0, target_left)
    actual_top = max(0, target_top)
    actual_right = max(
        0,
        int(output_width) - int(pixel_length) - actual_left,
    )
    actual_bottom = max(
        0,
        int(output_height) - int(vertical_distance) - actual_top,
    )

    belt_mask_path = session_dir / "belt_mask.png"
    preview_path = session_dir / "adjusted_preview.jpg"

    if not _cv2().imwrite(str(belt_mask_path), best_mask):
        raise RuntimeError(
            f"无法写入传送带掩码：{belt_mask_path}"
        )
    if not _cv2().imwrite(str(preview_path), warped_belt):
        raise RuntimeError(
            f"无法写入矫正预览图：{preview_path}"
        )

    result = CalibrationResult(
        calibration_id=calibration_id,
        camera_id=camera_id,
        created_at=created_at,
        image_width=width,
        image_height=height,
        transform_matrix=transform_matrix,
        inverse_transform_matrix=inverse_matrix,
        source_points=source_points,
        target_points=target_points,
        vertical_distance=int(vertical_distance),
        pixel_length=int(pixel_length),
        source_image=session_source,
        belt_mask=belt_mask_path,
        adjusted_preview=preview_path,
        note=note,
        output_width=int(output_width),
        output_height=int(output_height),
        canvas_mode="belt_margin",
        canvas_margin_left_px=int(actual_left),
        canvas_margin_right_px=int(actual_right),
        canvas_margin_top_px=int(actual_top),
        canvas_margin_bottom_px=int(actual_bottom),
        canvas_side_margin_ratio=float(side_margin_ratio),
        canvas_vertical_margin_ratio=float(vertical_margin_ratio),
        canvas_version=ADJUST_CANVAS_VERSION,
    )

    # 写 calibration.json 与 current_calibration.json
    ensure_dir(CALIBRATION_DIR)
    calibration_payload = result.to_dict()

    # 物理尺度继续仅由“传送带本体宽度”计算，不受安全边界影响。
    calibration_payload["mm_per_pixel"] = _mm_per_pixel(
        vertical_distance,
        pixel_length,
        real_length_mm,
    )

    calibration_json_path = session_dir / "calibration.json"
    with open(
        calibration_json_path,
        "w",
        encoding="utf-8",
    ) as fp:
        json.dump(
            calibration_payload,
            fp,
            ensure_ascii=False,
            indent=2,
        )

    current_payload = {
        **calibration_payload,
        "source_image": calibration_payload["source_image"],
        "belt_mask": calibration_payload["belt_mask"],
        "adjusted_preview": calibration_payload["adjusted_preview"],
        "calibration_json": to_project_relative(
            calibration_json_path
        ),
    }

    ensure_dir(CALIBRATION_CURRENT_JSON.parent)
    with open(
        CALIBRATION_CURRENT_JSON,
        "w",
        encoding="utf-8",
    ) as fp:
        json.dump(
            current_payload,
            fp,
            ensure_ascii=False,
            indent=2,
        )

    return result


def _mm_per_pixel(vertical_distance: int | None, pixel_length: int | None,
                  real_length_mm: float) -> float | None:
    """根据传送带像素长度估算 ``mm_per_pixel``。

    仅有 ``pixel_length`` 时可用；否则返回 ``None``，调用方可以自行 fallback。
    """

    if not pixel_length or pixel_length <= 0:
        return None
    try:
        return float(real_length_mm) / float(pixel_length)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _torch_Tensor(values):
    import torch

    return torch.Tensor(values)


# ---------------------------------------------------------------------------
# batch 模式主流程
# ---------------------------------------------------------------------------


def _iter_input_frames(input_dir: Path) -> Iterable[Path]:
    yield from iter_matching_files(
        input_dir,
        suffixes=[".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"],
    )


def _sorted_input_frames(input_dir: Path) -> list[Path]:
    """按帧编号数值排序的输入图列表（兼容 6 位 / 12 位）。"""

    frames = list(_iter_input_frames(input_dir))
    frames.sort(key=lambda p: (parse_frame_index(p.stem) is None, parse_frame_index(p.stem) or 0, p.name))
    return frames


def _warp_with_transform(
    image_bgr: _np().ndarray,
    transform_matrix: _np().ndarray,
    output_size: tuple[int, int],
) -> _np().ndarray:
    return _cv2().warpPerspective(
        image_bgr,
        transform_matrix,
        output_size,
        flags=_cv2().INTER_LINEAR,
        borderMode=_cv2().BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )


def rectify_image_folder(
    input_dir: Path,
    output_dir: Path,
    calibration_json: Path,
    video_id: str | None = None,
) -> int:
    """批量矫正 ``input_dir`` 中的图片。"""

    if input_dir is None:
        raise FileNotFoundError("input_dir 不能为空")
    if calibration_json is None:
        raise FileNotFoundError("calibration_json 不能为空")

    input_dir = resolve_input_path(input_dir)
    output_dir = resolve_input_path(output_dir)
    calibration_json = resolve_input_path(calibration_json)

    if input_dir is None or not input_dir.exists():
        raise FileNotFoundError(f"输入目录不存在: {input_dir}")
    if not calibration_json.is_file():
        raise FileNotFoundError(f"标定文件不存在: {calibration_json}")
    if output_dir is None:
        raise ValueError("output_dir 不能为空")

    with open(calibration_json, "r", encoding="utf-8") as fp:
        payload = json.load(fp)
    M = _np().array(
        payload["transform_matrix"],
        dtype=_np().float64,
    )

    # 新版标定 JSON：使用最终“传送带 + 安全边界”画布尺寸。
    # 旧版 JSON 没有 output_width/output_height 时，退回旧字段以保持兼容。
    output_width = payload.get("output_width")
    output_height = payload.get("output_height")

    if output_width is None or output_height is None:
        output_width = payload.get("pixel_length")
        output_height = payload.get("vertical_distance")
        print(
            "[WARN] 当前 calibration.json 为旧格式，缺少 "
            "output_width/output_height；将使用 "
            "pixel_length/vertical_distance 作为输出尺寸。"
            "若要保留传送带边缘完整鱼，请重新执行一次标定。"
        )

    if output_width is None or output_height is None:
        raise ValueError(
            "标定文件同时缺少 output_width/output_height 和 "
            "pixel_length/vertical_distance，无法确定输出尺寸。"
        )

    output_width = int(output_width)
    output_height = int(output_height)
    if output_width <= 0 or output_height <= 0:
        raise ValueError(
            f"标定文件中的输出尺寸无效："
            f"{output_width}x{output_height}"
        )

    output_size = (
        output_width,
        output_height,
    )

    print(
        "[adjust batch] "
        f"canvas_version={payload.get('canvas_version', 'legacy')}, "
        f"output_size={output_width}x{output_height}, "
        f"belt_size={payload.get('pixel_length')}x"
        f"{payload.get('vertical_distance')}"
    )

    ensure_dir(output_dir)
    frames = _sorted_input_frames(input_dir)
    if not frames:
        print(f"[WARN] 输入目录中没有图片: {input_dir}")
        return 0

    success = 0
    for idx, image_path in enumerate(frames, start=1):
        image_bgr = _cv2().imread(str(image_path))
        if image_bgr is None:
            print(f"[WARN] 无法读取图片: {image_path}")
            continue
        warped = _warp_with_transform(image_bgr, M, output_size)
        frame_index = parse_frame_index(image_path.stem)
        if frame_index is None or frame_index < 1:
            frame_index = idx
        # 12 位编号：永不输出 000000000000.jpg；旧 6 位文件也能被解析。
        save_path = output_dir / (format_frame_index(frame_index) + ".jpg")
        _cv2().imwrite(str(save_path), warped)
        success += 1
        if idx % 50 == 0 or idx == len(frames):
            print(f"矫正进度: {idx}/{len(frames)}")

    print(f"批量矫正完成: 输入 {len(frames)} 张，成功 {success} 张，输出目录 {output_dir}")
    return success


def _safe_delete_raw(raw_dir: Path, video_id: str) -> None:
    """使用 config.cleanup 安全删除 raw/。"""

    expected_raw = video_frames_raw_dir(video_id).resolve()
    target = raw_dir.resolve()
    try:
        target.relative_to(expected_raw)
    except ValueError as exc:
        raise ValueError(
            f"待删除目录 {target} 不是 video_id={video_id} 的 raw/，拒绝执行。"
        ) from exc
    remove_dir_contents(expected_raw)
    print(f"已清理 raw/：{expected_raw}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _add_calibrate_parser(sub) -> None:
    """legacy：曾经的 calibrate 子命令构建器。

    ``adjust`` 不再支持 ``calibrate`` 子命令。请使用
    ``python -m algorithms.core.adjust_calibration``。
    本函数保留以避免外部脚本 import 失败，但内部已不再注册。
    """

    return


def _add_batch_parser(sub) -> None:
    sub.add_argument("--input-dir", required=True,
                     help="原始帧输入目录（6/12 位均可读），支持绝对路径或相对项目根")
    sub.add_argument("--output-dir", required=True,
                     help="矫正后帧输出目录（统一 12 位），支持绝对路径或相对项目根")
    sub.add_argument(
        "--calibration-json",
        default=None,
        help="可选：本次使用的标定 JSON 路径；省略时使用 data/calibration/current_calibration.json",
    )
    sub.add_argument(
        "--video-id",
        default=None,
        help="作业视频 ID。传入时清理 raw/ 前会校验目录，否则仅矫正输出。",
    )
    sub.add_argument(
        "--delete-input",
        action="store_true",
        help="兼容旧参数：矫正完成后删除 raw/。推荐改用 --keep-raw-frames 反义控制。",
    )
    sub.add_argument(
        "--keep-raw-frames",
        action="store_true",
        help="保留原始 raw 帧；不传此参数时默认会删除 raw/。",
    )


def build_parser() -> argparse.ArgumentParser:
    """``algorithms.core.adjust`` 现在只负责批量帧矫正。

    标定（生成透视矩阵）请使用 ``algorithms.core.adjust_calibration``。
    """

    parser = argparse.ArgumentParser(description="传送带批量帧矫正（按透视矩阵把 raw 帧矫正为 adjusted 帧）")
    sub = parser.add_subparsers(dest="mode", required=True)
    bat = sub.add_parser("batch", help="批量矫正帧图（推荐入口）")
    _add_batch_parser(bat)
    return parser


def _remove_first_level(directory: Path) -> None:
    """删除 ``directory`` 下的所有内容（保留目录本身）。"""

    if not directory.exists():
        return
    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            try:
                child.unlink()
            except FileNotFoundError:
                continue


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.mode == "batch":
        calibration_json = (
            resolve_input_path(args.calibration_json)
            if args.calibration_json
            else CALIBRATION_CURRENT_JSON
        )
        if not calibration_json.is_file():
            print(
                f"[ERROR] 找不到透视矩阵：{calibration_json}。"
                "请先运行 ``python -m algorithms.core.adjust_calibration`` 生成 "
                "``data/calibration/current_calibration.json``，或显式传入 --calibration-json。"
            )
            return 2
        input_dir = resolve_input_path(args.input_dir)
        output_dir = resolve_input_path(args.output_dir)

        success = rectify_image_folder(
            input_dir=input_dir,
            output_dir=output_dir,
            calibration_json=calibration_json,
            video_id=args.video_id,
        )

        delete_raw = (not args.keep_raw_frames) or args.delete_input
        if delete_raw and args.video_id and input_dir is not None:
            expected_raw = video_frames_raw_dir(args.video_id).resolve()
            try:
                input_dir.relative_to(expected_raw)
            except ValueError:
                pass
            else:
                _safe_delete_raw(expected_raw, args.video_id)
        return 0 if success > 0 else 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
