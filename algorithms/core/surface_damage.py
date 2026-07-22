"""wound 体表破损率计算。

从历史脚本 ``docs/reference/wound_original.py`` 迁移而来，但做了完整工程化：

1. 删除 ``/home/data/...`` 之类硬编码；
2. 删除 ``OUTPUT_SAVE_DIR`` 默认保存路径；本模块**不**保存任何 wound 图像；
3. 默认不下载字体；
4. ``process_single_picture`` 改名 ``analyze_surface_damage``，返回数值 dict；
5. 模型只 ``load_wound_model(...)`` 加载一次，多条鱼共用；
6. 路径解析支持 CLI 参数 + ``FISH_WOUND_CHECKPOINT`` 环境变量 + 仓库默认路径；
7. 不返回任何 ``wound_mask_path`` / ``wound_visual_path``；不写 wound 图片。

wound 字典字段：

- ``surface_damage_ratio``: 伤口像素 / 鱼体像素，0..1；
- ``surface_damage_percent``: 上述比例 * 100；
- ``wound_area_px``: 伤口像素（鱼体 mask 内）；
- ``fish_area_px``: 鱼体 mask 像素；
- ``status``: ``"done"`` / ``"failed"``；
- ``error_message``: 失败时填写。

无伤口鱼返回 ``surface_damage_ratio=0.0``。

鱼体 mask 像素为 0 时返回 ``status="failed"`` 并把数值字段写成 ``None``。
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import torch
from PIL import Image, ImageOps
from torchvision.models.detection import maskrcnn_resnet50_fpn_v2
from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
from torchvision.models.detection.mask_rcnn import MaskRCNNPredictor
from torchvision.transforms import functional as TF

THIS_DIR = Path(__file__).resolve().parent
ALGORITHMS_ROOT = THIS_DIR.parent
if str(ALGORITHMS_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS_ROOT))

from config.paths import MODEL_DIR, ensure_dir  # noqa: E402

# 把 torch.nn.functional as F 这类重命名放进局部，便于和原 wound.py 命名习惯保持一致。
import torch.nn.functional as F  # noqa: E402


CLASS_NAMES = ["background", "wound"]
TRAIN_SZ = 512
DEFAULT_WOUND_CHECKPOINT_REL = "maskrcnn_resnet50_fpn_v2-wound.pth"


# ---------------------------------------------------------------------------
# 默认权重路径解析
# ---------------------------------------------------------------------------


def resolve_wound_checkpoint(
    cli_path: str | Path | None = None,
    *,
    env_var: str = "FISH_WOUND_CHECKPOINT",
) -> Path | None:
    """按顺序解析 wound 模型权重路径：

    1. CLI 显式传入的 ``cli_path``；
    2. 环境变量 ``FISH_WOUND_CHECKPOINT``；
    3. ``algorithms/pth/maskrcnn_resnet50_fpn_v2-wound.pth`` 默认路径。

    返回 ``Path`` 或 ``None``（完全找不到）；找不到时调用方应报清晰错误。
    """

    if cli_path:
        p = Path(cli_path).expanduser()
        if p.is_file():
            return p
    env_path = os.environ.get(env_var, "").strip()
    if env_path:
        p = Path(env_path).expanduser()
        if p.is_file():
            return p
    default = MODEL_DIR / DEFAULT_WOUND_CHECKPOINT_REL
    if default.is_file():
        return default
    return None


# ---------------------------------------------------------------------------
# 模型构建 / 加载
# ---------------------------------------------------------------------------


def _build_wound_model(num_classes: int = len(CLASS_NAMES)) -> torch.nn.Module:
    """构造 Mask R-CNN ResNet50-FPN v2 backbone，并把 box_predictor / mask_predictor 替换成 2 类。"""

    model = maskrcnn_resnet50_fpn_v2(weights=None)
    in_features_box = model.roi_heads.box_predictor.cls_score.in_features
    in_features_mask = model.roi_heads.mask_predictor.conv5_mask.in_channels
    dim_reduced = model.roi_heads.mask_predictor.conv5_mask.out_channels
    model.roi_heads.box_predictor = FastRCNNPredictor(
        in_channels=in_features_box, num_classes=num_classes
    )
    model.roi_heads.mask_predictor = MaskRCNNPredictor(
        in_channels=in_features_mask, dim_reduced=dim_reduced, num_classes=num_classes
    )
    return model


def load_wound_model(
    checkpoint_path: str | Path | None = None,
    device: str | torch.device = "cuda",
) -> torch.nn.Module:
    """加载 wound 模型。优先 CLI 显式路径，其次 ``FISH_WOUND_CHECKPOINT``，再次 ``algorithms/pth/`` 默认。

    找不到权重时抛 ``FileNotFoundError`` 并提示三种补救方式。
    """

    if isinstance(device, str):
        device = torch.device(device)
    ckpt = resolve_wound_checkpoint(checkpoint_path)
    if ckpt is None:
        raise FileNotFoundError(
            "找不到 wound 模型权重，请设置环境变量 FISH_WOUND_CHECKPOINT，"
            "或传入 --wound-checkpoint，或将权重放到 "
            f"{MODEL_DIR / DEFAULT_WOUND_CHECKPOINT_REL}"
        )
    model = _build_wound_model()
    state_dict = torch.load(str(ckpt), map_location=device)
    model.load_state_dict(state_dict)
    model.to(device)
    model.eval()
    return model


# ---------------------------------------------------------------------------
# 单条鱼分析
# ---------------------------------------------------------------------------


def _read_mask_area(mask_path: str | Path) -> tuple[np.ndarray | None, int, str | None]:
    """读取鱼体 mask，返回 ``(bool mask, fish_area_px, error_or_None)``。"""

    try:
        with Image.open(str(mask_path)) as im:
            mask_pil = im.convert("L")
        fish_mask_np = np.asarray(mask_pil) > 127
    except Exception as exc:
        return None, 0, f"无法读取 mask: {exc}"
    return fish_mask_np, int(np.sum(fish_mask_np)), None


def _resize_letterbox(img: Image.Image, target_sz: int) -> Image.Image:
    """按比例缩放，最长边 = target_sz。"""

    w, h = img.size
    if max(w, h) <= 0:
        return img
    scale = target_sz / max(w, h)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resample = Image.Resampling.BILINEAR if hasattr(Image, "Resampling") else Image.BILINEAR
    return img.resize((new_w, new_h), resample)


@torch.no_grad()
def analyze_surface_damage(
    image_path: str | Path,
    mask_path: str | Path,
    model: torch.nn.Module,
    device: str | torch.device = "cuda",
    threshold: float = 0.5,
) -> dict[str, Any]:
    """对单张鱼的 crop 图 + mask 计算体表破损数值。

    返回 dict 字段：

    - ``surface_damage_ratio``：0..1
    - ``surface_damage_percent``：0..100
    - ``wound_area_px``：整数
    - ``fish_area_px``：整数
    - ``status``：``"done"`` / ``"failed"``
    - ``error_message``：失败原因

    没有任何图像被保存。
    """

    if isinstance(device, str):
        device = torch.device(device)

    fish_mask_np, fish_area_px, mask_err = _read_mask_area(mask_path)
    if mask_err is not None or fish_mask_np is None:
        return {
            "surface_damage_ratio": None,
            "surface_damage_percent": None,
            "wound_area_px": None,
            "fish_area_px": None,
            "status": "failed",
            "error_message": mask_err or "mask read failed",
        }
    if fish_area_px <= 0:
        return {
            "surface_damage_ratio": None,
            "surface_damage_percent": None,
            "wound_area_px": None,
            "fish_area_px": fish_area_px,
            "status": "failed",
            "error_message": "fish mask area is zero; cannot compute surface_damage",
        }

    try:
        with Image.open(str(image_path)) as im:
            img = ImageOps.exif_transpose(im).convert("RGB")
    except Exception as exc:
        return {
            "surface_damage_ratio": None,
            "surface_damage_percent": None,
            "wound_area_px": None,
            "fish_area_px": fish_area_px,
            "status": "failed",
            "error_message": f"无法读取 image: {exc}",
        }

    orig_w, orig_h = img.size
    if orig_w <= 0 or orig_h <= 0:
        return {
            "surface_damage_ratio": None,
            "surface_damage_percent": None,
            "wound_area_px": None,
            "fish_area_px": fish_area_px,
            "status": "failed",
            "error_message": "image is empty",
        }

    min_img_scale = min(orig_w, orig_h) / float(TRAIN_SZ)
    input_img = _resize_letterbox(img, target_sz=TRAIN_SZ)
    input_tensor = TF.to_tensor(input_img)[None].to(device)

    try:
        output = model(input_tensor)
    except Exception as exc:
        return {
            "surface_damage_ratio": None,
            "surface_damage_percent": None,
            "wound_area_px": None,
            "fish_area_px": fish_area_px,
            "status": "failed",
            "error_message": f"模型推理失败: {exc}",
        }

    out0 = {k: (v.detach().cpu() if hasattr(v, "detach") else v) for k, v in output[0].items()}
    keep = out0["scores"] >= threshold

    total_wound_area = 0
    if keep.any():
        raw_masks = out0["masks"][keep]
        pred_masks_full = F.interpolate(
            raw_masks, size=(orig_h, orig_w), mode="bilinear", align_corners=False
        ).squeeze(1)
        pred_masks_full = (pred_masks_full > 0.5).bool()
        pred_labels = out0["labels"][keep]
        for i in range(len(pred_labels)):
            label_idx = int(pred_labels[i])
            if CLASS_NAMES[label_idx] != "wound":
                continue
            wound_mask_np = pred_masks_full[i].numpy()
            valid_wound_mask_np = np.logical_and(wound_mask_np, fish_mask_np)
            valid_wound_area = int(np.sum(valid_wound_mask_np))
            if valid_wound_area > 0:
                total_wound_area += valid_wound_area

    surface_damage_ratio = total_wound_area / fish_area_px
    surface_damage_percent = surface_damage_ratio * 100.0

    return {
        "surface_damage_ratio": round(float(surface_damage_ratio), 6),
        "surface_damage_percent": round(float(surface_damage_percent), 4),
        "wound_area_px": int(total_wound_area),
        "fish_area_px": int(fish_area_px),
        "status": "done",
        "error_message": "",
    }


# ---------------------------------------------------------------------------
# CLI（仅 --help / 探测，不在这里跑批量推理）
# ---------------------------------------------------------------------------


def build_parser() -> "argparse.ArgumentParser":  # type: ignore[name-defined]
    import argparse

    parser = argparse.ArgumentParser(
        prog="algorithms.core.surface_damage",
        description="wound 体表破损率模块：模型加载 + 单条鱼分析。",
    )
    parser.add_argument(
        "--wound-checkpoint",
        default=None,
        help="wound 模型权重 .pth；不传则按 FISH_WOUND_CHECKPOINT → algorithms/pth/maskrcnn_resnet50_fpn_v2-wound.pth 顺序回退。",
    )
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--threshold", type=float, default=0.5)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        model = load_wound_model(args.wound_checkpoint, device=args.device)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        return 2
    print(f"wound model loaded on {args.device} (threshold={args.threshold}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
