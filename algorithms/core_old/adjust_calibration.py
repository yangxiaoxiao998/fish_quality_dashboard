"""单次相机标定（生成透视矩阵）。

只做一件事：给定一张标定图片，计算传送带透视变换矩阵并写入
``data/calibration/current_calibration.json``。

软件系统对接时，这是「用户点击标定按钮」背后的接口。

运行示例：

.. code-block:: bash

    python -m algorithms.core.adjust_calibration \\
        --source-image /abs/path/calib.png \\
        --camera-id default \\
        --device cuda \\
        --overwrite

输出：

- ``data/calibration/current_calibration.json``
- ``data/calibration/sessions/<calibration_id>/`` 下的 source.jpg / belt_mask.png /
  adjusted_preview.jpg / calibration.json（用于历史追溯；**不**写入 results/）。
- 任何中间临时图片只在 ``data/calibration/tmp/`` 下短暂存在，标定完成后清理。

本模块**不**做批量帧矫正。批量矫正见 ``algorithms.core.adjust``。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
ALGORITHMS_ROOT = THIS_DIR.parent
if str(ALGORITHMS_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS_ROOT))

from config.cleanup import remove_dir  # noqa: E402
from config.paths import (  # noqa: E402
    ALGORITHMS_GAOYUXI_DIR,
    BERT_BASE_UNCASED_DIR,
    CALIBRATION_SESSIONS_DIR,
    CALIBRATION_TMP_DIR,
    GROUNDED_SAM_DIR,
    GROUNDING_DINO_DIR,
    MODEL_DIR,
    SAM_HQ_VIT_H_CHECKPOINT,
    SAM_VIT_H_CHECKPOINT,
    SEGMENT_ANYTHING_DIR,
    ensure_dir,
    to_project_relative,
)


def _prepend_runtime_paths(paths: list[Path]) -> None:
    for path in reversed([str(p) for p in paths]):
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)


def _resolve_checkpoint(env_var: str, default_path: Path) -> Path:
    """从环境变量或默认路径解析权重位置，并返回绝对路径。"""
    raw = os.environ.get(env_var)
    if raw:
        return Path(raw).expanduser().resolve()
    return default_path


def _configure_adjust_runtime(adjust_module, device: str, use_sam_hq: bool = False) -> None:
    """让标定入口使用已编译的 GroundingDINO 与本地 BERT/SAM 权重。

    重要：本函数禁止再使用 ``ALGORITHMS_GAOYUXI_DIR / "pth"``。
    历史上 ``algorithms/algorithms/gaoyuxi/pth`` 路径下 sam_hq_vit_h.pth 是循环
    软链，会导致 FileNotFoundError。所有权重必须从 ``algorithms/pth``（即
    ``MODEL_DIR``）读取，并支持环境变量覆盖。
    """

    runtime_paths = [
        GROUNDING_DINO_DIR,
        SEGMENT_ANYTHING_DIR,
        ALGORITHMS_GAOYUXI_DIR,
        GROUNDED_SAM_DIR,
        ALGORITHMS_ROOT,
    ]

    def _bootstrap_dependencies() -> None:
        import torch  # noqa: F401  先加载 torch 动态库，保证 groundingdino._C 可导入

        _prepend_runtime_paths(runtime_paths)

    def _load_model(model_config_path, model_checkpoint_path, bert_base_uncased_path, model_device):
        _ = bert_base_uncased_path
        _, build_model, SLConfig, clean_state_dict, _, _, _ = adjust_module._ensure_dependencies()
        args = SLConfig.fromfile(str(model_config_path))
        args.device = model_device
        args.text_encoder_type = str(BERT_BASE_UNCASED_DIR)
        args.bert_base_uncased_path = str(BERT_BASE_UNCASED_DIR)
        model = build_model(args)
        checkpoint = adjust_module.torch_load(model_checkpoint_path)
        model.load_state_dict(clean_state_dict(checkpoint["model"]), strict=False)
        model.eval()
        return model

    adjust_module._bootstrap_dependencies = _bootstrap_dependencies
    adjust_module.load_model = _load_model
    adjust_module.GROUNDING_DINO_CONFIG_PATH = (
        GROUNDING_DINO_DIR / "groundingdino" / "config" / "GroundingDINO_SwinT_OGC.py"
    )
    # 统一从 algorithms/pth 解析所有权重，并支持环境变量覆盖
    adjust_module.GROUNDING_DINO_CHECKPOINT = _resolve_checkpoint(
        "FISH_GROUNDING_DINO_CHECKPOINT",
        MODEL_DIR / "groundingdino_swint_ogc.pth",
    )
    adjust_module.SAM_VIT_H_CHECKPOINT = _resolve_checkpoint(
        "FISH_SAM_CHECKPOINT", SAM_VIT_H_CHECKPOINT
    )
    adjust_module.SAM_HQ_VIT_H_CHECKPOINT = _resolve_checkpoint(
        "FISH_SAM_HQ_CHECKPOINT", SAM_HQ_VIT_H_CHECKPOINT
    )
    adjust_module.BERT_BASE_UNCASED_DIR = BERT_BASE_UNCASED_DIR
    adjust_module.DEVICE = device
    adjust_module.USE_SAM_HQ = bool(use_sam_hq)


def _cleanup_calibration_tmp() -> None:
    """清空 ``data/calibration/tmp/`` 中本次标定产生的临时图片。"""

    if CALIBRATION_TMP_DIR.exists():
        remove_dir(CALIBRATION_TMP_DIR)
        print(f"[cleanup] 已清理标定临时目录：{to_project_relative(CALIBRATION_TMP_DIR)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="algorithms.core.adjust_calibration",
        description=(
            "单次相机标定：给定一张标定图片，计算透视矩阵并写入 "
            "data/calibration/current_calibration.json。"
        ),
    )
    parser.add_argument(
        "--source-image",
        required=True,
        help="标定图片路径，支持绝对路径或相对项目根的相对路径。原图不会被修改或移动。",
    )
    parser.add_argument(
        "--camera-id",
        default="default",
        help="逻辑摄像头 ID，默认 default",
    )
    parser.add_argument(
        "--calibration-id",
        default=None,
        help="可选：手动指定 calibration_id；省略时自动生成 calib_YYYYMMDD_HHMMSS_<short>",
    )
    parser.add_argument(
        "--real-length-mm",
        type=float,
        default=1376.0,
        help="传送带长边真实长度（毫米），用于估算 mm_per_pixel",
    )
    parser.add_argument(
        "--note",
        default="",
        help="任意注释，会写入 calibration.json",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="允许覆盖已有 current_calibration.json",
    )
    parser.add_argument(
        "--device",
        default="cuda",
        help="推理设备，默认 cuda",
    )
    parser.add_argument(
        "--use-sam-hq",
        action="store_true",
        help="启用 SAM-HQ 模式（默认关闭，使用普通 SAM）。仅当 algorithms/pth/sam_hq_vit_h.pth 存在时才应启用。",
    )
    parser.add_argument(
        "--sam-checkpoint",
        default=None,
        help="普通 SAM 权重路径；默认从环境变量 FISH_SAM_CHECKPOINT 或 algorithms/pth/sam_vit_h_4b8939.pth 解析",
    )
    parser.add_argument(
        "--sam-hq-checkpoint",
        default=None,
        help="SAM-HQ 权重路径；默认从环境变量 FISH_SAM_HQ_CHECKPOINT 或 algorithms/pth/sam_hq_vit_h.pth 解析（仅当 --use-sam-hq 时才会检查存在性）",
    )
    return parser


def _resolve_checkpoint_from_args_or_env(args, sam_default: Path, hq_default: Path) -> tuple[Path, Path]:
    """命令行参数 > 环境变量 > config 默认路径。"""
    sam_ckpt = (
        Path(args.sam_checkpoint).expanduser().resolve()
        if args.sam_checkpoint
        else _resolve_checkpoint("FISH_SAM_CHECKPOINT", sam_default)
    )
    hq_ckpt = (
        Path(args.sam_hq_checkpoint).expanduser().resolve()
        if args.sam_hq_checkpoint
        else _resolve_checkpoint("FISH_SAM_HQ_CHECKPOINT", hq_default)
    )
    return sam_ckpt, hq_ckpt


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not Path(args.source_image).expanduser().resolve().is_file():
        print(f"[ERROR] 标定图片不存在：{args.source_image}")
        return 2

    import core.adjust as adjust_core  # noqa: E402  避免循环引用

    # 先解析权重路径，再传给子模块。
    sam_ckpt, hq_ckpt = _resolve_checkpoint_from_args_or_env(
        args, SAM_VIT_H_CHECKPOINT, SAM_HQ_VIT_H_CHECKPOINT
    )
    print(
        f"[adjust_calibration] SAM   = {sam_ckpt} "
        f"({'OK' if sam_ckpt.is_file() else 'MISSING'})"
    )
    print(
        f"[adjust_calibration] SAM-HQ = {hq_ckpt} "
        f"({'OK' if hq_ckpt.is_file() else 'MISSING'}) "
        f"use_sam_hq={args.use_sam_hq}"
    )

    if not sam_ckpt.is_file():
        print(
            "[ERROR] 标定失败：SAM 权重文件不存在。\n"
            f"  当前模式：普通 SAM (use_sam_hq={args.use_sam_hq})\n"
            f"  尝试路径：{sam_ckpt}\n"
            "  建议：\n"
            "    1) 确认 algorithms/pth/sam_vit_h_4b8939.pth 存在；\n"
            "    2) 或通过环境变量 FISH_SAM_CHECKPOINT 指向正确位置；\n"
            "    3) 或通过 --sam-checkpoint 显式传入。"
        )
        return 2

    if args.use_sam_hq and not hq_ckpt.is_file():
        print(
            "[ERROR] 标定失败：SAM-HQ 权重文件不存在。\n"
            f"  当前模式：SAM-HQ (use_sam_hq=True)\n"
            f"  尝试路径：{hq_ckpt}\n"
            "  建议：\n"
            "    1) 放置 sam_hq_vit_h.pth 到 algorithms/pth/；\n"
            "    2) 或通过环境变量 FISH_SAM_HQ_CHECKPOINT 指向正确位置；\n"
            "    3) 或移除 --use-sam-hq 改用普通 SAM。"
        )
        return 2

    _configure_adjust_runtime(adjust_core, args.device, use_sam_hq=args.use_sam_hq)

    if adjust_core.CALIBRATION_CURRENT_JSON.is_file() and not args.overwrite:
        print(
            f"[ERROR] {adjust_core.CALIBRATION_CURRENT_JSON} 已存在。"
            "如需覆盖请加 --overwrite。"
        )
        return 2

    # 强制打开日志前置：清理旧 tmp
    _cleanup_calibration_tmp()

    try:
        result = adjust_core.run_calibration(
            source_image=args.source_image,
            camera_id=args.camera_id,
            calibration_id=args.calibration_id,
            real_length_mm=args.real_length_mm,
            note=args.note,
        )
    except Exception as exc:
        print(
            f"[ERROR] 标定失败：{type(exc).__name__}: {exc}\n"
            f"  SAM 权重：{sam_ckpt}\n"
            f"  SAM-HQ 权重：{hq_ckpt}\n"
            f"  use_sam_hq={args.use_sam_hq}"
        )
        return 1
    finally:
        # 无论成功失败，都清空 tmp
        _cleanup_calibration_tmp()

    print("==== 标定完成 ====")
    print(f"calibration_id     = {result.calibration_id}")
    print(f"current_calibration = {to_project_relative(adjust_core.CALIBRATION_CURRENT_JSON)}")
    print(
        "calibration session = "
        f"data/calibration/sessions/{result.calibration_id}"
    )
    return 0


__all__ = [
    "build_parser",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
