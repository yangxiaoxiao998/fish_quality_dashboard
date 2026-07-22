"""
run_batch_pipeline.py - 生产级 batch runner 的命令行入口

不在该脚本中：
    - 连接数据库
    - 启动 RQ worker
    - 写绝对路径
    - 强依赖 CUDA

用法示例：

    # dry-run：只扫描不跑算法
    python tools/run_batch_pipeline.py --source perspective --dry-run --limit 20

    # 指定 video_id，dry-run
    python tools/run_batch_pipeline.py --video-id 20260624_0003_892c0f12364a \\
        --source perspective --dry-run --limit 20

    # 真实跑 crop 阶段（输出 crop + mask），写报告到默认路径
    python tools/run_batch_pipeline.py --video-id 20260624_0003_892c0f12364a \\
        --source perspective --limit 5

    # 自定义报告路径
    python tools/run_batch_pipeline.py --source perspective --dry-run \\
        --report-path data/logs/batch_pipeline_report.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Batch-run fish pipeline stages (dry-run by default for safety)."
    )

    p.add_argument(
        "--video-id",
        dest="video_id",
        default=None,
        help="target video_id. If omitted, batch_runner will pick all video_ids from list_video_ids().",
    )
    p.add_argument(
        "--source",
        dest="source",
        default="perspective",
        choices=["original", "perspective", "corrected"],
        help="input image source type (default: perspective)",
    )
    p.add_argument(
        "--limit",
        dest="limit",
        type=int,
        default=None,
        help="max number of videos to process (and max images per video when running crop).",
    )
    p.add_argument(
        "--pattern",
        dest="pattern",
        default=None,
        help="optional glob pattern, e.g. '*.jpg'. Default: jpg/jpeg/png/bmp/webp.",
    )
    p.add_argument(
        "--workers",
        dest="workers",
        type=int,
        default=1,
        help="reserved. CPU-only batch_runner always runs serially in this version (default: 1).",
    )
    p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="only scan, do not run any algorithm. Default: false (i.e. runs real crop).",
    )
    p.add_argument(
        "--run-crop",
        dest="run_crop",
        action="store_true",
        default=True,
        help="run crop stage (default: true). Use --no-run-crop to disable.",
    )
    p.add_argument(
        "--no-run-crop",
        dest="run_crop",
        action="store_false",
        help="disable crop stage.",
    )
    p.add_argument(
        "--run-correct",
        dest="run_correct",
        action="store_true",
        default=False,
        help="run correct stage. Reserved interface in this version.",
    )
    p.add_argument(
        "--run-metrics",
        dest="run_metrics",
        action="store_true",
        default=False,
        help="run five-dimension / wound metrics. Reserved interface in this version.",
    )
    p.add_argument(
        "--report-path",
        dest="report_path",
        default="data/logs/batch_pipeline_report.json",
        help="path to write the report JSON (relative to project root or absolute).",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)

    from app.paths import DATA_DIR, PROJECT_ROOT
    from app.services.batch_runner_service import (
        BatchRunnerConfig,
        run_batch,
        write_report,
    )

    config = BatchRunnerConfig(
        source=args.source,
        video_id=args.video_id,
        limit=args.limit,
        pattern=args.pattern,
        workers=args.workers,
        dry_run=args.dry_run,
        run_crop=args.run_crop,
        run_correct=args.run_correct,
        run_metrics=args.run_metrics,
    )

    report = run_batch(config=config, data_dir=DATA_DIR)

    # 默认报告路径是相对 PROJECT_ROOT（与原文档约定一致：data/logs/...）
    report_path_arg = args.report_path
    report_path = Path(report_path_arg)
    if not report_path.is_absolute():
        report_path = PROJECT_ROOT / report_path

    written = write_report(report, str(report_path))
    print(f"[run_batch_pipeline] report written: {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
