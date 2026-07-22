"""
diagnose_backend_start.py

后端启动前诊断脚本。

只做环境检查 / import 检查 / 算法 CLI --help 检查，不调用真实算法 / 真实标定。

诊断项：
  1. sys.executable
  2. sys.path 前 10 项
  3. FISH_PROJECT_ROOT / FISH_ALGO_ROOT / FISH_DATA_ROOT
  4. DATABASE_URL 脱敏
  5. REDIS_URL 脱敏
  6. Redis ping
  7. 数据库 current_database
  8. 关键模块 import：
     - app.main
     - app.workers.worker_main
     - app.services.algorithm_cli_service
     - app.services.algorithm_result_service
     - app.services.algorithm_calibration_capture_service
  9. 算法 CLI --help：
     - algorithms.core.adjust_calibration
     - algorithms.tools.run_video_pipeline

用法：
  PYTHONPATH=backend python backend/tools/diagnose_backend_start.py
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


def _mask_url(url: str | None) -> str:
    if not url:
        return "<not_set>"
    try:
        u = urlparse(url)
        if u.password:
            netloc = u.netloc.replace(f":{u.password}@", ":***@")
            return u._replace(netloc=netloc).geturl()
        return url
    except Exception:
        return "<unparseable>"


def main() -> int:
    print("=" * 60)
    print("diagnose_backend_start")
    print("=" * 60)
    print(f"[1] python          = {sys.executable}")
    print(f"[2] sys.version     = {sys.version.split()[0]}")
    print(f"[3] sys.path (top 10):")
    for p in sys.path[:10]:
        print(f"     - {p}")
    print()

    print("[4] env:")
    print(f"     FISH_PROJECT_ROOT = {os.getenv('FISH_PROJECT_ROOT')}")
    print(f"     FISH_ALGO_ROOT    = {os.getenv('FISH_ALGO_ROOT')}")
    print(f"     FISH_DATA_ROOT    = {os.getenv('FISH_DATA_ROOT')}")
    print(f"     PYTHONPATH (head) = {os.getenv('PYTHONPATH', '')[:120]}")
    print()

    db_url = os.getenv("DATABASE_URL") or ""
    redis_url = os.getenv("REDIS_URL") or ""
    print(f"[5] DATABASE_URL     = {_mask_url(db_url)}")
    print(f"[6] REDIS_URL        = {_mask_url(redis_url)}")
    print()

    # 7. Redis ping
    print("[7] redis ping:")
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(redis_url or "redis://127.0.0.1:6379/0")
        ping = client.ping()
        print(f"     [OK] redis ping = {ping}")
    except Exception as exc:
        print(f"     [FAIL] redis ping failed: {type(exc).__name__}: {exc}")
    print()

    # 8. 数据库连接
    print("[8] database current_database:")
    try:
        from sqlalchemy import text
        from app.repositories.db import engine

        with engine.connect() as conn:
            db = conn.execute(text("select current_database()")).scalar()
        print(f"     [OK] current_database = {db}")
    except Exception as exc:
        print(f"     [FAIL] {type(exc).__name__}: {exc}")
    print()

    # 9. 关键 import
    print("[9] critical imports:")
    mods = [
        "app.main",
        "app.workers.worker_main",
        "app.services.algorithm_cli_service",
        "app.services.algorithm_result_service",
        "app.services.algorithm_calibration_capture_service",
    ]
    failed = False
    for m in mods:
        try:
            __import__(m, fromlist=["*"])
            print(f"     [OK] {m}")
        except Exception as exc:
            failed = True
            print(f"     [FAIL] {m}: {type(exc).__name__}: {exc}")
    print()

    # 10. 算法 CLI --help
    print("[10] algorithm CLI --help:")
    py = sys.executable
    for cli in [
        "algorithms.core.adjust_calibration",
        "algorithms.tools.run_video_pipeline",
    ]:
        try:
            r = subprocess.run(
                [py, "-m", cli, "--help"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if r.returncode == 0 and "--source-image" in (r.stdout + r.stderr) or r.returncode == 0 and "--input-video" in (r.stdout + r.stderr):
                print(f"     [OK] python -m {cli} --help")
            elif r.returncode == 0:
                print(f"     [OK] python -m {cli} --help (returncode=0)")
            else:
                print(f"     [FAIL] python -m {cli} --help rc={r.returncode}")
                print(f"            stderr: {r.stderr[:200]}")
                failed = True
        except Exception as exc:
            print(f"     [FAIL] {cli}: {type(exc).__name__}: {exc}")
            failed = True
    print()

    print("=" * 60)
    if failed:
        print("diagnose_backend_start: FAIL")
        return 1
    print("diagnose_backend_start: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
