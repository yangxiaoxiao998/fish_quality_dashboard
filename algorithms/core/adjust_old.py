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
    透视变换矩阵矫正，写入 ``--output-dir``，输出全部使用 12 位命名。
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
        str(GROUNDED_SAM_DIR),
    ]:
        if path not in sys.path:
            sys.path.insert(0, path)


def _ensure_dependencies() -> tuple:
    _bootstrap_dependencies()
    try:
        import GroundingDINO.groundingdino.datasets.transforms as T  # type: ignore  # noqa: E402
        from GroundingDINO.groundingdino.models import build_model  # type: ignore  # noqa: E402
        from GroundingDINO.groundingdino.util.slconfig import SLConfig  # type: ignore  # noqa: E402
        from GroundingDINO.groundingdino.util.utils import clean_state_dict  # type: ignore  # noqa: E402
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


def rectify_conveyor_belt(image_bgr, mask_np):
    contours, _ = _cv2().findContours(
        mask_np.astype(_np().uint8), _cv2().RETR_EXTERNAL, _cv2().CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return None, 0, 0, None, None, None
    c = max(contours, key=_cv2().contourArea)
    epsilon = 0.02 * _cv2().arcLength(c, True)
    approx = _cv2().approxPolyDP(c, epsilon, True)
    if len(approx) == 4:
        pts = approx.reshape(4, 2)
    else:
        rect = _cv2().minAreaRect(c)
        pts = _cv2().boxPoints(rect)
    rect_pts = order_points(pts)
    src_pts = _np().array(rect_pts, dtype="float32")
    (tl, tr, br, bl) = rect_pts
    widthA = _np().sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
    widthB = _np().sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
    maxWidth = max(int(widthA), int(widthB))
    heightA = _np().sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
    heightB = _np().sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
    maxHeight = max(int(heightA), int(heightB))
    dst = _np().array([
        [0, 0],
        [maxWidth - 1, 0],
        [maxWidth - 1, maxHeight - 1],
        [0, maxHeight - 1],
    ], dtype="float32")
    M = _cv2().getPerspectiveTransform(src_pts, dst)
    M_inv = _cv2().getPerspectiveTransform(dst, src_pts)
    warped_img = _cv2().warpPerspective(image_bgr, M, (maxWidth, maxHeight))
    return warped_img, maxHeight, maxWidth, M, src_pts, dst


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

    def to_dict(self) -> dict:
        return {
            "calibration_id": self.calibration_id,
            "camera_id": self.camera_id,
            "created_at": self.created_at,
            "image_width": int(self.image_width),
            "image_height": int(self.image_height),
            "transform_matrix": _np().asarray(self.transform_matrix, dtype=float).tolist(),
            "inverse_transform_matrix": _np().asarray(self.inverse_transform_matrix, dtype=float).tolist(),
            "source_points": _np().asarray(self.source_points, dtype=float).tolist(),
            "target_points": _np().asarray(self.target_points, dtype=float).tolist(),
            "vertical_distance": None if self.vertical_distance is None else int(self.vertical_distance),
            "pixel_length": None if self.pixel_length is None else int(self.pixel_length),
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
) -> CalibrationResult:
    """读取 ``source_image``，执行标定，并把会话写入 sessions 目录。

    - ``source_image`` 可以是绝对路径或相对于项目根的相对路径。
    - 原始图片**不会被删除或移动**；本函数只在 ``data/calibration/sessions/<id>/``
      下生成 ``source.jpg`` 副本，便于隔离历史会话。
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
    (_, build_model, SLConfig, clean_state_dict,
     SamPredictor, sam_hq_model_registry, sam_model_registry) = _ensure_dependencies()
    model = load_model(
        GROUNDING_DINO_CONFIG_PATH,
        GROUNDING_DINO_CHECKPOINT,
        BERT_BASE_UNCASED_DIR,
        DEVICE,
    )
    if USE_SAM_HQ and sam_hq_model_registry is not None:
        predictor = SamPredictor(
            sam_hq_model_registry[SAM_VERSION](checkpoint=str(SAM_HQ_VIT_H_CHECKPOINT)).to(DEVICE)
        )
    else:
        predictor = SamPredictor(
            sam_model_registry[SAM_VERSION](checkpoint=str(SAM_VIT_H_CHECKPOINT)).to(DEVICE)
        )

    image_pil, image_tensor = load_image(session_source)
    boxes_filt = get_boxes_only(model, image_tensor, TEXT_PROMPT, BOX_THRESHOLD, DEVICE)
    if len(boxes_filt) == 0:
        raise RuntimeError("标定失败：未在图片中检测到传送带，请重新拍摄或更换角度。")

    image_rgb = _cv2().cvtColor(image_bgr, _cv2().COLOR_BGR2RGB)
    predictor.set_image(image_rgb)

    H, W = image_pil.size[1], image_pil.size[0]
    boxes_filt = boxes_filt.clone()
    for i in range(boxes_filt.size(0)):
        boxes_filt[i] = boxes_filt[i] * _torch_Tensor([W, H, W, H])
        boxes_filt[i][:2] -= boxes_filt[i][2:] / 2
        boxes_filt[i][2:] += boxes_filt[i][:2]

    transformed_boxes = predictor.transform.apply_boxes_torch(
        boxes_filt.cpu(), image_rgb.shape[:2]
    ).to(DEVICE)
    masks, _, _ = predictor.predict_torch(
        point_coords=None,
        point_labels=None,
        boxes=transformed_boxes,
        multimask_output=False,
    )
    best_mask = masks[0][0].cpu().numpy().astype(_np().uint8) * 255

    warped_belt, vertical_distance, pixel_length, transform_matrix, source_points, target_points = rectify_conveyor_belt(
        image_bgr, best_mask
    )
    if warped_belt is None:
        raise RuntimeError("标定失败：无法从掩码中提取传送带多边形，请重新拍摄。")
    inverse_matrix = _cv2().getPerspectiveTransform(
        _np().asarray(target_points, dtype="float32"),
        _np().asarray(source_points, dtype="float32"),
    )

    belt_mask_path = session_dir / "belt_mask.png"
    preview_path = session_dir / "adjusted_preview.jpg"
    _cv2().imwrite(str(belt_mask_path), best_mask)
    _cv2().imwrite(str(preview_path), warped_belt)

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
    )

    # 写 calibration.json 与 current_calibration.json
    ensure_dir(CALIBRATION_DIR)
    calibration_payload = result.to_dict()
    calibration_payload["mm_per_pixel"] = _mm_per_pixel(vertical_distance, pixel_length, real_length_mm)
    calibration_json_path = session_dir / "calibration.json"
    with open(calibration_json_path, "w", encoding="utf-8") as fp:
        json.dump(calibration_payload, fp, ensure_ascii=False, indent=2)

    current_payload = {
        **calibration_payload,
        "source_image": calibration_payload["source_image"],
        "belt_mask": calibration_payload["belt_mask"],
        "adjusted_preview": calibration_payload["adjusted_preview"],
        "calibration_json": to_project_relative(calibration_json_path),
    }
    ensure_dir(CALIBRATION_CURRENT_JSON.parent)
    with open(CALIBRATION_CURRENT_JSON, "w", encoding="utf-8") as fp:
        json.dump(current_payload, fp, ensure_ascii=False, indent=2)

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
    return _cv2().warpPerspective(image_bgr, transform_matrix, output_size)


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
    M = _np().array(payload["transform_matrix"], dtype=_np().float32)
    output_size = (int(payload["pixel_length"]), int(payload["vertical_distance"]))

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
