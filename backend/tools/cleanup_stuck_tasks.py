"""
cleanup_stuck_tasks.py - 清理卡在 queued / processing 状态的 video_task

功能：
    1. 查询 video_task 中 status in ('queued', 'processing') 且
       updated_at 超过指定分钟数未更新的任务。
    2. 默认只打印，不修改。
    3. 支持参数 --apply。
    4. --apply 时将这些任务状态改为 failed。
    5. last_error 写入：
        "task timeout while queued/processing"
       finished_at 设置为当前北京时间。

用法：
    python tools/cleanup_stuck_tasks.py
    python tools/cleanup_stuck_tasks.py --timeout-minutes 30
    python tools/cleanup_stuck_tasks.py --apply
    python tools/cleanup_stuck_tasks.py --timeout-minutes 15 --apply
    python tools/cleanup_stuck_tasks.py --status processing --apply
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _now_beijing():
    from app.utils.time_utils import now_beijing
    return now_beijing()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="清理卡在 queued / processing 状态的 video_task"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际更新数据库；不加则只打印",
    )
    parser.add_argument(
        "--timeout-minutes",
        type=int,
        default=None,
        help="处理超时分钟数，默认使用 settings.stuck_task_timeout_minutes",
    )
    parser.add_argument(
        "--status",
        type=str,
        default="queued,processing",
        help="要清理的状态列表（逗号分隔），默认 queued,processing",
    )
    args = parser.parse_args(argv[1:])

    from app.settings import settings
    from app.repositories.db import SessionLocal
    from app.repositories.models import VideoTask
    from app.utils.time_utils import now_beijing
    from sqlalchemy import select, or_

    timeout_minutes = (
        args.timeout_minutes
        if args.timeout_minutes is not None
        else settings.stuck_task_timeout_minutes
    )
    statuses = [s.strip() for s in args.status.split(",") if s.strip()]
    now = now_beijing()
    cutoff = now - timedelta(minutes=timeout_minutes)

    print("=" * 60)
    print(f"[cleanup_stuck_tasks] mode={'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"[cleanup_stuck_tasks] status filter={statuses}")
    print(f"[cleanup_stuck_tasks] timeout_minutes={timeout_minutes}")
    print(f"[cleanup_stuck_tasks] cutoff (now - {timeout_minutes}min) = {cutoff.isoformat()}")
    print("=" * 60)

    db = SessionLocal()
    try:
        conditions = [
            VideoTask.status == s for s in statuses
        ]
        if len(conditions) == 1:
            status_filter = conditions[0]
        else:
            status_filter = or_(*conditions)
        stmt = select(VideoTask).where(
            status_filter,
            VideoTask.updated_at < cutoff,
        )
        tasks = db.execute(stmt).scalars().all()

        if not tasks:
            print(
                f"[cleanup_stuck_tasks] 没有卡住的 {statuses} 任务。"
            )
            return 0

        print(f"[cleanup_stuck_tasks] 找到 {len(tasks)} 个卡住任务：\n")
        print(
            f"{'video_id':<32} {'status':<10} {'updated_at':<26} {'age':>10}"
        )
        print("-" * 100)
        for t in tasks:
            age_min = (now - t.updated_at).total_seconds() / 60 if t.updated_at else 0
            updated = t.updated_at.isoformat() if t.updated_at else "-"
            print(
                f"{t.video_id:<32} {t.status:<10} {updated:<26} {age_min:>9.1f}m"
            )

        if not args.apply:
            print()
            print(
                f"[cleanup_stuck_tasks] DRY-RUN 完成。共 {len(tasks)} 个任务待清理。"
                f" 加 --apply 实际执行。"
            )
            return 0

        print()
        print(f"[cleanup_stuck_tasks] 开始更新 {len(tasks)} 个任务 -> failed ...")
        for t in tasks:
            t.status = "failed"
            t.finished_at = now
            t.last_error = "task timeout while queued/processing"
        db.commit()
        print(f"[cleanup_stuck_tasks] 成功更新 {len(tasks)} 个任务为 failed。")
        return 0
    except Exception as e:
        db.rollback()
        print(f"[cleanup_stuck_tasks] ERROR: {e!r}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
