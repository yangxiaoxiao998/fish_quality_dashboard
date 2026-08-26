from __future__ import annotations

import argparse
import sys

from app.services.update.upgrade_manager import service


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a standalone software update task.")
    parser.add_argument("task_id", help="Task identifier created by Upgrade Manager")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        service.run_task(args.task_id)
    except Exception as exc:
        print(f"[runner][error] {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
