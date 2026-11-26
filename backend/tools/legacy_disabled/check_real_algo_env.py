from __future__ import annotations

import os
import sys
from pathlib import Path


# PROJECT_ROOT = Path("/home/yangzt/Code/fish_quality_dashboard")
# BACKEND_ROOT = PROJECT_ROOT / "backend"
# ALGO_ROOT = PROJECT_ROOT / "algorithms"
def _env_path(name: str) -> Path | None:
    value = os.getenv(name)
    if not value:
        return None
    return Path(value).expanduser().resolve()


# 当前文件：
#   <project>/backend/tools/check_real_algo_env.py
# 所以：
#   PROJECT_ROOT = parents[2]
_DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]

PROJECT_ROOT = _env_path("FISH_PROJECT_ROOT") or _DEFAULT_PROJECT_ROOT
BACKEND_ROOT = PROJECT_ROOT / "backend"
ALGO_ROOT = _env_path("FISH_ALGO_ROOT") or (PROJECT_ROOT / "algorithms").resolve()
DATA_ROOT = _env_path("FISH_DATA_ROOT") or (PROJECT_ROOT / "data").resolve()

sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(ALGO_ROOT))

def _add_local_segment_anything_path() -> None:
    candidates = [
        ALGO_ROOT / "Grounded-Segment-Anything" / "segment_anything",
        ALGO_ROOT / "fishalgo" / "measures" / "crop_images" / "segment-anything",
        ALGO_ROOT / "segment-anything",
        ALGO_ROOT / "SegmentAnything",
    ]

    for path in candidates:
        if (path / "segment_anything" / "__init__.py").exists():
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
            print(f"[PATH] local segment-anything: {path}")
            return


_add_local_segment_anything_path()

def title(name: str) -> None:
    print("\n" + "=" * 20 + f" {name} " + "=" * 20)


def check_import(name: str, optional: bool = False) -> bool:
    try:
        mod = __import__(name)
        version = getattr(mod, "__version__", "")
        file = getattr(mod, "__file__", "")
        print(f"[OK] {name} {version} {file}")
        return True
    except Exception as e:
        level = "WARN" if optional else "FAIL"
        print(f"[{level}] {name}: {repr(e)}")
        return optional


def check_file(path: Path, required: bool = True) -> bool:
    if path.exists():
        print(f"[OK] {path}")
        return True

    level = "FAIL" if required else "WARN"
    print(f"[{level}] missing: {path}")
    return not required


def main() -> int:

    ok = True

    title("Python")
    print("python:", sys.executable)
    print("PYTHONPATH:")
    for p in sys.path[:6]:
        print(" ", p)

    title("Backend Packages")
    for name in [
        "fastapi",
        "uvicorn",
        "pydantic",
        "sqlalchemy",
        "psycopg2",
        "redis",
        "rq",
        "requests",
    ]:
        ok = check_import(name) and ok

    title("Algorithm Packages")
    for name in [
        "numpy",
        "cv2",
        "torch",
        "torchvision",
        "mmcv",
        "mmdet",
        "mmengine",
        "segment_anything",
        "catboost",
    ]:
        ok = check_import(name) and ok

    title("Torch CUDA")
    try:
        import torch

        print("torch:", torch.__version__)
        print("torch cuda:", torch.version.cuda)
        print("cuda available:", torch.cuda.is_available())
    except Exception as e:
        print("[FAIL] torch cuda check:", repr(e))
        ok = False

    title("Model Files")
    required_files = [
        ALGO_ROOT / "fishalgo" / "measures" / "crop_images" / "grounded_sam_mmdet_batch2.py",
        ALGO_ROOT / "fishalgo" / "measures" / "crop_images" / "grounding_dino_swin-t_finetune_8xb4_50e_fish.py",
        ALGO_ROOT / "pth" / "best_coco_bbox_mAP_epoch_12.pth",
        ALGO_ROOT / "pth" / "catboost_full_model.pkl",
    ]

    optional_files = [
        ALGO_ROOT / "pth" / "sam_vit_h_4b8939.pth",
        ALGO_ROOT / "pth" / "sam_hq_vit_h.pth",
        ALGO_ROOT / "fishalgo" / "vendor" / "fishxx" / "merge_funs.py",
        ALGO_ROOT / "fishalgo" / "measures" / "long-height.py",
        ALGO_ROOT / "fishalgo" / "measures" / "wound.py",
    ]

    for path in required_files:
        ok = check_file(path, required=True) and ok

    for path in optional_files:
        check_file(path, required=False)

    title("FishAlgo Imports")
    try:
        from fishalgo.measures import (
            calculate_fish_condition_factor,
            calculate_fish_count,
            segment_fish_from_frame,
            correct_fish_image,
            calculate_fish_length_height,
            calculate_fish_weight,
            analyze_fish_surface_damage_with_ratio,
        )

        print("[OK] fishalgo.measures import")
        print("condition:", calculate_fish_condition_factor(body_length=30, body_weight=500))
        print("count:", calculate_fish_count(segment_items=[{"fish_id": "a"}, {"fish_id": "b"}]))
    except Exception as e:
        print("[FAIL] fishalgo import:", repr(e))
        ok = False

    title("Realtime Detection Function")
    try:
        import fishalgo.measures.fish_segment as fish_segment

        print("has detect_fish_in_frame_array:", hasattr(fish_segment, "detect_fish_in_frame_array"))
        print("has segment_fish_from_frame:", hasattr(fish_segment, "segment_fish_from_frame"))
        print("has crop_fish_images_from_frames:", hasattr(fish_segment, "crop_fish_images_from_frames"))

        if not hasattr(fish_segment, "detect_fish_in_frame_array"):
            ok = False
    except Exception as e:
        print("[FAIL] fish_segment realtime check:", repr(e))
        ok = False

    title("Backend App Import")
    try:
        import app.main

        print("[OK] app.main import")
    except Exception as e:
        print("[FAIL] app.main import:", repr(e))
        ok = False

    title("Algo Facade")
    try:
        from app.services import algo_facade

        print("ALGO_MODE:", algo_facade.ALGO_MODE)
        print("has calculate_body_length_without_tail:", hasattr(algo_facade, "calculate_body_length_without_tail"))
        print("has analyze_surface_damage_with_ratio:", hasattr(algo_facade, "analyze_surface_damage_with_ratio"))
        print("condition:", algo_facade.calculate_condition_factor(body_weight=500, body_length=30))
    except Exception as e:
        print("[FAIL] algo_facade:", repr(e))
        ok = False

    title("Database Columns")
    try:
        from sqlalchemy import text
        from app.repositories.db import engine

        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    select column_name
                    from information_schema.columns
                    where table_name = 'fish'
                    """
                )
            ).fetchall()

        cols = {str(r[0]) for r in rows}
        needed = {
            "body_length_without_tail",
            "surface_damage_ratio",
            "mask_image_path",
            "wound_mask_path",
        }

        for col in sorted(needed):
            if col in cols:
                print(f"[OK] fish.{col}")
            else:
                print(f"[FAIL] missing fish.{col}")
                ok = False

    except Exception as e:
        print("[FAIL] database column check:", repr(e))
        ok = False

    title("Result")
    if ok:
        print("REAL ALGO ENV CHECK PASSED")
        return 0

    print("REAL ALGO ENV CHECK FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
