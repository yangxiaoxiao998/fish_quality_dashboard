"""
check_real_algo_dependencies.py - 真实算法依赖检查脚本

目标：
    在真实 POST 视频测试之前，确认真实算法路径（GroundingDINO + SAM）
    所需的依赖是否就绪。

关键依赖（缺失 -> ERROR，exit code 非 0）：
    - cv2
    - torch
    - mmengine
    - mmcv
    - mmdet
    - segment_anything
    - GroundingDINO（import 路径）
    - FISH_GROUNDING_DINO_CONFIG
    - FISH_GROUNDING_DINO_CHECKPOINT
    - FISH_SAM_CHECKPOINT

可选依赖（缺失 -> WARN，exit code 0）：
    - matplotlib（如果当前 ground_sam 真实路径已能无 matplotlib 运行）
    - FISH_SAM_HQ_CHECKPOINT
    - FISH_TAIL_CHECKPOINT
    - FISH_WOUND_CHECKPOINT
    - FISH_BERT_BASE_UNCASED_PATH
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(
    os.getenv("FISH_PROJECT_ROOT", Path(__file__).absolute().parents[2])
).expanduser().absolute()
BACKEND_DIR = PROJECT_ROOT / "backend"
ALGO_DIR = PROJECT_ROOT / "algorithms"


def _try_import(name: str) -> tuple[bool, str]:
    try:
        m = importlib.import_module(name)
        ver = getattr(m, "__version__", "")
        return True, ver
    except Exception as e:
        return False, repr(e)


def _check_path(name: str, value: str | None) -> tuple[bool, str]:
    if not value:
        return False, f"NOT SET ({name})"
    p = Path(value)
    if not p.exists():
        return False, f"NOT FOUND: {p}"
    return True, str(p)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--no-strict-matplotlib",
        action="store_true",
        help="treat matplotlib as optional (WARN instead of ERROR)",
    )
    args = parser.parse_args()

    errors: list[str] = []
    warnings: list[str] = []

    print("========== real algorithm dependency check ==========")
    print(f"python     = {sys.executable}")
    print(f"PROJECT_ROOT = {PROJECT_ROOT}")
    print(f"PROJECT_ROOT_REAL = {PROJECT_ROOT.resolve()}")
    print(f"BACKEND_DIR = {BACKEND_DIR}")
    print(f"ALGO_DIR    = {ALGO_DIR}")
    print(f"CUDA available = { _try_cuda() }")
    print()

    print("========== Python module imports ==========")
    critical_modules = [
        "cv2",
        "torch",
        "mmengine",
        "mmcv",
        "mmdet",
        "segment_anything",
    ]
    for name in critical_modules:
        ok, info = _try_import(name)
        status = "OK" if ok else "ERROR"
        print(f"  {name:24s} {status:6s} {info}")
        if not ok:
            errors.append(f"missing module: {name}")

    print()
    print("========== GroundingDINO import ==========")
    gsa_repo = ALGO_DIR / "Grounded-Segment-Anything"
    if str(gsa_repo) not in sys.path:
        sys.path.insert(0, str(gsa_repo))
    if str(ALGO_DIR / "GroundingDINO") not in sys.path:
        sys.path.insert(0, str(ALGO_DIR / "GroundingDINO"))

    gsa_ok, gsa_info = _try_import("grounded_sam_demo")
    if not gsa_ok:
        # 真实入口可能命名为 grounded_sam_demo 或其它，这里只检查最常用名字
        gsa_info2 = ""
        gsa_ok2, gsa_info2 = _try_import("groundingdino")
        if gsa_ok2:
            gsa_ok = True
            gsa_info = gsa_info2 + " (fallback)"
    status = "OK" if gsa_ok else "ERROR"
    print(f"  {'groundingdino/grounded_sam':24s} {status:6s} {gsa_info}")
    if not gsa_ok:
        errors.append("cannot import GroundingDINO")

    print()
    print("========== required model files ==========")
    required_files = [
        ("FISH_GROUNDING_DINO_CONFIG", os.getenv("FISH_GROUNDING_DINO_CONFIG")),
        ("FISH_GROUNDING_DINO_CHECKPOINT", os.getenv("FISH_GROUNDING_DINO_CHECKPOINT")),
        ("FISH_SAM_CHECKPOINT", os.getenv("FISH_SAM_CHECKPOINT")),
    ]
    for name, value in required_files:
        ok, info = _check_path(name, value)
        status = "OK" if ok else "ERROR"
        print(f"  {name:32s} {status:6s} {info}")
        if not ok:
            errors.append(f"missing/empty env: {name}")

    print()
    print("========== optional ==========")
    optional_modules = ["matplotlib"]
    for name in optional_modules:
        ok, info = _try_import(name)
        if not ok and not args.no_strict_matplotlib:
            status = "ERROR"
            errors.append(f"missing module: {name}")
        elif not ok:
            status = "WARN"
            warnings.append(f"missing optional module: {name}")
        else:
            status = "OK"
        print(f"  {name:24s} {status:6s} {info}")

    optional_files = [
        ("FISH_SAM_HQ_CHECKPOINT", os.getenv("FISH_SAM_HQ_CHECKPOINT")),
        ("FISH_TAIL_CHECKPOINT", os.getenv("FISH_TAIL_CHECKPOINT")),
        ("FISH_WOUND_CHECKPOINT", os.getenv("FISH_WOUND_CHECKPOINT")),
        ("FISH_BERT_BASE_UNCASED_PATH", os.getenv("FISH_BERT_BASE_UNCASED_PATH")),
    ]
    for name, value in optional_files:
        ok, info = _check_path(name, value)
        status = "OK" if ok else "WARN"
        print(f"  {name:32s} {status:6s} {info}")
        if not ok:
            warnings.append(f"missing optional env: {name}")

    print()
    print("========== summary ==========")
    print(f"  errors   = {len(errors)}")
    for e in errors:
        print(f"    - {e}")
    print(f"  warnings = {len(warnings)}")
    for w in warnings:
        print(f"    - {w}")

    if errors:
        print("[FAIL] critical dependencies missing")
        return 1
    print("[OK] critical dependencies present")
    return 0


def _try_cuda() -> str:
    try:
        import torch

        return str(torch.cuda.is_available())
    except Exception as e:
        return f"<error: {e!r}>"


if __name__ == "__main__":
    raise SystemExit(main())
