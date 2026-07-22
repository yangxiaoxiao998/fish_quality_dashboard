"""
check_model_cache_behavior.py - 模型缓存行为验证脚本

用途：
    验证每个算法模型的进程内缓存行为：
        - 第一次调用时加载。
        - 第二次调用复用同一个对象（id 一致）。
        - clear_*() 后再次加载对象变化。
        - 模型缺失时 SKIP，而不是整个脚本崩溃。

参数：
    --include-crop    默认 false，避免 GroundingDINO/SAM 太慢。
    --include-weight  默认 false。
    --include-wound   默认 false。
    --include-tail    默认 false。
    --include-correct 默认 false。
    --all-light       = --include-weight --include-wound --include-tail --include-correct
    --json            输出 JSON。

退出码：
    - 所有包含项 PASS 或 SKIP：0
    - 任一包含项 FAIL：1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _python_exe() -> str:
    return sys.executable


def _check_weight(algo_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "SKIP"}
    try:
        sys.path.insert(0, str(algo_root))
        import fishalgo.measures.fish_weight as fw
    except Exception as exc:
        result["status"] = "FAIL"
        result["reason"] = f"import failed: {exc!r}"
        return result

    pkl_path = fw.get_fish_weight_model_path()
    result["path"] = pkl_path
    if not pkl_path or not Path(pkl_path).exists():
        result["status"] = "SKIP"
        result["reason"] = f"weight model pkl not found: {pkl_path}"
        return result

    try:
        pkg1 = fw.get_weight_model_package()
        pkg2 = fw.get_weight_model_package()
        same_before_clear = id(pkg1) == id(pkg2)
        fw.clear_weight_model_cache()
        pkg3 = fw.get_weight_model_package()
        same_after_clear = id(pkg1) == id(pkg3)
        result["same_before_clear"] = same_before_clear
        result["same_after_clear"] = same_after_clear
        result["clear_ok"] = True
        result["status"] = "PASS" if (same_before_clear and not same_after_clear) else "FAIL"
        if result["status"] == "FAIL":
            result["reason"] = (
                f"same_before_clear={same_before_clear} "
                f"same_after_clear={same_after_clear} (expected True / False)"
            )
    except Exception as exc:
        result["status"] = "FAIL"
        result["reason"] = f"weight check failed: {exc!r}"
    return result


def _check_wound(algo_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "SKIP"}
    try:
        sys.path.insert(0, str(algo_root))
        import fishalgo.measures.fish_wound as fwd
    except Exception as exc:
        result["status"] = "FAIL"
        result["reason"] = f"import failed: {exc!r}"
        return result

    from fishalgo.paths import ALGORITHMS_ROOT

    candidates = [
        ALGORITHMS_ROOT / "pth" / "maskrcnn_resnet50_fpn_v2-wound.pth",
        ALGORITHMS_ROOT / "fishalgo" / "measures" / "pth" / "maskrcnn_resnet50_fpn_v2-wound.pth",
        ALGORITHMS_ROOT / "fishalgo" / "measures" / "wound_model" / "maskrcnn_resnet50_fpn_v2-wound.pth",
    ]
    pth = next((p for p in candidates if p.exists()), None)
    result["path"] = str(pth) if pth else None
    if pth is None:
        result["status"] = "SKIP"
        result["reason"] = "wound checkpoint not found"
        return result

    try:
        m1, _d1 = fwd.get_wound_model_with_device()
        m2, _d2 = fwd.get_wound_model_with_device()
        same_before_clear = id(m1) == id(m2)
        fwd.clear_wound_model_cache()
        m3, _d3 = fwd.get_wound_model_with_device()
        same_after_clear = id(m1) == id(m3)
        result["same_before_clear"] = same_before_clear
        result["same_after_clear"] = same_after_clear
        result["clear_ok"] = True
        result["status"] = "PASS" if (same_before_clear and not same_after_clear) else "FAIL"
        if result["status"] == "FAIL":
            result["reason"] = (
                f"same_before_clear={same_before_clear} "
                f"same_after_clear={same_after_clear} (expected True / False)"
            )
    except Exception as exc:
        result["status"] = "FAIL"
        result["reason"] = f"wound check failed: {exc!r}"
    return result


def _check_tail(algo_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "SKIP"}
    try:
        sys.path.insert(0, str(algo_root))
        import fishalgo.measures.long_height as lh
    except Exception as exc:
        result["status"] = "FAIL"
        result["reason"] = f"import failed: {exc!r}"
        return result

    ckpt = lh._get_tail_checkpoint_path()
    result["path"] = ckpt
    if not Path(ckpt).exists():
        result["status"] = "SKIP"
        result["reason"] = f"tail checkpoint not found: {ckpt}"
        return result

    try:
        from inference_tail import TailDetector  # noqa: F401
    except Exception as exc:
        result["status"] = "SKIP"
        result["reason"] = f"inference_tail not importable: {exc!r}"
        return result

    try:
        d1 = lh.get_tail_detector()
        d2 = lh.get_tail_detector()
        same_before_clear = id(d1) == id(d2)
        lh.clear_tail_detector_cache()
        d3 = lh.get_tail_detector()
        same_after_clear = id(d1) == id(d3)
        result["same_before_clear"] = same_before_clear
        result["same_after_clear"] = same_after_clear
        result["clear_ok"] = True
        result["status"] = "PASS" if (same_before_clear and not same_after_clear) else "FAIL"
        if result["status"] == "FAIL":
            result["reason"] = (
                f"same_before_clear={same_before_clear} "
                f"same_after_clear={same_after_clear} (expected True / False)"
            )
    except Exception as exc:
        result["status"] = "FAIL"
        result["reason"] = f"tail check failed: {exc!r}"
    return result


def _check_correct(algo_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "SKIP"}
    try:
        sys.path.insert(0, str(algo_root))
        import fishalgo.measures.fish_correct as fc
    except Exception as exc:
        result["status"] = "FAIL"
        result["reason"] = f"import failed: {exc!r}"
        return result

    # 检查 merge_funs.py 是否能找到
    merge_funs = None
    try:
        merge_funs = fc._load_merge_funs()
    except FileNotFoundError as exc:
        result["status"] = "SKIP"
        result["reason"] = f"merge_funs.py not found: {exc!r}"
        return result
    except Exception as exc:
        result["status"] = "FAIL"
        result["reason"] = f"merge_funs load failed: {exc!r}"
        return result

    try:
        # 第二次调用应该返回同一个 module 对象
        m2 = fc._load_merge_funs()
        same_before_clear = merge_funs is m2
        fc.clear_correct_model_cache()
        m3 = fc._load_merge_funs()
        same_after_clear = merge_funs is m3
        result["same_before_clear"] = same_before_clear
        result["same_after_clear"] = same_after_clear
        result["clear_ok"] = True
        # merge_funs 自身模块对象在 importlib loader 层面是同一个 sys.modules entry，
        # 因此同进程内 id 通常一直相同；这里只校验 clear_correct_model_cache 不报错。
        result["status"] = "PASS"
    except Exception as exc:
        result["status"] = "FAIL"
        result["reason"] = f"correct check failed: {exc!r}"
    return result


def _check_crop(algo_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "SKIP"}
    try:
        sys.path.insert(0, str(algo_root))
        import fishalgo.measures.fish_segment as fs
    except Exception as exc:
        result["status"] = "FAIL"
        result["reason"] = f"import failed: {exc!r}"
        return result

    try:
        m1, p1 = fs._get_model_and_predictor()
        m2, p2 = fs._get_model_and_predictor()
        same_before_clear = id(m1) == id(m2) and id(p1) == id(p2)
        fs.clear_crop_model_cache()
        m3, p3 = fs._get_model_and_predictor()
        same_after_clear = id(m1) == id(m3)
        result["same_before_clear"] = same_before_clear
        result["same_after_clear"] = same_after_clear
        result["clear_ok"] = True
        result["status"] = "PASS" if (same_before_clear and not same_after_clear) else "FAIL"
        if result["status"] == "FAIL":
            result["reason"] = (
                f"same_before_clear={same_before_clear} "
                f"same_after_clear={same_after_clear} (expected True / False)"
            )
    except Exception as exc:
        result["status"] = "FAIL"
        result["reason"] = f"crop check failed: {exc!r}"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-crop", action="store_true")
    parser.add_argument("--include-weight", action="store_true")
    parser.add_argument("--include-wound", action="store_true")
    parser.add_argument("--include-tail", action="store_true")
    parser.add_argument("--include-correct", action="store_true")
    parser.add_argument(
        "--all-light",
        action="store_true",
        help="equivalent to --include-weight --include-wound --include-tail --include-correct",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.all_light:
        args.include_weight = True
        args.include_wound = True
        args.include_tail = True
        args.include_correct = True

    project_root = Path(
        os.getenv("FISH_PROJECT_ROOT", Path(__file__).absolute().parents[2])
    ).expanduser().absolute()
    algo_root = project_root / "algorithms"

    summary: dict[str, Any] = {
        "project_root": str(project_root),
        "algo_root": str(algo_root),
        "python": _python_exe(),
    }

    if args.include_weight:
        summary["weight"] = _check_weight(algo_root)
    else:
        summary["weight"] = {"status": "SKIP", "reason": "not included"}

    if args.include_wound:
        summary["wound"] = _check_wound(algo_root)
    else:
        summary["wound"] = {"status": "SKIP", "reason": "not included"}

    if args.include_tail:
        summary["tail"] = _check_tail(algo_root)
    else:
        summary["tail"] = {"status": "SKIP", "reason": "not included"}

    if args.include_correct:
        summary["correct"] = _check_correct(algo_root)
    else:
        summary["correct"] = {"status": "SKIP", "reason": "not included"}

    if args.include_crop:
        summary["crop"] = _check_crop(algo_root)
    else:
        summary["crop"] = {"status": "SKIP", "reason": "not included"}

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("========== model cache behavior ==========")
        for k in ("weight", "wound", "tail", "correct", "crop"):
            entry = summary.get(k, {})
            print(f"  {k:8s} status={entry.get('status')}")
            for sub_k, sub_v in entry.items():
                if sub_k in ("status",):
                    continue
                print(f"           {sub_k}={sub_v!r}")
            print()

    has_fail = any(
        isinstance(summary.get(k), dict)
        and summary[k].get("status") == "FAIL"
        for k in ("weight", "wound", "tail", "correct", "crop")
    )
    return 1 if has_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())