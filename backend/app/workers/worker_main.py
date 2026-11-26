"""
worker_main.py - RQ worker 的主入口。

算法统一走 algorithm_cli_service.run_video_pipeline_cli。

启动方式：

    python -m app.workers.worker_main
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _mask_url(url: str) -> str:
    if "@" not in url or "://" not in url:
        return url
    try:
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            creds, host = rest.split("@", 1)
            if ":" in creds:
                user, _ = creds.split(":", 1)
                creds = f"{user}:***"
            return f"{scheme}://{creds}@{host}"
    except Exception:
        return url
    return url


def _git_commit() -> str:
    _project_root = Path(__file__).resolve().parents[3]
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_project_root),
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return out.decode().strip()
    except Exception:
        return "unknown"


def print_diagnostics() -> None:
    print("=" * 60)
    print("[worker] startup diagnostics")
    print("=" * 60)
    print(f"  sys.executable           = {sys.executable}")
    print(f"  sys.path (top 5)         = {sys.path[:5]}")
    print(f"  algorithm_cli_service    = app.services.algorithm_cli_service")
    print(f"  algorithm_result_service = app.services.algorithm_result_service")

    try:
        from app.services import algorithm_cli_service
        print(f"  algorithm_cli_service.__file__ = {algorithm_cli_service.__file__}")
    except Exception as e:
        print(f"  algorithm_cli_service: <FAILED: {e!r}>")

    try:
        from app.services import algorithm_result_service
        print(f"  algorithm_result_service.__file__ = {algorithm_result_service.__file__}")
    except Exception as e:
        print(f"  algorithm_result_service: <FAILED: {e!r}>")

    print(f"  git_commit               = {_git_commit()}")

    try:
        from app.settings import settings
        from app.repositories.db import _current_database

        print(f"  DATABASE_URL             = {_mask_url(settings.database_url)}")
        print(f"  current_database         = {_current_database()}")
    except Exception as e:
        print(f"  DB diagnostics           = <FAILED: {e!r}>")
    print("=" * 60)


def main() -> None:
    print_diagnostics()

    from rq import Worker
    from app.workers.queue import get_queue, DEFAULT_QUEUE_NAME
    from app.infra.redis_client import get_redis
    from app.settings import settings

    try:
        r = get_redis()
        r.ping()
        print(f"[worker] redis OK url={_mask_url(settings.redis_url)}")
    except Exception as e:
        print(f"[worker][FATAL] cannot connect to redis: {e!r}")
        print(f"[worker][FATAL] url={_mask_url(settings.redis_url)}")
        sys.exit(1)

    q = get_queue()
    try:
        existing = q.job_ids
    except Exception as e:
        print(f"[worker][FATAL] cannot read queue {q.name!r}: {e!r}")
        sys.exit(1)
    print(
        f"[worker] queue name={q.name!r} "
        f"redis url={_mask_url(settings.redis_url)} "
        f"existing queued jobs={len(existing)}"
    )

    worker = Worker([q])
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
