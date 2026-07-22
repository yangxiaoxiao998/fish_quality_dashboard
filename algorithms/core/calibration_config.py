"""透视标定矩阵加载器（algorithms 侧）。

唯一允许的来源：系统「视觉矫正」页面保存的 JSON 配置。

不允许再使用任何写死默认矩阵。如果 JSON 不存在或矩阵无效，
直接抛出清晰错误，提示用户去 系统设置 → 视觉矫正 完成标定。

数据真实保存路径：
  - data/calibration/current_calibration.json    （系统视觉矫正页面写入的实际文件）
  - data/config/calibration/current_camera_calibration.json  （旧的备份位置）

JSON 关键字段（任一即可识别）：
  - transform_matrix      3x3 list
  - perspective_matrix    3x3 list
  - matrix / homography   3x3 list

字段 mm_per_pixel 也会同时返回（如果 JSON 中存在）。

注意：algorithms/core 运行时不应依赖 FastAPI / SQLAlchemy / Settings，
因此这里只用最朴素的 json + Path。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np


def get_project_root() -> Path:
    env_root = os.getenv("FISH_PROJECT_ROOT") or os.getenv("FISH_PROJECT_ROOT_REAL")
    if env_root:
        return Path(env_root).expanduser().resolve()
    # algorithms/core/calibration_config.py
    return Path(__file__).resolve().parents[2]


def candidate_transform_json_paths() -> list[Path]:
    """视觉矫正 JSON 候选路径（按优先级排列）。"""
    root = get_project_root()

    env_json = (
        os.getenv("FISH_PERSPECTIVE_MATRIX_JSON")
        or os.getenv("FISH_CALIBRATION_CONFIG")
    )
    paths: list[Path] = []
    if env_json:
        paths.append(Path(env_json).expanduser())

    paths.extend(
        [
            root / "data" / "calibration" / "current_calibration.json",
            root / "data" / "calibration" / "transform_matrix.json",
            root / "data" / "calibration" / "calibration.json",
            root / "data" / "config" / "calibration" / "current_camera_calibration.json",
            root / "data" / "config" / "calibration.json",
            root / "data" / "config" / "perspective.json",
        ]
    )

    # 去重保序
    out: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        rp = p if p.is_absolute() else (root / p)
        s = str(rp)
        if s not in seen:
            out.append(rp)
            seen.add(s)
    return out


def _extract_matrix_from_json(data: Any, camera_id: str = "default") -> Any:
    """兼容多种 JSON 结构提取 transform_matrix。"""
    if not isinstance(data, dict):
        return None

    # 1) 根节点直接放矩阵字段
    for key in ("transform_matrix", "perspective_matrix", "matrix", "homography"):
        value = data.get(key)
        if value is not None:
            return value

    # 2) 字典按 camera_id
    cameras = data.get("cameras") or data.get("camera_configs") or data.get("items")
    if isinstance(cameras, dict):
        cfg = cameras.get(camera_id) or cameras.get("default")
        if isinstance(cfg, dict):
            for key in ("transform_matrix", "perspective_matrix", "matrix", "homography"):
                value = cfg.get(key)
                if value is not None:
                    return value

    # 3) 列表保存
    if isinstance(cameras, list):
        matched = None
        for item in cameras:
            if not isinstance(item, dict):
                continue
            if str(item.get("camera_id") or "default") == camera_id:
                matched = item
                break
        if matched is None and cameras:
            matched = cameras[0] if isinstance(cameras[0], dict) else None
        if isinstance(matched, dict):
            for key in ("transform_matrix", "perspective_matrix", "matrix", "homography"):
                value = matched.get(key)
                if value is not None:
                    return value

    # 4) 嵌套 calibration / perspective / current
    nested = data.get("calibration") or data.get("perspective") or data.get("current")
    if isinstance(nested, dict):
        return _extract_matrix_from_json(nested, camera_id=camera_id)

    return None


def normalize_transform_matrix(value: Any) -> np.ndarray:
    arr = np.asarray(value, dtype=np.float64)
    if arr.shape == (3, 3):
        return arr
    if arr.size == 9:
        return arr.reshape(3, 3)
    raise ValueError(
        f"透视矩阵格式错误，期望 3x3 或 9 个数，实际 shape={arr.shape}, size={arr.size}"
    )


def load_system_transform_matrix(camera_id: str = "default") -> np.ndarray:
    """读取系统视觉矫正 JSON 中的透视矩阵。

    失败时抛出清晰异常，绝不静默回退到错误默认矩阵。
    """
    tried: list[str] = []
    for path in candidate_transform_json_paths():
        tried.append(str(path))
        if not path.exists():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception as exc:
            print(
                f"[calibration_config] skip invalid json {path}: "
                f"{type(exc).__name__}: {exc}"
            )
            continue
        matrix_value = _extract_matrix_from_json(data, camera_id=camera_id)
        if matrix_value is None:
            continue
        try:
            matrix = normalize_transform_matrix(matrix_value)
        except ValueError as exc:
            print(
                f"[calibration_config] invalid matrix in {path}: {exc}"
            )
            continue
        print(f"[calibration_config] loaded transform_matrix from {path}")
        return matrix

    raise FileNotFoundError(
        "未找到系统视觉矫正透视矩阵 JSON，或 JSON 中没有有效 transform_matrix。"
        "请先在系统设置 → 视觉矫正 中完成标定并保存。"
        f"已尝试路径：{tried}"
    )


def load_mm_per_pixel(camera_id: str = "default") -> float | None:
    """读取 mm_per_pixel 字段；JSON 没有则返回 None。"""
    for path in candidate_transform_json_paths():
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        value = data.get("mm_per_pixel")
        if value is None:
            nested = (
                data.get("calibration")
                or data.get("perspective")
                or data.get("current")
            )
            if isinstance(nested, dict):
                value = nested.get("mm_per_pixel")
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


__all__ = [
    "candidate_transform_json_paths",
    "get_project_root",
    "load_mm_per_pixel",
    "load_system_transform_matrix",
    "normalize_transform_matrix",
]