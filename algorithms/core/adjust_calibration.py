"""单次相机标定（生成透视矩阵）。

本入口用于从一张标定图片生成传送带透视矩阵，并写入：

- ``data/calibration/current_calibration.json``
- ``data/calibration/sessions/<calibration_id>/calibration.json``

与旧版“完整保留原始相机画面”的画布策略不同，本版本默认采用：

    传送带矫正矩形 + 左右安全边界 + 少量上下安全边界

这样既不会把输出紧裁到传送带边缘，又不会为了保留整幅原图而产生很大的
无效黑色三角区域、使鱼在后续模型输入中被过度缩小。

默认：

- 左右各保留传送带矫正宽度的 25%；
- 上下各保留传送带矫正高度的 5%。

安全边界不从命令行传入，统一从
``algorithms/config/adjust_calibration_margin.json`` 读取：

.. code-block:: json

    {
      "side_margin_ratio": 0.25,
      "vertical_margin_ratio": 0.05
    }

如需调整安全边界，请直接修改该 JSON 文件。

示例：

.. code-block:: bash

    python -m algorithms.core.adjust_calibration \\
        --source-image /abs/path/calib.jpg \\
        --real-length-mm 1360 \\
        --device cuda \\
        --overwrite

注意：本模块只负责“标定”。批量帧矫正仍由 ``algorithms.core.adjust`` 完成。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


THIS_DIR = Path(__file__).resolve().parent
ALGORITHMS_ROOT = THIS_DIR.parent
if str(ALGORITHMS_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS_ROOT))


from config.cleanup import remove_dir  # noqa: E402
from config.paths import (  # noqa: E402
    ADJUST_CALIBRATION_MARGIN_CONFIG_PATH,
    BERT_BASE_UNCASED_DIR,
    CALIBRATION_TMP_DIR,
    GROUNDED_SAM_DIR,
    GROUNDING_DINO_DIR,
    MODEL_DIR,
    SAM_HQ_VIT_H_CHECKPOINT,
    SAM_VIT_H_CHECKPOINT,
    SEGMENT_ANYTHING_DIR,
    resolve_input_path,
    to_project_relative,
)


# ---------------------------------------------------------------------------
# 运行时依赖 / 权重配置
# ---------------------------------------------------------------------------


def _prepend_runtime_paths(paths: list[Path]) -> None:
    """把运行时依赖路径放到 ``sys.path`` 最前面。"""

    for path in reversed([str(p) for p in paths]):
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)


def _resolve_checkpoint(env_var: str, default_path: Path) -> Path:
    """环境变量 > 默认路径。"""

    raw = os.environ.get(env_var)
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(default_path).expanduser().resolve()


def _resolve_checkpoint_from_args_or_env(
    args,
    sam_default: Path,
    hq_default: Path,
) -> tuple[Path, Path]:
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


def _configure_adjust_runtime(
    adjust_module,
    device: str,
    use_sam_hq: bool,
    sam_checkpoint: Path,
    sam_hq_checkpoint: Path,
    grounding_dino_checkpoint: Path,
) -> None:
    """配置 ``algorithms.core.adjust`` 的实际运行依赖和权重。

    与旧版相比，这里有一个重要修正：命令行传入的 ``--sam-checkpoint`` /
    ``--sam-hq-checkpoint`` 会真正写入 ``adjust_module``，而不是只做存在性检查。
    """

    runtime_paths = [
        GROUNDING_DINO_DIR,
        SEGMENT_ANYTHING_DIR,
        GROUNDED_SAM_DIR,
    ]

    def _bootstrap_dependencies() -> None:
        import torch  # noqa: F401

        _prepend_runtime_paths(runtime_paths)

    def _load_model(
        model_config_path,
        model_checkpoint_path,
        bert_base_uncased_path,
        model_device,
    ):
        _ = bert_base_uncased_path
        _, build_model, SLConfig, clean_state_dict, _, _, _ = (
            adjust_module._ensure_dependencies()
        )
        args = SLConfig.fromfile(str(model_config_path))
        args.device = model_device
        # GroundingDINO's tokenizer factory dispatches on this model name;
        # the separate bert_base_uncased_path selects the local weights.
        args.text_encoder_type = "bert-base-uncased"
        args.bert_base_uncased_path = str(BERT_BASE_UNCASED_DIR)
        model = build_model(args)
        checkpoint = adjust_module.torch_load(model_checkpoint_path)
        model.load_state_dict(
            clean_state_dict(checkpoint["model"]),
            strict=False,
        )
        model.eval()
        return model

    adjust_module._bootstrap_dependencies = _bootstrap_dependencies
    adjust_module.load_model = _load_model

    adjust_module.GROUNDING_DINO_CONFIG_PATH = (
        GROUNDING_DINO_DIR
        / "groundingdino"
        / "config"
        / "GroundingDINO_SwinT_OGC.py"
    )
    adjust_module.GROUNDING_DINO_CHECKPOINT = grounding_dino_checkpoint
    adjust_module.SAM_VIT_H_CHECKPOINT = sam_checkpoint
    adjust_module.SAM_HQ_VIT_H_CHECKPOINT = sam_hq_checkpoint
    adjust_module.BERT_BASE_UNCASED_DIR = BERT_BASE_UNCASED_DIR
    adjust_module.DEVICE = device
    adjust_module.USE_SAM_HQ = bool(use_sam_hq)


# ---------------------------------------------------------------------------
# “传送带 + 安全边界”画布策略
# ---------------------------------------------------------------------------


def _load_canvas_margin_config(
    config_path: Path = ADJUST_CALIBRATION_MARGIN_CONFIG_PATH,
) -> dict[str, float]:
    """读取并校验标定画布的横向、纵向安全边界比例。"""

    payload = _read_json(config_path)
    required_keys = ("side_margin_ratio", "vertical_margin_ratio")
    missing_keys = [key for key in required_keys if key not in payload]
    if missing_keys:
        raise RuntimeError(
            f"安全边界配置缺少字段 {missing_keys}：{config_path}"
        )

    values: dict[str, float] = {}
    for key in required_keys:
        raw_value = payload[key]
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise RuntimeError(
                f"安全边界配置 {key} 必须是数值，当前值为 {raw_value!r}："
                f"{config_path}"
            )
        try:
            values[key] = _validate_nonnegative_ratio(float(raw_value), key)
        except ValueError as exc:
            raise RuntimeError(
                f"安全边界配置无效：{config_path}；{exc}"
            ) from exc
    return values


def _validate_nonnegative_ratio(value: float, name: str) -> float:
    value = float(value)
    if value < 0:
        raise ValueError(f"{name} 不能小于 0，当前值为 {value}")
    # 比例过大通常意味着参数误填。允许到 2.0，已经足以覆盖非常大的鱼。
    if value > 2.0:
        raise ValueError(
            f"{name}={value} 过大；这里的比例是相对于传送带矫正尺寸，"
            "建议使用 0~1 范围。"
        )
    return value


def _resolve_canvas_margins(
    belt_width: int,
    belt_height: int,
    *,
    side_margin_ratio: float,
    vertical_margin_ratio: float,
) -> tuple[int, int, int, int]:
    """根据 JSON 比例计算 ``left/right/top/bottom`` 安全区。"""

    side_margin_ratio = _validate_nonnegative_ratio(
        side_margin_ratio,
        "side_margin_ratio",
    )
    vertical_margin_ratio = _validate_nonnegative_ratio(
        vertical_margin_ratio,
        "vertical_margin_ratio",
    )

    auto_side = int(round(float(belt_width) * side_margin_ratio))
    auto_vertical = int(round(float(belt_height) * vertical_margin_ratio))
    return auto_side, auto_side, auto_vertical, auto_vertical


def _install_margin_canvas_rectifier(
    adjust_module,
    *,
    side_margin_ratio: float,
    vertical_margin_ratio: float,
):
    """在本次标定运行期间替换 ``adjust.rectify_conveyor_belt``。

    关键区别：

    旧策略：
        先把传送带拉正，再把“整幅原图四角”也投影，使用整幅原图投影后的
        外接矩形作为输出画布。因此会产生很大的画布和黑色三角区。

    新策略：
        只让传送带本体映射到标准矩形，并围绕该矩形增加可控安全区。
        左右安全区用于容纳跨越传送带边缘的完整鱼。

    返回一个 ``canvas_state`` 字典，标定完成后用于打印和写入 JSON 元数据。
    """

    canvas_state: dict[str, int | float | str] = {
        "canvas_mode": "belt_margin",
        "side_margin_ratio": float(side_margin_ratio),
        "vertical_margin_ratio": float(vertical_margin_ratio),
    }

    def _rectify_conveyor_belt(image_bgr, mask_np, *_, **__):
        np = adjust_module._np()
        cv2 = adjust_module._cv2()

        contours, _hierarchy = cv2.findContours(
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

        # 继续沿用 adjust.py 的边界修正：防止 minAreaRect 角点落到原图外。
        move_inside = getattr(adjust_module, "_move_points_inside_image", None)
        if callable(move_inside):
            pts = move_inside(pts, image_bgr.shape)

        rect_pts = adjust_module.order_points(
            np.asarray(pts, dtype=np.float32)
        )
        src_pts = np.asarray(rect_pts, dtype=np.float32)
        tl, tr, br, bl = rect_pts

        width_a = np.linalg.norm(br - bl)
        width_b = np.linalg.norm(tr - tl)
        height_a = np.linalg.norm(tr - br)
        height_b = np.linalg.norm(tl - bl)

        # ceil 避免 int() 向下截断导致边缘额外损失。
        belt_width = max(2, int(np.ceil(max(width_a, width_b))))
        belt_height = max(2, int(np.ceil(max(height_a, height_b))))

        left, right, top, bottom = _resolve_canvas_margins(
            belt_width,
            belt_height,
            side_margin_ratio=side_margin_ratio,
            vertical_margin_ratio=vertical_margin_ratio,
        )

        # 基础目标矩形：只描述传送带本体，不把整幅原图纳入画布计算。
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

        # 只做固定平移：让传送带左上角位于 (left, top)。
        translation = np.asarray(
            [
                [1.0, 0.0, float(left)],
                [0.0, 1.0, float(top)],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )
        transform_matrix = translation @ np.asarray(
            base_transform,
            dtype=np.float64,
        )

        output_width = int(belt_width + left + right)
        output_height = int(belt_height + top + bottom)

        if output_width <= 0 or output_height <= 0:
            raise RuntimeError(
                f"计算得到无效输出画布：{output_width} x {output_height}"
            )

        # 防止误填 margin 导致异常大的内存分配。
        image_height, image_width = image_bgr.shape[:2]
        max_width = max(int(image_width) * 4, 4096)
        max_height = max(int(image_height) * 4, 4096)
        if output_width > max_width or output_height > max_height:
            raise RuntimeError(
                "安全边界导致输出画布异常大："
                f"{output_width}x{output_height}；原图为 "
                f"{image_width}x{image_height}。请减小 margin。"
            )

        target_points = cv2.perspectiveTransform(
            src_pts.reshape(-1, 1, 2),
            transform_matrix,
        ).reshape(-1, 2).astype(np.float32)

        warped_img = cv2.warpPerspective(
            image_bgr,
            transform_matrix,
            (output_width, output_height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0),
        )

        canvas_state.update(
            {
                "belt_width": int(belt_width),
                "belt_height": int(belt_height),
                "margin_left_px": int(left),
                "margin_right_px": int(right),
                "margin_top_px": int(top),
                "margin_bottom_px": int(bottom),
                "output_width": int(output_width),
                "output_height": int(output_height),
            }
        )

        return (
            warped_img,
            belt_height,
            belt_width,
            transform_matrix,
            src_pts,
            target_points,
        )

    adjust_module.rectify_conveyor_belt = _rectify_conveyor_belt
    return canvas_state


# ---------------------------------------------------------------------------
# 临时目录 / JSON 元数据
# ---------------------------------------------------------------------------


def _cleanup_calibration_tmp() -> None:
    """清空 ``data/calibration/tmp/`` 中本次标定产生的临时图片。"""

    if CALIBRATION_TMP_DIR.exists():
        remove_dir(CALIBRATION_TMP_DIR)
        print(
            "[cleanup] 已清理标定临时目录："
            f"{to_project_relative(CALIBRATION_TMP_DIR)}"
        )


def _read_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取 JSON：{path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON 顶层必须为对象：{path}")
    return payload


def _write_json(path: Path, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


def _append_canvas_metadata(
    adjust_module,
    result,
    canvas_state: dict,
) -> None:
    """把画布策略和安全区写入 current/session calibration JSON。"""

    current_json = adjust_module.CALIBRATION_CURRENT_JSON
    current_payload = _read_json(current_json)

    metadata = {
        "canvas_mode": "belt_margin",
        "canvas_margin_config_path": str(
            canvas_state.get("margin_config_path", "")
        ),
        "canvas_margin_left_px": int(canvas_state.get("margin_left_px", 0)),
        "canvas_margin_right_px": int(canvas_state.get("margin_right_px", 0)),
        "canvas_margin_top_px": int(canvas_state.get("margin_top_px", 0)),
        "canvas_margin_bottom_px": int(canvas_state.get("margin_bottom_px", 0)),
        "canvas_side_margin_ratio": float(canvas_state.get("side_margin_ratio", 0.0)),
        "canvas_vertical_margin_ratio": float(
            canvas_state.get("vertical_margin_ratio", 0.0)
        ),
    }

    current_payload.update(metadata)
    _write_json(current_json, current_payload)

    # run_calibration() 把 session/source.jpg 记录在 result.source_image 中。
    session_json = Path(result.source_image).parent / "calibration.json"
    if session_json.is_file():
        session_payload = _read_json(session_json)
        session_payload.update(metadata)
        _write_json(session_json, session_payload)


def _print_calibration_geometry(
    result,
    current_json: Path,
    canvas_state: dict,
) -> None:
    payload = _read_json(current_json)

    image_width = int(payload.get("image_width", result.image_width) or 0)
    image_height = int(payload.get("image_height", result.image_height) or 0)
    belt_width = int(payload.get("pixel_length", result.pixel_length) or 0)
    belt_height = int(
        payload.get("vertical_distance", result.vertical_distance) or 0
    )
    output_width = int(payload.get("output_width", 0) or 0)
    output_height = int(payload.get("output_height", 0) or 0)

    left = int(canvas_state.get("margin_left_px", 0) or 0)
    right = int(canvas_state.get("margin_right_px", 0) or 0)
    top = int(canvas_state.get("margin_top_px", 0) or 0)
    bottom = int(canvas_state.get("margin_bottom_px", 0) or 0)

    print("---- 标定几何信息 ----")
    print(f"原始图像尺寸       = {image_width} x {image_height}")
    print(f"传送带本体尺寸     = {belt_width} x {belt_height}")
    print(
        "安全边界           = "
        f"left={left}, right={right}, top={top}, bottom={bottom} px"
    )
    print(f"最终输出画布       = {output_width} x {output_height}")

    expected_width = belt_width + left + right
    expected_height = belt_height + top + bottom
    if output_width != expected_width or output_height != expected_height:
        print(
            "[WARN] 输出尺寸与“传送带 + margin”不一致："
            f"期望 {expected_width}x{expected_height}，"
            f"实际 {output_width}x{output_height}。"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="algorithms.core.adjust_calibration",
        description=(
            "单次相机标定：把传送带拉正，并在其四周保留可控安全边界，"
            "避免边缘完整鱼被裁掉，同时避免保留整幅原图产生过大的黑色区域。"
        ),
    )

    parser.add_argument(
        "--source-image",
        required=True,
        help="标定图片路径；支持绝对路径或相对项目根的相对路径。",
    )
    parser.add_argument(
        "--camera-id",
        default="default",
        help="逻辑摄像头 ID，默认 default",
    )
    parser.add_argument(
        "--calibration-id",
        default=None,
        help="可选：手动指定 calibration_id；默认自动生成。",
    )
    parser.add_argument(
        "--real-length-mm",
        type=float,
        default=1000.0,
        help=(
            "传送带长边真实长度（毫米）。mm_per_pixel 仍仅由传送带本体的 "
            "pixel_length 计算，不受安全边界影响。"
        ),
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

    # -------------------- SAM --------------------
    parser.add_argument(
        "--use-sam-hq",
        action="store_true",
        help="启用 SAM-HQ；默认使用普通 SAM。",
    )
    parser.add_argument(
        "--sam-checkpoint",
        default=None,
        help=(
            "普通 SAM 权重路径；默认从 FISH_SAM_CHECKPOINT 或 "
            "algorithms/pth/sam_vit_h_4b8939.pth 解析。"
        ),
    )
    parser.add_argument(
        "--sam-hq-checkpoint",
        default=None,
        help=(
            "SAM-HQ 权重路径；默认从 FISH_SAM_HQ_CHECKPOINT 或 "
            "algorithms/pth/sam_hq_vit_h.pth 解析。"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # 这里必须使用项目统一路径解析，而不是 Path(...).resolve()。
    # 否则从非项目根目录启动时，“相对项目根”的路径会被错误解释。
    source_image = resolve_input_path(args.source_image)
    if source_image is None or not source_image.is_file():
        print(f"[ERROR] 标定图片不存在：{args.source_image}")
        return 2

    try:
        margin_config = _load_canvas_margin_config()
    except RuntimeError as exc:
        print(f"[ERROR] 无法加载安全边界配置：{exc}")
        return 2

    print(
        "[adjust_calibration] 安全边界配置 = "
        f"{to_project_relative(ADJUST_CALIBRATION_MARGIN_CONFIG_PATH)}；"
        f"左右各 {margin_config['side_margin_ratio']:.4f}，"
        f"上下各 {margin_config['vertical_margin_ratio']:.4f}"
    )

    import core.adjust as adjust_core  # noqa: E402

    sam_ckpt, hq_ckpt = _resolve_checkpoint_from_args_or_env(
        args,
        SAM_VIT_H_CHECKPOINT,
        SAM_HQ_VIT_H_CHECKPOINT,
    )
    dino_ckpt = _resolve_checkpoint(
        "FISH_GROUNDING_DINO_CHECKPOINT",
        MODEL_DIR / "groundingdino_swint_ogc.pth",
    )

    print(
        f"[adjust_calibration] GroundingDINO = {dino_ckpt} "
        f"({'OK' if dino_ckpt.is_file() else 'MISSING'})"
    )
    print(
        f"[adjust_calibration] SAM           = {sam_ckpt} "
        f"({'OK' if sam_ckpt.is_file() else 'MISSING'})"
    )
    print(
        f"[adjust_calibration] SAM-HQ        = {hq_ckpt} "
        f"({'OK' if hq_ckpt.is_file() else 'MISSING'}) "
        f"use_sam_hq={args.use_sam_hq}"
    )

    if not dino_ckpt.is_file():
        print(
            "[ERROR] 标定失败：GroundingDINO 权重不存在。\n"
            f"  尝试路径：{dino_ckpt}\n"
            "  可通过 FISH_GROUNDING_DINO_CHECKPOINT 指定。"
        )
        return 2

    # 修正旧逻辑：启用 SAM-HQ 时只强制检查 HQ 权重；
    # 未启用时才要求普通 SAM 权重存在。
    if args.use_sam_hq:
        if not hq_ckpt.is_file():
            print(
                "[ERROR] 标定失败：当前启用了 SAM-HQ，但 SAM-HQ 权重不存在。\n"
                f"  尝试路径：{hq_ckpt}\n"
                "  请通过 --sam-hq-checkpoint 或 FISH_SAM_HQ_CHECKPOINT 指定，"
                "或移除 --use-sam-hq。"
            )
            return 2
    else:
        if not sam_ckpt.is_file():
            print(
                "[ERROR] 标定失败：普通 SAM 权重不存在。\n"
                f"  尝试路径：{sam_ckpt}\n"
                "  请通过 --sam-checkpoint 或 FISH_SAM_CHECKPOINT 指定。"
            )
            return 2

    _configure_adjust_runtime(
        adjust_core,
        device=args.device,
        use_sam_hq=args.use_sam_hq,
        sam_checkpoint=sam_ckpt,
        sam_hq_checkpoint=hq_ckpt,
        grounding_dino_checkpoint=dino_ckpt,
    )

    # 本次运行将 adjust.py 的“完整原图画布”替换为“传送带 + 安全边界”。
    canvas_state = _install_margin_canvas_rectifier(
        adjust_core,
        side_margin_ratio=margin_config["side_margin_ratio"],
        vertical_margin_ratio=margin_config["vertical_margin_ratio"],
    )
    canvas_state["margin_config_path"] = to_project_relative(
        ADJUST_CALIBRATION_MARGIN_CONFIG_PATH
    )

    if adjust_core.CALIBRATION_CURRENT_JSON.is_file() and not args.overwrite:
        print(
            f"[ERROR] {adjust_core.CALIBRATION_CURRENT_JSON} 已存在。"
            "如需覆盖请加 --overwrite。"
        )
        return 2

    _cleanup_calibration_tmp()

    try:
        result = adjust_core.run_calibration(
            source_image=source_image,
            camera_id=args.camera_id,
            calibration_id=args.calibration_id,
            real_length_mm=args.real_length_mm,
            note=args.note,
        )

        _append_canvas_metadata(
            adjust_core,
            result,
            canvas_state,
        )
        _print_calibration_geometry(
            result,
            adjust_core.CALIBRATION_CURRENT_JSON,
            canvas_state,
        )

    except Exception as exc:
        print(
            f"[ERROR] 标定失败：{type(exc).__name__}: {exc}\n"
            f"  GroundingDINO 权重：{dino_ckpt}\n"
            f"  SAM 权重：{sam_ckpt}\n"
            f"  SAM-HQ 权重：{hq_ckpt}\n"
            f"  use_sam_hq={args.use_sam_hq}"
        )
        return 1
    finally:
        _cleanup_calibration_tmp()

    print("==== 标定完成 ====")
    print(f"calibration_id      = {result.calibration_id}")
    print(
        "current_calibration = "
        f"{to_project_relative(adjust_core.CALIBRATION_CURRENT_JSON)}"
    )
    print(
        "calibration session = "
        f"data/calibration/sessions/{result.calibration_id}"
    )
    print(
        "画布策略            = belt_margin（传送带 + 可控安全边界）"
    )
    print(
        "说明：pixel_length / vertical_distance 仍表示传送带本体；"
        "output_width / output_height 表示最终矫正图尺寸。"
    )
    return 0


__all__ = [
    "build_parser",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
