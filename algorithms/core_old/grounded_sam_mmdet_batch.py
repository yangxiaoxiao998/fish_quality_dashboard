"""Grounded-SAM 批量推理脚本（基于 MMDetection GroundingDINO）。

读取矫正后的帧图，运行 GroundingDINO + SAM，得到：

- ``crop_images/annotated_frames/<12-digit>_grounded_sam.jpg``
- ``crop_images/first_match/id_<track>_<12-digit>_crop.jpg``
- ``crop_images/first_match/id_<track>_<12-digit>_mask_crop_bw.png``
- ``videos/grounded_sam.mp4``

所有路径从 ``config/paths.py`` 获取。新文件统一 12 位编号；
旧 6 位命名（如 ``id_3_000001_crop.jpg``）仍可被读入。

GroundingDINO config / checkpoint 配套关系
------------------------------------------

- 默认 ``--config``    = ``algorithms/algorithms/gaoyuxi/grounding_dino_swin-t_finetune_8xb4_50e_fish.py``
- 默认 ``--grounded-checkpoint`` = ``algorithms/pth/best_coco_bbox_mAP_epoch_12.pth``
- 默认 ``--sam-checkpoint``     = ``algorithms/pth/sam_vit_h_4b8939.pth``
- ``--sam-version`` 默认 ``vit_h``；``--use-sam-hq`` 默认关闭

config 与 checkpoint **必须来自同一训练架构**，否则会报类似
``Size([1280]) vs Size([1024])`` 的尺寸不匹配错误。本脚本会在加载阶段捕获该错误
并抛出中文友好提示。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from pathlib import Path
from typing import Iterable, Sequence, TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型检查，运行时延后加载
    import numpy as np  # noqa: F401

THIS_DIR = Path(__file__).resolve().parent
ALGORITHMS_ROOT = THIS_DIR.parent
if str(ALGORITHMS_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS_ROOT))


# ---------------------------------------------------------------------------
# 离线 / 兼容性环境变量（在 import mmengine / transformers 之前设置）
# ---------------------------------------------------------------------------

# 让 ``transformers.AutoTokenizer.from_pretrained`` 不尝试访问 huggingface.co。
# 我们的 ``bert-base-uncased`` 已经在 ``algorithms/algorithms/gaoyuxi/bert-base-uncased``。
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
# 直接指向本地 BERT，避免 mmdet 内部再发起网络请求
os.environ.setdefault(
    "GDINO_BERT_PATH",
    str(ALGORITHMS_ROOT / "algorithms" / "gaoyuxi" / "bert-base-uncased"),
)


from config.naming import (  # noqa: E402  pylint: disable=wrong-import-position
    ANNOTATED_SUFFIX,
    extract_frame_index_from_path,
    make_annotated_frame_name,
    make_fish_crop_name,
    make_fish_mask_name,
    parse_frame_index,
)
from config.paths import (  # noqa: E402  pylint: disable=wrong-import-position
    GROUNDED_SAM_DIR,
    MM_DETECTION_CHECKPOINT,
    PROJECT_ROOT,
    SAM_VIT_H_CHECKPOINT,
    SAM_HQ_VIT_H_CHECKPOINT,
    GROUNDING_DINO_CONFIG_PATH,
    ensure_dir,
    resolve_input_path,
    to_project_relative,
    video_annotated_frames_dir,
    video_first_match_dir,
    video_frames_adjusted_dir,
    video_grounded_sam_video,
)


# ---------------------------------------------------------------------------
# 延迟加载 cv2 / matplotlib / supervision / torch；导入失败不应让 --help 崩
# ---------------------------------------------------------------------------


def _cv2():
    import cv2  # type: ignore

    return cv2


def _plt():
    import matplotlib.pyplot as plt  # type: ignore

    return plt


def _np():
    import numpy as np  # type: ignore

    return np


def _sv():
    import supervision as sv  # type: ignore

    return sv


def _torch():
    import torch  # type: ignore

    return torch


# 兼容旧版 mmengine/mmdet checkpoint：
# 这一段会调用 torch.load，因此延迟到真正推理时再执行。
def _install_torch_compat():
    """注册 mmengine 安全 globals + 默认 ``weights_only=False`` 的 ``torch.load``。"""

    torch = _torch()
    if getattr(_install_torch_compat, "_done", False):
        return torch

    # mmengine 训练 checkpoint 中常含 ``HistoryBuffer`` 等自定义对象；
    # PyTorch >= 2.6 默认 ``weights_only=True`` 会拒绝。允许这些已知类型。
    try:
        from mmengine.logging.history_buffer import HistoryBuffer  # type: ignore

        torch.serialization.add_safe_globals([HistoryBuffer])
    except Exception:
        pass
    try:
        from mmengine.logging.message_hub import MessageHub  # type: ignore

        torch.serialization.add_safe_globals([MessageHub])
    except Exception:
        pass

    _orig_torch_load = torch.load

    def _torch_load_compat(*args, **kwargs):
        kwargs.setdefault("weights_only", False)
        return _orig_torch_load(*args, **kwargs)

    torch.load = _torch_load_compat
    _install_torch_compat._done = True
    return torch


def _bootstrap_dependencies() -> None:
    """确保 upstream 仓库在 ``sys.path`` 中。"""

    # core/grounded_sam_mmdet_batch.py 位于 algorithms/core/；
    # 上游仓库位于 algorithms/algorithms/gaoyuxi/
    upstream_root = Path(__file__).resolve().parents[1] / "algorithms" / "gaoyuxi"
    for candidate in [
        upstream_root / "Grounded-Segment-Anything",
        upstream_root / "Grounded-Segment-Anything" / "segment_anything",
    ]:
        if candidate.exists() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
    grounded_sam = Path(GROUNDED_SAM_DIR)
    if grounded_sam.exists() and str(grounded_sam) not in sys.path:
        sys.path.insert(0, str(grounded_sam))


_bootstrap_dependencies()

try:
    from trackers import SORTTracker  # type: ignore  # noqa: E402
except ImportError:
    SORTTracker = None  # type: ignore  # noqa: E402

try:
    from segment_anything import (  # type: ignore  # noqa: E402
        SamPredictor,
        sam_hq_model_registry,
        sam_model_registry,
    )
except ImportError:
    SamPredictor = None  # type: ignore  # noqa: E402
    sam_hq_model_registry = None  # type: ignore  # noqa: E402
    sam_model_registry = None  # type: ignore  # noqa: E402


DEFAULT_TEXT_PROMPT = "fish."

# 透视矩阵已禁止使用任何写死默认值。
# 真实数据来自系统「视觉矫正」页面保存的 JSON：
#   data/calibration/current_calibration.json
# 通过 core.calibration_config.load_system_transform_matrix 读取。


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".webp", ".tif"}
LEFT_EDGE_IGNORE_RATIO = 0.02
RIGHT_EDGE_IGNORE_RATIO = 0.02
CURRENT_IMG_WIDTH = None
CURRENT_IMG_DPI = None

warnings.filterwarnings("ignore", message=".*__floordiv__ is deprecated.*", category=UserWarning)
warnings.filterwarnings("ignore", message=".*position encoding of key ismissing.*", category=UserWarning)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def get_track_color(track_id, alpha=0.6):
    np = _np()
    if track_id is None:
        return _np().array([30 / 255, 144 / 255, 255 / 255, alpha])

    track_index = abs(int(track_id))
    rng = _np().random.default_rng((track_index + 1) * 9973 + 17)
    color = rng.random(3) * 0.6 + 0.25
    return _np().concatenate([color, _np().array([alpha])], axis=0)


def show_mask(mask, ax, color=None):
    np = _np()
    plt = _plt()
    if color is None:
        color = _np().array([30 / 255, 144 / 255, 255 / 255, 0.6])
    h, w = mask.shape[-2:]
    mask_image = mask.reshape(h, w, 1) * color.reshape(1, 1, -1)
    ax.imshow(mask_image)


def show_box(box, ax, label, color=None):
    plt = _plt()
    x0, y0 = box[0], box[1]
    w, h = box[2] - box[0], box[3] - box[1]
    if color is None:
        color = _np().array([0.0, 0.5, 0.0, 1.0]) if False else _np().array([0.0, 0.5, 0.0, 1.0])
    ax.add_patch(plt.Rectangle((x0, y0), w, h, edgecolor=color[:3], facecolor=(0, 0, 0, 0), lw=2))

    fontsize = 10
    dpi = CURRENT_IMG_DPI or plt.gcf().dpi
    img_w = CURRENT_IMG_WIDTH
    char_px = fontsize * (dpi / 72.0) * 0.6
    try:
        max_chars_right = int((img_w - x0) / char_px) if (img_w is not None) else None
    except Exception:
        max_chars_right = None

    if max_chars_right is None or max_chars_right <= 0:
        text_to_draw = label
        text_x = x0
        text_y = y0
    else:
        if len(label) > max_chars_right:
            first = label[:max_chars_right].rstrip()
            rest = label[max_chars_right:].lstrip()
            text_to_draw = first + "\n" + rest
            text_x = x0
            line_height_px = fontsize * (dpi / 72.0) * 1.2
            text_y = y0 - line_height_px
        else:
            text_to_draw = label
            text_x = x0
            text_y = y0

    ax.text(text_x, text_y, text_to_draw, color=color[:3], fontsize=fontsize,
            bbox={"facecolor": "black", "alpha": 0.35, "pad": 1}, va='top')


def filter_masks_by_edges(masks, boxes, labels, track_ids, image_shape,
                          left_ratio=LEFT_EDGE_IGNORE_RATIO,
                          right_ratio=RIGHT_EDGE_IGNORE_RATIO):
    np = _np()
    torch = _torch()

    image_height, image_width = image_shape[:2]
    left_w = max(1, int(_np().ceil(image_width * left_ratio))) if image_width > 0 and left_ratio > 0 else 0
    right_w = max(1, int(_np().ceil(image_width * right_ratio))) if image_width > 0 and right_ratio > 0 else 0

    kept_masks, kept_boxes, kept_labels, kept_track_ids = [], [], [], []
    for idx, mask in enumerate(masks):
        mask_bool = mask.detach().cpu().numpy()[0].astype(bool)
        if left_w > 0 and mask_bool[:, :left_w].any():
            continue
        if right_w > 0 and mask_bool[:, max(0, image_width - right_w):].any():
            continue
        if left_w > 0:
            mask_bool[:, :left_w] = False
        if right_w > 0:
            mask_bool[:, max(0, image_width - right_w):] = False
        kept_masks.append(torch.from_numpy(mask_bool[None, ...]).to(device=mask.device))
        kept_boxes.append(boxes[idx])
        kept_labels.append(labels[idx])
        try:
            x0, y0, x1, y1 = boxes[idx].cpu().numpy().tolist()
        except Exception:
            kept_track_ids.append(None if track_ids is None else track_ids[idx])
            continue
        central_x0 = left_w
        central_x1 = max(0, image_width - right_w)
        if x0 < central_x0 or x1 > central_x1:
            kept_track_ids.append(None)
        else:
            kept_track_ids.append(None if track_ids is None else track_ids[idx])
    return kept_masks, kept_boxes, kept_labels, kept_track_ids


def _iter_input_images(input_dir: Path) -> list[Path]:
    """按数值帧号升序遍历 ``input_dir`` 下的图片，兼容 6 位 / 12 位。"""

    if not input_dir.exists():
        return []
    items: list[tuple[int, str]] = []
    for entry in input_dir.iterdir():
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        idx = parse_frame_index(entry.stem)
        if idx is None:
            continue
        items.append((idx, entry.name))
    items.sort(key=lambda x: x[0])
    return [input_dir / name for _, name in items]


def build_image_list(input_image, input_dir):
    if input_image:
        return [Path(input_image)]
    if not input_dir:
        return []
    return _iter_input_images(Path(input_dir))


def get_frame_index(image_path):
    return extract_frame_index_from_path(Path(image_path))


def filter_image_list_by_frame_range(image_paths, frame_start, frame_end):
    if frame_start is None and frame_end is None:
        return image_paths
    filtered = []
    for image_path in image_paths:
        idx = get_frame_index(image_path)
        if idx is None:
            continue
        if frame_start is not None and idx < frame_start:
            continue
        if frame_end is not None and idx > frame_end:
            continue
        filtered.append(image_path)
    return filtered


def extract_predictions(result, score_thr, default_label):
    torch = _torch()
    pred_instances = getattr(result, "pred_instances", None)
    if pred_instances is None:
        return torch.empty((0, 4)), []

    boxes = pred_instances.bboxes
    scores = pred_instances.scores
    label_names = getattr(pred_instances, "label_names", None)
    if boxes is None or len(boxes) == 0:
        return torch.empty((0, 4)), []
    if scores is None:
        keep = torch.ones(len(boxes), dtype=torch.bool)
    else:
        keep = scores >= score_thr
    boxes = boxes[keep].detach().cpu()
    if scores is not None:
        scores = scores[keep].detach().cpu()
    if label_names is None:
        label_names = [default_label] * len(boxes)
    else:
        keep_list = keep.tolist()
        label_names = [label_names[idx] for idx, ok in enumerate(keep_list) if ok]

    phrases = []
    if scores is None:
        phrases = [str(name) for name in label_names]
    else:
        for name, score in zip(label_names, scores.tolist()):
            phrases.append(f"{name}({score:.3f})")
    return boxes, phrases


def build_detections(boxes):
    np = _np()
    sv = _sv()
    if boxes.numel() == 0:
        return sv.Detections(xyxy=_np().empty((0, 4), dtype=float))
    return sv.Detections(
        xyxy=boxes.numpy(),
        confidence=_np().ones(len(boxes), dtype=float),
        class_id=_np().zeros(len(boxes), dtype=int),
    )


def normalize_track_ids(track_ids, count):
    if track_ids is None:
        return list(range(count))
    normalized = []
    for idx in range(count):
        track_id = track_ids[idx] if idx < len(track_ids) else None
        if track_id is None:
            normalized.append(idx)
            continue
        track_value = int(track_id)
        normalized.append(idx if track_value < 0 else track_value)
    return normalized


def save_first_seen_id_samples(output_root, frame_index, image_rgb, masks, track_ids, seen_track_ids):
    """每个 track_id 只保留第一次出现时的彩色 crop 与黑白掩码。"""

    if output_root is None or seen_track_ids is None:
        return
    cv2 = _cv2()
    np = _np()
    ensure_dir(output_root)

    for mask, track_id in zip(masks, track_ids):
        if track_id is None:
            continue
        track_id_int = int(track_id)
        if track_id_int in seen_track_ids:
            continue
        seen_track_ids.add(track_id_int)

        mask_bool = mask.cpu().numpy()[0].astype(bool)
        ys, xs = _np().where(mask_bool)
        if ys.size == 0 or xs.size == 0:
            continue

        y_min, y_max = int(ys.min()), int(ys.max())
        x_min, x_max = int(xs.min()), int(xs.max())

        scale = 1.2
        h = y_max - y_min + 1
        w = x_max - x_min + 1
        cy = y_min + h / 2.0
        cx = x_min + w / 2.0
        new_h = max(1, int(round(h * scale)))
        new_w = max(1, int(round(w * scale)))
        new_y_min = int(round(cy - new_h / 2.0))
        new_y_max = new_y_min + new_h - 1
        new_x_min = int(round(cx - new_w / 2.0))
        new_x_max = new_x_min + new_w - 1

        img_h, img_w = image_rgb.shape[:2]
        new_y_min = max(0, new_y_min)
        new_x_min = max(0, new_x_min)
        new_y_max = min(img_h - 1, new_y_max)
        new_x_max = min(img_w - 1, new_x_max)

        crop_rgb = image_rgb[new_y_min:new_y_max + 1, new_x_min:new_x_max + 1]
        crop_name = make_fish_crop_name(track_id_int, frame_index)
        crop_path = output_root / crop_name
        cv2.imwrite(str(crop_path), cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2BGR))

        crop_mask = (mask_bool[new_y_min:new_y_max + 1, new_x_min:new_x_max + 1].astype(_np().uint8) * 255)
        mask_name = make_fish_mask_name(track_id_int, frame_index)
        mask_path = output_root / mask_name
        cv2.imwrite(str(mask_path), crop_mask)


def collect_annotated_frames(directory: Path) -> list[Path]:
    annotated_paths: list[Path] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        if not path.name.endswith(ANNOTATED_SUFFIX):
            continue
        annotated_paths.append(path)
    annotated_paths.sort(key=lambda p: get_frame_index(p) if get_frame_index(p) is not None else p.name)
    return annotated_paths


def create_video_from_images(image_paths, video_output_path, fps):
    cv2 = _cv2()
    if not image_paths:
        print(f"[WARN] No images found for video creation: {video_output_path}")
        return
    first_frame = cv2.imread(str(image_paths[0]))
    if first_frame is None:
        print(f"[WARN] Failed to read first frame: {image_paths[0]}")
        return
    height, width = first_frame.shape[:2]
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(video_output_path), fourcc, fps, (width, height))
    for image_path in image_paths:
        frame = cv2.imread(str(image_path))
        if frame is None:
            print(f"[WARN] Skipping unreadable frame: {image_path}")
            continue
        if frame.shape[:2] != (height, width):
            frame = cv2.resize(frame, (width, height))
        writer.write(frame)
    writer.release()
    print(f"[INFO] Video saved to: {video_output_path}")

    # 写完 mp4 后立刻做浏览器兼容处理：cv2.VideoWriter + mp4v 写出的 mp4
    # 是 mpeg4 / Simple Profile，Firefox / Chrome 在 <video> 中会报
    # "No video with supported format and MIMEtype found"。
    # 这里调 ensure_browser_compatible_mp4 转成 H.264 / yuv420p / +faststart。
    try:
        from pathlib import Path as _P
        from algorithms.tools.video_compat import ensure_browser_compatible_mp4
        ok = ensure_browser_compatible_mp4(_P(str(video_output_path)))
        if ok:
            print(f"[INFO] Browser-compatible video confirmed: {video_output_path}")
        else:
            print(
                f"[WARN] ensure_browser_compatible_mp4 failed; original kept: {video_output_path}"
            )
    except Exception as _exc:
        print(f"[WARN] ensure_browser_compatible_mp4 raised: {_exc}")


def _warp_with_transform(image_bgr, transform_matrix):
    """历史保留：不再在 ``process_one_image`` 中调用；保留供旧调用方使用。"""

    cv2 = _cv2()
    np = _np()
    h, w = image_bgr.shape[:2]
    corners = _np().array([[0, 0], [w, 0], [w, h], [0, h]], dtype=_np().float32).reshape(-1, 1, 2)
    transformed_corners = cv2.perspectiveTransform(corners, transform_matrix)
    x_min = int(_np().floor(transformed_corners[:, 0, 0].min()))
    y_min = int(_np().floor(transformed_corners[:, 0, 1].min()))
    x_max = int(_np().ceil(transformed_corners[:, 0, 0].max()))
    y_max = int(_np().ceil(transformed_corners[:, 0, 1].max()))
    new_w = x_max - x_min
    new_h = y_max - y_min
    translation_matrix = _np().array([[1, 0, -x_min], [0, 1, -y_min], [0, 0, 1]], dtype=_np().float32)
    M_adjusted = translation_matrix.dot(transform_matrix)
    return cv2.warpPerspective(image_bgr, M_adjusted, (new_w, new_h))


def process_one_image(
    model,
    image_path,
    annotated_output_dir,
    text_prompt,
    box_threshold,
    predictor,
    device,
    tracker=None,
    first_match_output_dir=None,
    seen_track_ids=None,
    transform_matrix=None,  # 历史入参，**忽略**，不再对 adjusted 帧做透视变换
):
    """对一张 adjusted 帧执行 GroundedDINO + SAM，并直接画框、画 mask、写图。

    关键约定：

    1. 输入帧已经是 ``adjust batch`` 矫正后的 ``adjusted`` 帧，
       **不再**对输入做任何 ``warpPerspective / rotate / transpose``。
    2. ``annotated_output_path`` 与 ``adjusted`` 帧**完全同方向同尺寸**，
       只多出检测框、mask 叠加和 track id。
    3. ``first_match`` crop 与 mask 的坐标系与 ``adjusted`` 帧一致，
       留给 ``filter_complete_fish`` 做后续旋转矫正。
    """

    cv2 = _cv2()
    np = _np()
    torch = _torch()

    frame_index = extract_frame_index_from_path(Path(image_path))
    if frame_index is None:
        print(f"[WARN] 无法解析帧编号：{image_path}")
        return None

    image_bgr = cv2.imread(str(image_path))
    if image_bgr is None:
        print(f"[ERROR] Could not read image: {image_path}")
        return None

    # 注意：传入的 ``transform_matrix`` 被故意忽略，保留参数仅为兼容旧调用方。
    _ = transform_matrix

    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    predictor.set_image(image_rgb)

    from mmdet.apis import inference_detector  # type: ignore  # noqa: WPS433

    result = inference_detector(model, image_bgr, text_prompt=text_prompt, custom_entities=True)
    boxes, phrases = extract_predictions(result, box_threshold, text_prompt.rstrip(".").strip() or "object")
    if boxes.numel() == 0:
        print(f"[WARN] No detections for: {image_path}")
        return None

    detections = build_detections(boxes)
    if tracker is not None:
        tracked_detections = tracker.update(detections)
    else:
        tracked_detections = detections
    raw_track_ids = getattr(tracked_detections, "tracker_id", None)

    boxes_device = boxes.to(device)
    transformed_boxes = predictor.transform.apply_boxes_torch(boxes_device, image_rgb.shape[:2]).to(device)
    masks, _, _ = predictor.predict_torch(
        point_coords=None,
        point_labels=None,
        boxes=transformed_boxes,
        multimask_output=False,
    )

    masks, boxes, phrases, raw_track_ids = filter_masks_by_edges(
        masks=masks,
        boxes=boxes,
        labels=phrases,
        track_ids=raw_track_ids,
        image_shape=image_rgb.shape,
    )
    if len(masks) == 0:
        print(f"[WARN] All detections were filtered out for: {image_path}")
        return None

    track_ids = normalize_track_ids(raw_track_ids, len(masks))

    save_first_seen_id_samples(
        output_root=Path(first_match_output_dir) if first_match_output_dir else None,
        frame_index=frame_index,
        image_rgb=image_rgb,
        masks=masks,
        track_ids=track_ids,
        seen_track_ids=seen_track_ids,
    )

    annotated_path = annotated_output_dir / make_annotated_frame_name(frame_index)
    annotated_bgr = _draw_annotations_on_bgr(
        image_bgr=image_bgr,
        masks=masks,
        boxes=boxes,
        phrases=phrases,
        track_ids=track_ids,
    )
    cv2.imwrite(str(annotated_path), annotated_bgr)
    return annotated_path


def _draw_annotations_on_bgr(image_bgr, masks, boxes, phrases, track_ids):
    """直接基于 ``image_bgr.copy()`` 画 mask / bbox / track id。

    不对原图做旋转、warp 或 transpose；保存的 annotated 与 input adjusted 帧
    **完全同方向同尺寸**。
    """

    cv2 = _cv2()
    np = _np()
    annotated = image_bgr.copy()

    for mask, track_id in zip(masks, track_ids):
        color_f32 = get_track_color(track_id)
        color_bgr = (
            int(color_f32[2] * 255),
            int(color_f32[1] * 255),
            int(color_f32[0] * 255),
        )
        mask_bool = mask.detach().cpu().numpy()[0].astype(bool)
        if mask_bool.shape[:2] != annotated.shape[:2]:
            # mask 尺寸与 annotated 不一致时跳过绘制，但保留 box
            continue
        overlay = annotated.copy()
        overlay[mask_bool] = color_bgr
        annotated = cv2.addWeighted(annotated, 0.6, overlay, 0.4, 0)

    for box, label, track_id in zip(boxes, phrases, track_ids):
        try:
            x0, y0, x1, y1 = box.cpu().numpy().tolist()
        except Exception:
            continue
        color_f32 = get_track_color(track_id)
        color_bgr = (
            int(color_f32[2] * 255),
            int(color_f32[1] * 255),
            int(color_f32[0] * 255),
        )
        x0i, y0i, x1i, y1i = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
        cv2.rectangle(annotated, (x0i, y0i), (x1i, y1i), color_bgr, thickness=3)
        text = f"{label} | ID {track_id}"
        (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        ty0 = max(0, y0i - th - baseline - 4)
        cv2.rectangle(annotated, (x0i, ty0), (x0i + tw + 4, ty0 + th + baseline + 4), color_bgr, thickness=-1)
        cv2.putText(
            annotated,
            text,
            (x0i + 2, ty0 + th + 1),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

    return annotated


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------


def _resolve_project_relative(path: str | Path | None) -> Path | None:
    """``config.paths.resolve_input_path`` 的本地别名，支持绝对路径。"""

    return resolve_input_path(path)


def _log_path(p: Path) -> str:
    return to_project_relative(p)


def resolve_pipeline_paths(args) -> tuple[Path, Path, Path]:
    """根据 ``--video-id`` 或显式 CLI 参数返回 ``(annotated, first_match, video)`` 三个目录。"""

    if getattr(args, "video_id", None):
        annotated = video_annotated_frames_dir(args.video_id)
        first_match = video_first_match_dir(args.video_id)
        video_out = video_grounded_sam_video(args.video_id)
    else:
        annotated = _resolve_project_relative(args.output_dir)
        first_match = (
            _resolve_project_relative(args.first_match_output_dir)
            if getattr(args, "first_match_output_dir", None)
            else None
        )
        video_out = (
            _resolve_project_relative(args.video_output)
            if getattr(args, "video_output", None)
            else None
        )
    return annotated, first_match, video_out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _resolve_input_dir(args) -> Path:
    if getattr(args, "input_dir", None):
        return _resolve_project_relative(args.input_dir)
    if getattr(args, "input_image", None):
        return _resolve_project_relative(args.input_image).parent
    if getattr(args, "video_id", None):
        return video_frames_adjusted_dir(args.video_id)
    raise FileNotFoundError(
        "未指定 --input-dir / --input-image / --video-id；"
        "请显式传入输入目录或 --video-id。"
    )


def _init_grounding_dino_with_friendly_error(config_path: str, checkpoint_path: str, device: str):
    """包装 ``mmdet.apis.init_detector``，捕获 config/checkpoint 不匹配错误。"""

    _install_torch_compat()
    from mmdet.apis import init_detector  # type: ignore  # noqa: WPS433

    try:
        return init_detector(config_path, checkpoint_path, device=device)
    except RuntimeError as exc:
        message = str(exc)
        if "size mismatch" in message or "shapes cannot be multiplied" in message or "cannot be multiplied" in message:
            friendly = (
                "\n[ERROR] 当前 GroundingDINO 配置文件与权重文件不匹配：\n"
                f"  config     = {config_path}\n"
                f"  checkpoint = {checkpoint_path}\n"
                f"  底层错误   = {type(exc).__name__}: {message[:1000]}\n"
                "\n常见原因：\n"
                "  1. Swin-T 权重配了 Swin-B 配置（embed_dim 96 vs 128）。\n"
                "  2. fish 领域 finetune checkpoint (best_coco_bbox_mAP_epoch_12.pth)\n"
                "     必须配 grounding_dino_swin-t_finetune_8xb4_50e_fish.py。\n"
                "  3. groundingdino_swint_ogc.pth 是上游预训练，必须配\n"
                "     GroundingDINO_SwinT_OGC.py（hidden_dim=256）。\n"
                "\n请显式传入匹配的 --config 与 --grounded-checkpoint：\n"
                "  --config algorithms/algorithms/gaoyuxi/grounding_dino_swin-t_finetune_8xb4_50e_fish.py\n"
                "  --grounded-checkpoint algorithms/pth/best_coco_bbox_mAP_epoch_12.pth\n"
            )
            raise RuntimeError(friendly) from exc
        raise
    except Exception as exc:
        # 兜底：把"加载失败"也提示出来
        friendly = (
            "\n[ERROR] GroundingDINO 模型加载失败：\n"
            f"  config     = {config_path}\n"
            f"  checkpoint = {checkpoint_path}\n"
            f"  底层错误   = {type(exc).__name__}: {str(exc)[:1000]}\n"
            "请确认 config 与 checkpoint 是否来自同一训练架构。\n"
        )
        raise RuntimeError(friendly) from exc


def run(args) -> int:
    annotated_dir, first_match_dir, video_output_path = resolve_pipeline_paths(args)
    ensure_dir(annotated_dir)
    if first_match_dir is not None:
        ensure_dir(first_match_dir)

    _install_torch_compat()

    config_path = str(args.config) if args.config else str(GROUNDING_DINO_CONFIG_PATH)
    grounded_checkpoint = str(args.grounded_checkpoint) if args.grounded_checkpoint else str(MM_DETECTION_CHECKPOINT)
    sam_checkpoint = str(args.sam_checkpoint) if args.sam_checkpoint else str(SAM_VIT_H_CHECKPOINT)
    sam_hq_checkpoint = (
        str(args.sam_hq_checkpoint)
        if getattr(args, "sam_hq_checkpoint", None)
        else str(SAM_HQ_VIT_H_CHECKPOINT)
    )

    model = _init_grounding_dino_with_friendly_error(config_path, grounded_checkpoint, args.device)

    if args.use_sam_hq:
        predictor = SamPredictor(sam_hq_model_registry[args.sam_version](checkpoint=sam_hq_checkpoint).to(args.device))
    else:
        predictor = SamPredictor(sam_model_registry[args.sam_version](checkpoint=sam_checkpoint).to(args.device))

    image_paths = build_image_list(args.input_image, args.input_dir)
    image_paths = filter_image_list_by_frame_range(image_paths, args.frame_start, args.frame_end)
    if not image_paths:
        raise RuntimeError("No images found.")

    batch_mode = args.input_dir is not None
    tracker = (
        SORTTracker(
            frame_rate=float(args.video_fps),
            track_activation_threshold=0.0,
            minimum_consecutive_frames=1,
        )
        if (batch_mode and SORTTracker is not None)
        else None
    )
    seen_track_ids = set()
    for image_path in image_paths:
        process_one_image(
            model=model,
            image_path=image_path,
            annotated_output_dir=annotated_dir,
            text_prompt=args.text_prompt,
            box_threshold=args.box_threshold,
            predictor=predictor,
            device=args.device,
            tracker=tracker,
            first_match_output_dir=str(first_match_dir) if first_match_dir else None,
            seen_track_ids=seen_track_ids,
            transform_matrix=args.transform_matrix,
        )
        torch = _torch()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    if video_output_path is not None and first_match_dir is not None:
        annotated_video_paths = collect_annotated_frames(annotated_dir)
        ensure_dir(video_output_path.parent)
        create_video_from_images(annotated_video_paths, video_output_path, args.video_fps)

    print("==== grounded_sam_mmdet_batch 完成 ====")
    print(f"video_id         : {getattr(args, 'video_id', '') or '-'}")
    print(f"config           : {config_path}")
    print(f"checkpoint       : {grounded_checkpoint}")
    print(f"sam              : {args.sam_version} ({sam_hq_checkpoint if args.use_sam_hq else sam_checkpoint})")
    print(f"annotated_dir    : {_log_path(annotated_dir)}")
    if first_match_dir is not None:
        print(f"first_match_dir  : {_log_path(first_match_dir)}")
    if video_output_path is not None:
        print(f"video_output     : {_log_path(video_output_path)}")
    print(f"frame_count      : {len(image_paths)}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Grounded-SAM 批量推理；输入矫正后帧图，输出 12 位命名的标注图/单鱼截图/视频"
    )

    parser.add_argument("--video-id", default=None,
                        help="视频 ID；传入时按 data/jobs/<video-id>/ 自动解析所有输出目录")
    parser.add_argument("--input-dir", "--input_dir", dest="input_dir", default=None,
                        help="矫正后帧图目录；省略时使用 data/jobs/<video-id>/frames/adjusted")
    parser.add_argument("--input-image", "--input_image", dest="input_image", default=None,
                        help="可选：单张图片模式")
    parser.add_argument("--output-dir", "--output_dir", dest="output_dir", default=None,
                        help="标注图输出目录；省略时使用 data/jobs/<video-id>/crop_images/annotated_frames")
    parser.add_argument("--first-match-output-dir", "--first_match_output_dir",
                        dest="first_match_output_dir", default=None,
                        help="first_match 输出目录；省略时使用 data/jobs/<video-id>/crop_images/first_match")
    parser.add_argument("--video-output", "--video_output", dest="video_output", default=None,
                        help="标注视频输出；省略时使用 data/jobs/<video-id>/videos/grounded_sam.mp4")
    parser.add_argument("--video-fps", "--video_fps", dest="video_fps", type=int, default=25,
                        help="输出视频的 FPS，默认 25")

    parser.add_argument(
        "--config",
        "--grounded-config",
        dest="config",
        type=str,
        default=str(GROUNDING_DINO_CONFIG_PATH),
        help=(
            "GroundingDINO 配置 .py 路径；支持绝对路径或相对项目根。"
            "默认 = algorithms/algorithms/gaoyuxi/grounding_dino_swin-t_finetune_8xb4_50e_fish.py"
        ),
    )
    parser.add_argument(
        "--grounded-checkpoint",
        "--grounded_checkpoint",
        dest="grounded_checkpoint",
        type=str,
        default=str(MM_DETECTION_CHECKPOINT),
        help=(
            "GroundingDINO 权重 .pth 路径；支持绝对路径或相对项目根。"
            "默认 = algorithms/pth/best_coco_bbox_mAP_epoch_12.pth。"
            "必须与 --config 来自同一训练架构（Swin-T OGC）。"
        ),
    )
    parser.add_argument(
        "--sam-version",
        "--sam_version",
        dest="sam_version",
        type=str,
        default="vit_h",
        help="SAM ViT 版本：vit_b / vit_l / vit_h，默认 vit_h",
    )
    parser.add_argument(
        "--sam-checkpoint",
        "--sam_checkpoint",
        dest="sam_checkpoint",
        type=str,
        default=str(SAM_VIT_H_CHECKPOINT),
        help="SAM 权重路径；默认 algorithms/pth/sam_vit_h_4b8939.pth",
    )
    parser.add_argument(
        "--sam-hq-checkpoint",
        "--sam_hq_checkpoint",
        dest="sam_hq_checkpoint",
        type=str,
        default=str(SAM_HQ_VIT_H_CHECKPOINT),
        help="SAM-HQ 权重路径；默认 algorithms/pth/sam_hq_vit_h.pth",
    )
    parser.add_argument(
        "--use-sam-hq",
        "--use_sam_hq",
        dest="use_sam_hq",
        action="store_true",
        help="使用 SAM-HQ 而非基础 SAM（默认使用基础 SAM）",
    )
    parser.add_argument(
        "--text-prompt",
        "--text_prompt",
        dest="text_prompt",
        type=str,
        default=DEFAULT_TEXT_PROMPT,
    )
    parser.add_argument(
        "--box-threshold",
        "--box_threshold",
        dest="box_threshold",
        type=float,
        default=0.3,
    )
    parser.add_argument(
        "--text-threshold",
        "--text_threshold",
        dest="text_threshold",
        type=float,
        default=0.25,
    )
    parser.add_argument(
        "--device",
        type=str,
        default="cuda",
        help="推理设备，默认 cuda",
    )
    parser.add_argument(
        "--frame-start",
        "--frame_start",
        dest="frame_start",
        type=int,
        default=0,
    )
    parser.add_argument(
        "--frame-end",
        "--frame_end",
        dest="frame_end",
        type=int,
        default=10 ** 6,
    )
    parser.add_argument(
        "--transform-matrix",
        "--transform_matrix",
        dest="transform_matrix",
        type=str,
        default=None,
        help="可选的 3x3 透视变换矩阵 JSON 路径",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(list(argv) if argv is not None else None)
    # 注意：``args.transform_matrix`` 历史上曾被自动注入默认值，
    # 当前 pipeline 输入已经是 adjust batch 矫正后的 adjusted 帧，
    # 不再对输入做任何透视变换，因此这里不再注入默认值。
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())