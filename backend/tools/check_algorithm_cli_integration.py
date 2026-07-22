"""
check_algorithm_cli_integration.py - 算法 CLI 集成轻量校验

不跑真实算法；只检查：
1. algorithm_cli_service 可 import
2. algorithm_result_service 可 import
3. calibration 状态可读
4. results 目录是否存在
5. 如果存在最新 pipeline_result.json，尝试读取并做字段映射
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


def main() -> int:
    print("=" * 60)
    print("check_algorithm_cli_integration")
    print("=" * 60)

    print("[1] importing algorithm_cli_service ...")
    from app.services import algorithm_cli_service

    print(f"  algorithm_cli_service.__file__ = {algorithm_cli_service.__file__}")
    print(f"  ALGORITHM_PYTHON              = {algorithm_cli_service.ALGORITHM_PYTHON}")
    print(f"  CALIBRATION_JSON              = {algorithm_cli_service.CALIBRATION_JSON}")
    print(f"  RESULTS_DIR                   = {algorithm_cli_service.RESULTS_DIR}")

    print("[2] importing algorithm_result_service ...")
    from app.services import algorithm_result_service

    print(f"  algorithm_result_service.__file__ = {algorithm_result_service.__file__}")

    print("[3] calibration status ...")
    calib_exists = algorithm_cli_service.CALIBRATION_JSON.exists()
    print(f"  calibration_json.exists() = {calib_exists}")
    if calib_exists:
        print(f"  url = {algorithm_cli_service.algorithm_path_to_static_url('data/calibration/current_calibration.json')}")

    print("[4] results directory ...")
    if algorithm_cli_service.RESULTS_DIR.exists():
        sub_dirs = [
            p.name
            for p in algorithm_cli_service.RESULTS_DIR.iterdir()
            if p.is_dir() and not p.name.endswith(".partial")
        ]
        print(f"  RESULTS_DIR subdirs ({len(sub_dirs)}): {sub_dirs[:10]}{'...' if len(sub_dirs) > 10 else ''}")
    else:
        print(f"  RESULTS_DIR not found: {algorithm_cli_service.RESULTS_DIR}")

    print("[5] latest pipeline_result.json (read-only mapping) ...")
    latest = algorithm_cli_service.find_latest_result_video_id()
    if latest:
        print(f"  latest video_id = {latest}")
        try:
            payload = algorithm_cli_service.read_pipeline_result(latest)
            vt = algorithm_result_service.pipeline_result_to_video_task_fields(payload)
            fishes = payload.get("fish") or []
            print(f"  status = {payload.get('status')}")
            print(f"  total_fish = {vt.get('total_fish')}")
            print(f"  complete_fish = {vt.get('complete_fish')}")
            print(f"  damaged_fish = {vt.get('damaged_fish')}")
            print(f"  fish[] = {len(fishes)}")
            if fishes:
                sample = algorithm_result_service.pipeline_fish_to_db_fields(fishes[0])
                print(f"  sample fish_uid = {sample.get('fish_uid')}")
                print(f"  sample body_length_cm = {sample.get('body_length_cm')}")
                print(f"  sample surface_damage_ratio = {sample.get('surface_damage_ratio')}")
        except Exception as exc:
            print(f"  [WARN] failed to read/parse: {exc}")
    else:
        print("  no pipeline_result.json found")

    print("[6] URL conversion sample ...")
    print(f"  data/results/x/fish_images/y_crop.jpg -> {algorithm_cli_service.algorithm_path_to_static_url('data/results/x/fish_images/y_crop.jpg')}")

    print("=" * 60)
    print("check_algorithm_cli_integration: OK")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
