"""
diagnose_rq_worker.py - RQ worker 诊断脚本

用途：
    判断 RQ worker 是否存活、队列是否有任务、任务是否失败、
    worker log 最新内容是什么。

参数：
    --queue       默认 fish
    --tail-lines  默认 160

兼容 rq==2.10.0 / redis-py 标准 API。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from redis import Redis
from rq import Queue, Worker
from rq.registry import (
    DeferredJobRegistry,
    FailedJobRegistry,
    ScheduledJobRegistry,
    StartedJobRegistry,
)

PROJECT_ROOT = Path(
    os.getenv("FISH_PROJECT_ROOT", Path(__file__).absolute().parents[2])
).expanduser().absolute()
DATA_DIR = Path(
    os.getenv("FISH_DATA_ROOT", PROJECT_ROOT / "data")
).expanduser().absolute()


def tail_file(path: Path, lines: int) -> str:
    if not path.exists():
        return f"[WARN] log not found: {path}"
    try:
        data = path.read_text(errors="replace").splitlines()
    except Exception as e:
        return f"[WARN] cannot read log: {path}, error={e!r}"
    return "\n".join(data[-lines:])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", default="fish")
    parser.add_argument("--tail-lines", type=int, default=160)
    args = parser.parse_args()

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    print("========== RQ diagnostics ==========")
    print("PROJECT_ROOT       =", PROJECT_ROOT)
    print("PROJECT_ROOT_REAL  =", PROJECT_ROOT.resolve())
    print("DATA_DIR           =", DATA_DIR)
    print("DATA_DIR_REAL      =", DATA_DIR.resolve())
    print("REDIS_URL          =", redis_url)
    print("QUEUE              =", args.queue)

    try:
        conn = Redis.from_url(redis_url)
        print("[redis] ping =", conn.ping())
    except Exception as e:
        print("[ERROR] redis connection failed:", repr(e))
        return 2

    try:
        q = Queue(args.queue, connection=conn)
    except Exception as e:
        print("[ERROR] cannot create queue:", repr(e))
        return 3

    print("\n========== queue ==========")
    try:
        print("queue.name  =", q.name)
        print("queue.count =", q.count)
    except Exception as e:
        print("[WARN] cannot read queue.count:", repr(e))

    try:
        failed = FailedJobRegistry(args.queue, connection=conn)
        started = StartedJobRegistry(args.queue, connection=conn)
        deferred = DeferredJobRegistry(args.queue, connection=conn)
        scheduled = ScheduledJobRegistry(args.queue, connection=conn)

        print("\n========== registries ==========")
        print("failed.count   =", len(failed))
        print("started.count  =", len(started))
        print("deferred.count =", len(deferred))
        print("scheduled.count=", len(scheduled))
    except Exception as e:
        print("[WARN] cannot list registries:", repr(e))
        failed = None

    print("\n========== workers ==========")
    try:
        workers = Worker.all(connection=conn)
        print("worker.count =", len(workers))
        for w in workers:
            try:
                queues = [qq.name for qq in w.queues]
            except Exception:
                queues = "<unreadable>"
            try:
                state = w.get_state()
            except Exception:
                state = "<unreadable>"
            try:
                name = w.name
            except Exception:
                name = "<unreadable>"
            print(f"- name={name} state={state} queues={queues}")
    except Exception as e:
        print("[WARN] cannot list workers:", repr(e))

    print("\n========== failed jobs ==========")
    if failed is None:
        print("(registry unavailable)")
    else:
        try:
            ids = list(failed.get_job_ids())[:5]
            if not ids:
                print("(none)")
            for job_id in ids:
                try:
                    job = q.fetch_job(job_id)
                except Exception as e:
                    print(f"- cannot fetch job {job_id}: {e!r}")
                    continue
                if job is None:
                    print("- missing job", job_id)
                    continue
                print("-" * 60)
                print("job.id      =", job.id)
                print("func_name   =", job.func_name)
                print("created_at  =", job.created_at)
                print("ended_at    =", job.ended_at)
                exc = job.exc_info or ""
                print("exc_info    =")
                print(exc[:3000])
        except Exception as e:
            print("[WARN] cannot inspect failed jobs:", repr(e))

    print("\n========== worker log tail ==========")
    log_path = DATA_DIR / "logs" / "rq_worker.log"
    print(tail_file(log_path, args.tail_lines))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
