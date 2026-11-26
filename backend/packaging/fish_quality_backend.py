#!/usr/bin/env python3
"""Unified source/PyInstaller entry point for backend runtime processes."""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import runpy
import sys
import traceback
from pathlib import Path


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def _install_root() -> Path:
    configured = os.getenv("FISH_INSTALL_ROOT") or os.getenv("FISH_PROJECT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _configure_runtime() -> Path:
    root = _install_root()
    bundle_root = Path(getattr(sys, "_MEIPASS", root)).resolve()
    os.environ.setdefault("FISH_INSTALL_ROOT", str(root))
    os.environ.setdefault("FISH_PROJECT_ROOT", str(root))
    os.environ.setdefault("FISH_DATA_ROOT", str(root / "data"))
    os.environ.setdefault("FISH_CONFIG_ROOT", str(root / "config"))
    os.environ.setdefault("FISH_ALGO_ROOT", str(root / "algorithms"))
    os.environ.setdefault("DATABASE_URL", "postgresql://fish:fish@127.0.0.1:5432/fishdb")
    os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")

    search_paths = (
        root,
        root / "backend",
        root / "algorithms",
        bundle_root,
        bundle_root / "backend",
        bundle_root / "algorithms",
    )
    for candidate in reversed(search_paths):
        text = str(candidate)
        if text not in sys.path:
            sys.path.insert(0, text)
    return root


def _run_module(module_name: str, module_args: list[str]) -> int:
    old_argv = sys.argv[:]
    try:
        sys.argv = [module_name, *module_args]
        runpy.run_module(module_name, run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        print(exc.code, file=sys.stderr)
        return 1
    finally:
        sys.argv = old_argv
    return 0


def _run_api(host: str, port: int) -> int:
    import uvicorn
    from app.main import app

    uvicorn.run(app, host=host, port=port, reload=False)
    return 0


def _run_worker() -> int:
    from app.workers.worker_main import main

    main()
    return 0


def _run_upgrade_manager(host: str, port: int) -> int:
    import uvicorn
    from app.services.update.upgrade_manager.app import app

    uvicorn.run(app, host=host, port=port, reload=False)
    return 0


def _doctor(root: Path, *, deep: bool = False) -> int:
    from app.paths import DATA_DIR
    from app.runtime_paths import ALGORITHMS_ROOT, CONFIG_ROOT, RESOURCE_ROOT

    payload = {
        "ok": True,
        "frozen": _is_frozen(),
        "executable": sys.executable,
        "install_root": str(root),
        "resource_root": str(RESOURCE_ROOT),
        "config_root": str(CONFIG_ROOT),
        "data_root": str(DATA_DIR),
        "algorithms_root": str(ALGORITHMS_ROOT),
        "models_exist": (ALGORITHMS_ROOT / "pth").is_dir(),
        "report_template_exists": (
            RESOURCE_ROOT / "backend" / "app" / "templates" / "report_word_template.docx"
        ).is_file(),
    }
    if deep:
        for candidate in (
            ALGORITHMS_ROOT / "vendor" / "GroundingDINO",
            ALGORITHMS_ROOT / "vendor" / "Grounded-Segment-Anything" / "segment_anything",
        ):
            text = str(candidate)
            if text not in sys.path:
                sys.path.insert(0, text)

        dependency_imports = {
            "groundingdino.transforms": "import groundingdino.datasets.transforms",
            "groundingdino.models": "from groundingdino.models import build_model",
            "groundingdino.slconfig": "from groundingdino.util.slconfig import SLConfig",
            "groundingdino.utils": "from groundingdino.util.utils import clean_state_dict",
            "segment_anything": "from segment_anything import SamPredictor, sam_hq_model_registry, sam_model_registry",
            # These imports load their native libraries immediately.  Keeping
            # them in the deep check catches incomplete PyInstaller bundles
            # before the fish-weight stage reaches production data.
            "xgboost": "from xgboost import XGBRegressor; XGBRegressor(objective='reg:squarederror')",
            "lightgbm": "from lightgbm import LGBMRegressor; LGBMRegressor()",
            "catboost": "from catboost import CatBoostRegressor; CatBoostRegressor(verbose=False)",
        }
        dependency_status: dict[str, dict[str, str | bool]] = {}
        for name, statement in dependency_imports.items():
            try:
                exec(statement, globals())
                dependency_status[name] = {"ok": True}
            except Exception as exc:
                dependency_status[name] = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }
        payload["algorithm_dependencies"] = dependency_status
        payload["algorithm_dependencies_ok"] = all(
            bool(item["ok"]) for item in dependency_status.values()
        )
    payload["ok"] = bool(payload["models_exist"] and payload["report_template_exists"])
    if deep:
        payload["ok"] = bool(payload["ok"] and payload["algorithm_dependencies_ok"])
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="鱼品质检测系统后端运行入口")
    subparsers = parser.add_subparsers(dest="command", required=True)
    api = subparsers.add_parser("api", help="启动 FastAPI 服务")
    api.add_argument("--host", default=os.getenv("BACKEND_HOST", "0.0.0.0"))
    api.add_argument("--port", type=int, default=int(os.getenv("BACKEND_PORT", "8000")))
    subparsers.add_parser("worker", help="启动 RQ Worker")
    manager = subparsers.add_parser("upgrade-manager", help="启动升级管理服务")
    manager.add_argument("--host", default=os.getenv("UPGRADE_MANAGER_HOST", "127.0.0.1"))
    manager.add_argument("--port", type=int, default=int(os.getenv("UPGRADE_MANAGER_PORT", "8012")))
    module = subparsers.add_parser("run-module", help=argparse.SUPPRESS)
    module.add_argument("module_name")
    module.add_argument("module_args", nargs=argparse.REMAINDER)
    runner = subparsers.add_parser("upgrade-runner", help=argparse.SUPPRESS)
    runner.add_argument("task_id")
    doctor = subparsers.add_parser("doctor", help="检查运行目录和资源")
    doctor.add_argument(
        "--deep",
        action="store_true",
        help="同时导入 GroundingDINO、SAM、XGBoost 等算法依赖",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    root = _configure_runtime()
    args = _parser().parse_args(argv)
    if args.command == "api":
        return _run_api(args.host, args.port)
    if args.command == "worker":
        return _run_worker()
    if args.command == "upgrade-manager":
        return _run_upgrade_manager(args.host, args.port)
    if args.command == "run-module":
        return _run_module(args.module_name, args.module_args)
    if args.command == "upgrade-runner":
        from app.services.update.upgrade_manager.runner import main as runner_main
        return int(runner_main([args.task_id]))
    if args.command == "doctor":
        return _doctor(root, deep=bool(args.deep))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
