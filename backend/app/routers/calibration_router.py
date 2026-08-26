# 摄像头视角标定相关接口
# 提供接口：
# GET  /api/calibration/status       --- 获取当前标定状态和配置
# POST /api/calibration/save         --- 手动保存当前标定配置
# POST /api/calibration/clear        --- 清空当前标定配置
# POST /api/calibration/perspective  --- 上传一帧图片，自动计算并保存视角矫正参数

from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.services.calibration_config_service import (
    clear_calibration_config,
    get_calibration_status,
    save_calibration_config,
    get_margin_config,
    save_margin_config,
)


router = APIRouter()


# =============================================================================
# 路径配置
# =============================================================================
# 当前文件：<project>/backend/app/routers/calibration_router.py
# parents[3] = <project>
_DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[3]

PROJECT_ROOT = Path(
    os.getenv("FISH_PROJECT_ROOT", str(_DEFAULT_PROJECT_ROOT))
).expanduser()

ALGORITHMS_ROOT = Path(
    os.getenv("FISH_ALGO_ROOT", str(PROJECT_ROOT / "algorithms"))
).expanduser()

DATA_ROOT = Path(
    os.getenv("FISH_DATA_ROOT", str(PROJECT_ROOT / "data"))
).expanduser()


# =============================================================================
# 请求体
# =============================================================================

class CalibrationSaveRequest(BaseModel):
    enabled: bool = True
    camera_id: str = "default"

    source_image_path: str | None = None
    belt_mask_path: str | None = None
    rectified_preview_path: str | None = None

    transform_matrix: list[list[float]] | None = Field(default=None)

    vertical_distance: float | None = None
    pixel_length: float | None = None
    note: str | None = None


# =============================================================================
# 原有接口：保留
# =============================================================================
"""
GET  /api/calibration/status       --- 获取当前标定状态和配置
POST /api/calibration/save         --- 手动保存当前标定配置
POST /api/calibration/clear        --- 清空当前标定配置
"""
@router.get("/calibration/status")
def calibration_status() -> dict[str, Any]:
    return get_calibration_status()


@router.post("/calibration/save")
def calibration_save(payload: CalibrationSaveRequest) -> dict[str, Any]:
    return save_calibration_config(payload.model_dump())


@router.post("/calibration/clear")
def calibration_clear() -> dict[str, Any]:
    return clear_calibration_config()


@router.get("/calibration/margin-config")
def calibration_margin_config() -> dict[str, Any]:
    return {"ok": True, **get_margin_config()}


@router.post("/calibration/margin-config")
def save_calibration_margin_config(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"ok": True, **save_margin_config(payload)}
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# =============================================================================
# 接口：上传图片自动计算透视矩阵
# =============================================================================
"""
POST /api/calibration/perspective
"""
@router.post("/calibration/perspective")
def calibration_perspective(
    file: UploadFile = File(...),
    camera_id: str = "default",
) -> dict[str, Any]:
    """
    上传一帧当前摄像头画面，自动检测传送带区域，计算透视矩阵，并保存到：

        data/config/calibration/current_camera_calibration.json

    注意：
    - 这个接口会调用算法环境中的 GroundingDINO + SAM；
    - .venv_algo 运行环境必须能 import mmdet / mmcv / mmengine / segment_anything / torch；

    """
    suffix = Path(file.filename or "frame.jpg").suffix.lower()

    if suffix not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported image type: {suffix}",
        )

    upload_dir = DATA_ROOT / "uploads" / "calibration" / datetime.now().strftime("%Y/%m/%d")
    upload_dir.mkdir(parents=True, exist_ok=True)

    filename = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + suffix
    image_path = upload_dir / filename

    try:
        with image_path.open("wb") as f:
            shutil.copyfileobj(file.file, f)

        # 确保算法包路径正确
        # 保证算法包路径正确
        if str(ALGORITHMS_ROOT) not in sys.path:
            sys.path.insert(0, str(ALGORITHMS_ROOT))

        os.environ["FISH_PROJECT_ROOT"] = str(PROJECT_ROOT)
        os.environ["FISH_ALGO_ROOT"] = str(ALGORITHMS_ROOT)
        os.environ["FISH_DATA_ROOT"] = str(DATA_ROOT)

        prepare_algorithm_runtime()
        from fishalgo.measures.camera_calibration import calibrate_camera_from_image  # type: ignore[import-untyped]

        calibration = calibrate_camera_from_image(
            image_path=str(image_path),
            camera_id=camera_id,
        )

        from app.services.path_service import to_data_relative, to_static_url

        rel_path = to_data_relative(image_path) or f"data/uploads/calibration/{image_path.relative_to(DATA_ROOT).as_posix()}"
        static_url = to_static_url(rel_path) or f"/static/{rel_path[len('data/'):] if rel_path.startswith('data/') else rel_path}"

        return {
            "ok": True,
            "message": "perspective calibration saved",
            "source_image_path": rel_path,
            "source_image_url": static_url,
            "image_path": str(image_path),
            "calibration": calibration,
        }

    except Exception as exc:
        import traceback

        raise HTTPException(
            status_code=500,
            detail={
                "ok": False,
                "error": str(exc),
                "traceback": traceback.format_exc(limit=20),
                "image_path": str(image_path),
                "hint": (
                    "请检查 adjust.py 是否提供 calibrate_camera / calibrate_from_image / "
                    "run_calibration，或者 rectify_conveyor_belt + detect_belt_mask。"
                ),
            },
        )

    finally:
        try:
            file.file.close()
        except Exception:
            pass


# =============================================================================
# 类型检查配置
# =============================================================================
# fishalgo 模块在运行时通过 sys.path 动态添加，Pylance 无法静态解析
# type: ignore 用于抑制静态类型检查器的导入错误提示

# prepare_algorithm_runtime：确保算法运行时路径正确
def prepare_algorithm_runtime() -> None:
    """
    给自动视角标定准备算法运行路径和缓存目录。
    防止第三方库写 /home/data、~/.cache 等无权限路径。
    """
    candidates = [
        ALGORITHMS_ROOT,
        ALGORITHMS_ROOT / "fishalgo" / "measures" / "crop_images",

        ALGORITHMS_ROOT / "GroundingDINO",
        ALGORITHMS_ROOT / "Grounded-Segment-Anything" / "GroundingDINO",

        ALGORITHMS_ROOT / "segment-anything",
        ALGORITHMS_ROOT / "Grounded-Segment-Anything",
        ALGORITHMS_ROOT / "Grounded-Segment-Anything" / "segment_anything",
    ]

    for path in candidates:
        if path.exists() and str(path) not in sys.path:
            sys.path.insert(0, str(path))

    tmp_root = DATA_ROOT / "tmp" / "algo_runtime"
    cache_root = tmp_root / "cache"
    matplotlib_root = tmp_root / "matplotlib"
    torch_root = tmp_root / "torch"
    hf_root = tmp_root / "huggingface"
    ultralytics_root = tmp_root / "ultralytics"

    for path in [
        tmp_root,
        cache_root,
        matplotlib_root,
        torch_root,
        hf_root,
        hf_root / "hub",
        hf_root / "transformers",
        ultralytics_root,
    ]:
        path.mkdir(parents=True, exist_ok=True)

    os.environ["FISH_PROJECT_ROOT"] = str(PROJECT_ROOT)
    os.environ["FISH_ALGO_ROOT"] = str(ALGORITHMS_ROOT)
    os.environ["FISH_DATA_ROOT"] = str(DATA_ROOT)

    os.environ["MPLCONFIGDIR"] = str(matplotlib_root)
    os.environ["XDG_CACHE_HOME"] = str(cache_root)
    os.environ["TORCH_HOME"] = str(torch_root)
    os.environ["HF_HOME"] = str(hf_root)
    os.environ["HF_HUB_CACHE"] = str(hf_root / "hub")
    os.environ["TRANSFORMERS_CACHE"] = str(hf_root / "transformers")
    os.environ["YOLO_CONFIG_DIR"] = str(ultralytics_root)

    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    # BERT 唯一目录：algorithms/pth/bert-base-uncased。
    # 强制覆盖（不用 setdefault），与 algorithm_cli_service._build_env 完全一致。
    bert_dir = (
        ALGORITHMS_ROOT
        / "pth"
        / "bert-base-uncased"
    ).resolve()
    required_bert_files = (
        "config.json",
        "pytorch_model.bin",
        "tokenizer_config.json",
        "tokenizer.json",
        "vocab.txt",
    )
    missing_bert_files = [
        name
        for name in required_bert_files
        if not (bert_dir / name).is_file()
    ]
    if missing_bert_files:
        raise FileNotFoundError(
            "BERT模型目录不存在或文件不完整，缺少："
            + ", ".join(missing_bert_files)
            + f"（{bert_dir}）"
        )
    os.environ["FISH_BERT_BASE_UNCASED_PATH"] = str(bert_dir)
    os.environ["GDINO_BERT_PATH"] = str(bert_dir)
    os.environ.setdefault(
        "FISH_GROUNDING_DINO_CONFIG",
        str(ALGORITHMS_ROOT / "fishalgo" / "measures" / "crop_images" / "grounding_dino_swin-t_finetune_8xb4_50e_fish.py"),
    )
    os.environ.setdefault(
        "FISH_GROUNDING_DINO_CHECKPOINT",
        str(ALGORITHMS_ROOT / "pth" / "groundingdino_swint_ogc.pth"),
    )
    os.environ.setdefault(
        "FISH_SAM_CHECKPOINT",
        str(ALGORITHMS_ROOT / "pth" / "sam_vit_h_4b8939.pth"),
    )
    os.environ.setdefault(
        "FISH_SAM_HQ_CHECKPOINT",
        str(ALGORITHMS_ROOT / "pth" / "sam_hq_vit_h.pth"),
    )
