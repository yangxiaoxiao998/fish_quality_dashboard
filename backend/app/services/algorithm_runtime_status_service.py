"""
algorithm_runtime_status_service.py

系统运维 -> 算法模型运行状态服务。

明确事实：
    当前算法是 CLI 子进程模式（worker → subprocess → python -m algorithms.tools.run_video_pipeline），
    每个任务子进程内部 torch.load(...) 加载 / 释放模型，模型不会跨任务常驻显存。
    FastAPI / RQ worker 启动时预热模型并不能被 CLI 子进程复用，反而会浪费显存。
    真正做到模型常驻显存需要把算法改成常驻进程服务（AlgorithmRuntimeService / 独立 algorithm_server），
    本文件只读相关元数据，不做实际预热。

提供：
    get_algorithm_runtime_status()  -> GET /api/ops/algorithm-runtime/status
    preload_algorithm_runtime()     -> POST /api/ops/algorithm-runtime/preload

返回字段（运维可读，普通用户页面继续走原简单提示）：
    mode: "cli" | "resident" | "preload"
    preload_supported: bool
    loaded: bool
    message: str
    gpu: { available, name?, memory_used_mb?, memory_total_mb? }
    models: [{ key, name, path, exists, size_mb }]
    python: str
    torch: { installed, version?, cuda_available?, cuda_version?, device_name? }
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("algorithm_runtime_status_service")

# 默认模型文件候选（key -> (显示名, 默认相对项目根的相对路径)）。
# 真实路径优先读 start_dev.sh 设的环境变量（FISH_BERT_BASE_UNCASED_PATH 等），
# 找不到环境变量时再回退到默认路径。
DEFAULT_MODEL_CANDIDATES: list[tuple[str, str, Optional[str]]] = [
    # (key, display_name, default_relative_path)
    (
        "sam_vit_h",
        "SAM ViT-H",
        "algorithms/pth/sam_vit_h_4b8939.pth",
    ),
    (
        "sam_hq",
        "SAM-HQ",
        "algorithms/pth/sam_hq_vit_h.pth",
    ),
    (
        "groundingdino",
        "GroundingDINO (best_coco_bbox_mAP_epoch_12)",
        "algorithms/pth/best_coco_bbox_mAP_epoch_12.pth",
    ),
    (
        "groundingdino_swint",
        "GroundingDINO SwinT OGC (备用)",
        "algorithms/pth/groundingdino_swint_ogc.pth",
    ),
    (
        "tail_maskrcnn",
        "Tail MaskRCNN",
        "algorithms/pth/maskrcnn_resnet50_fpn_v2-tail.pth",
    ),
    (
        "wound_maskrcnn",
        "Wound MaskRCNN (备用)",
        "algorithms/pth/maskrcnn_resnet50_fpn_v2-wound.pth",
    ),
    (
        "weight_catboost",
        "Weight CatBoost",
        "algorithms/pth/catboost_full_model.pkl",
    ),
    (
        "swin_tiny",
        "Swin-Tiny backbone (备用)",
        "algorithms/pth/swin_tiny_patch4_window7_224.pth",
    ),
    (
        "bert_base_uncased",
        "BERT base uncased (GroundingDINO文本编码器)",
        "algorithms/pth/bert-base-uncased",
    ),
]


# 环境变量 key -> (model_key, 默认相对路径 fallback)
ENV_MODEL_KEYS: dict[str, tuple[str, str]] = {
    "FISH_SAM_CHECKPOINT": ("sam_vit_h", "algorithms/pth/sam_vit_h_4b8939.pth"),
    "FISH_SAM_HQ_CHECKPOINT": ("sam_hq", "algorithms/pth/sam_hq_vit_h.pth"),
    "FISH_GROUNDING_DINO_CHECKPOINT": (
        "groundingdino",
        "algorithms/pth/best_coco_bbox_mAP_epoch_12.pth",
    ),
    "FISH_TAIL_CHECKPOINT": (
        "tail_maskrcnn",
        "algorithms/pth/maskrcnn_resnet50_fpn_v2-tail.pth",
    ),
    "FISH_WEIGHT_MODEL_PATH": (
        "weight_catboost",
        "algorithms/pth/catboost_full_model.pkl",
    ),
    "FISH_BERT_BASE_UNCASED_PATH": (
        "bert_base_uncased",
        "algorithms/pth/bert-base-uncased",
    ),
}

DISPLAY_NAME_OVERRIDES: dict[str, str] = {
    "groundingdino": "GroundingDINO (best_coco_bbox_mAP_epoch_12)",
    "bert_base_uncased": "BERT base uncased (GroundingDINO文本编码器)",
}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _project_root() -> Path:
    from app.paths import PROJECT_ROOT

    return PROJECT_ROOT


def _read_torch_info() -> dict[str, Any]:
    """
    探测 torch / cuda。不强制 import 失败：所有异常都吞掉并降级为 installed=False。
    """
    info: dict[str, Any] = {
        "installed": False,
        "version": None,
        "cuda_available": False,
        "cuda_version": None,
        "device_name": None,
    }
    try:
        import torch  # type: ignore
    except Exception as exc:
        logger.warning("import torch failed: %s", exc)
        info["error"] = f"{type(exc).__name__}: {exc}"
        return info

    info["installed"] = True
    try:
        info["version"] = str(torch.__version__)
    except Exception:
        pass
    try:
        info["cuda_available"] = bool(torch.cuda.is_available())
    except Exception:
        pass
    try:
        info["cuda_version"] = str(torch.version.cuda) if torch.version.cuda else None
    except Exception:
        pass
    if info["cuda_available"]:
        try:
            info["device_name"] = str(torch.cuda.get_device_name(0))
        except Exception:
            pass
    return info


def _read_nvidia_smi_gpu() -> Optional[dict[str, Any]]:
    """
    用 nvidia-smi 兜底读 GPU 显存信息（不依赖 torch）。
    """
    if not shutil.which("nvidia-smi"):
        return None
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (subprocess.TimeoutExpired, Exception):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    parts = [p.strip() for p in proc.stdout.strip().splitlines()[0].split(",")]
    if len(parts) < 3:
        return None
    try:
        return {
            "available": True,
            "name": parts[0],
            "memory_used_mb": int(float(parts[1])),
            "memory_total_mb": int(float(parts[2])),
        }
    except Exception:
        return None


def _read_torch_gpu_info() -> Optional[dict[str, Any]]:
    try:
        import torch  # type: ignore
    except Exception:
        return None
    if not torch.cuda.is_available():
        return None
    try:
        name = str(torch.cuda.get_device_name(0))
        free_b, total_b = torch.cuda.mem_get_info(0)
        return {
            "available": True,
            "name": name,
            "memory_used_mb": int((total_b - free_b) / (1024 * 1024)),
            "memory_total_mb": int(total_b / (1024 * 1024)),
        }
    except Exception as exc:
        logger.warning("read torch gpu info failed: %s", exc)
        return None


def _resolve_model_paths() -> list[dict[str, Any]]:
    """
    返回每个模型的检查结果。优先用环境变量路径；否则回退到默认相对项目根的相对路径。
    """
    root = _project_root()
    display_names = {k: n for k, n, _ in DEFAULT_MODEL_CANDIDATES}
    fallback: dict[str, str] = {
        k: p for k, _, p in DEFAULT_MODEL_CANDIDATES
    }

    by_key: dict[str, dict[str, Any]] = {}

    # 1. 优先用环境变量
    for env_key, (model_key, default_rel) in ENV_MODEL_KEYS.items():
        env_value = os.getenv(env_key)
        rel = env_value or default_rel
        path = Path(rel)
        if not path.is_absolute():
            path = (root / rel).resolve()
        display_name = (
            DISPLAY_NAME_OVERRIDES.get(model_key)
            or display_names.get(model_key)
            or model_key
        )
        entry: dict[str, Any] = {
            "key": model_key,
            "name": display_name,
            "path": str(path),
            "exists": path.is_file() if path.suffix else path.exists(),
            "size_mb": _size_mb(path),
            "source": f"env:{env_key}" if env_value else "default",
        }
        by_key[model_key] = entry

    # 2. 补齐默认候选但还没出现的 key
    for model_key, display_name, default_rel in DEFAULT_MODEL_CANDIDATES:
        if model_key in by_key:
            continue
        path = (root / default_rel).resolve()
        by_key[model_key] = {
            "key": model_key,
            "name": display_name,
            "path": str(path),
            "exists": path.is_file() if path.suffix else path.exists(),
            "size_mb": _size_mb(path),
            "source": "default",
        }

    # 3. BERT 完整性：五个必需文件 + 全部非空才 complete
    bert_required = (
        "config.json",
        "pytorch_model.bin",
        "tokenizer_config.json",
        "tokenizer.json",
        "vocab.txt",
    )
    bert_entry = by_key.get("bert_base_uncased")
    if bert_entry is not None:
        bert_path = Path(bert_entry["path"])
        missing_files: list[str] = [
            name
            for name in bert_required
            if not (bert_path / name).is_file()
        ]
        empty_files: list[str] = [
            name
            for name in bert_required
            if (bert_path / name).is_file()
            and (bert_path / name).stat().st_size <= 0
        ]
        bert_entry["required_files"] = list(bert_required)
        bert_entry["missing_files"] = missing_files
        bert_entry["empty_files"] = empty_files
        bert_entry["complete"] = not missing_files and not empty_files

    # 4. 按 DEFAULT_MODEL_CANDIDATES 顺序输出
    out: list[dict[str, Any]] = []
    for model_key, _, _ in DEFAULT_MODEL_CANDIDATES:
        if model_key in by_key:
            out.append(by_key[model_key])
    return out


def _size_mb(path: Path) -> Optional[float]:
    if not path.exists():
        return None
    try:
        if path.is_file():
            return round(path.stat().st_size / (1024 * 1024), 1)
        if path.is_dir():
            total = 0
            for child in path.rglob("*"):
                if child.is_file():
                    try:
                        total += child.stat().st_size
                    except OSError:
                        pass
            return round(total / (1024 * 1024), 1) if total else 0.0
    except OSError as exc:
        logger.debug("size probe failed: %s err=%s", path, exc)
    return None


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def get_algorithm_runtime_status() -> dict[str, Any]:
    """
    GET /api/ops/algorithm-runtime/status
    """
    torch_info = _read_torch_info()
    gpu_info = _read_torch_gpu_info() or _read_nvidia_smi_gpu() or {
        "available": False
    }

    models = _resolve_model_paths()
    found_count = sum(1 for m in models if m.get("exists"))

    return {
        "ok": True,
        "data": {
            "mode": "cli",
            "preload_supported": False,
            "loaded": False,
            "message": (
                "当前算法以 CLI 子进程方式运行，模型会随任务进程加载和释放；"
                "若要常驻显存，需要后续改造成常驻算法服务（例如常驻 RQ worker 内的 "
                "AlgorithmRuntimeService，或独立 algorithm_server）。"
            ),
            "gpu": gpu_info,
            "models": models,
            "models_summary": {
                "total": len(models),
                "found": found_count,
                "missing": len(models) - found_count,
            },
            "python": _python_executable(),
            "torch": torch_info,
        },
    }


def preload_algorithm_runtime() -> dict[str, Any]:
    """
    POST /api/ops/algorithm-runtime/preload

    当前 CLI 模式：直接返回 PRELOAD_NOT_SUPPORTED_IN_CLI_MODE。

    设计说明（仅作占位，本轮不实现实际预热）：
      1) 未来要支持 preload 时，先把算法从 CLI 改成常驻 Python API；
      2) FastAPI 启动时调用 preload_algorithm_runtime() -> 算法服务 startup hook；
      3) 算法服务持有所有模型对象，worker 直接调用算法服务.run_video_pipeline()。
    """
    return {
        "ok": False,
        "code": "PRELOAD_NOT_SUPPORTED_IN_CLI_MODE",
        "message": (
            "当前算法以 CLI 子进程方式运行，模型无法跨任务常驻显存。"
            "请先改造为常驻算法服务（AlgorithmRuntimeService / 独立 algorithm_server），"
            "再调用 /api/ops/algorithm-runtime/preload。"
        ),
    }


def _python_executable() -> str:
    import sys

    return sys.executable or "python"


__all__ = [
    "get_algorithm_runtime_status",
    "preload_algorithm_runtime",
    "DEFAULT_MODEL_CANDIDATES",
]