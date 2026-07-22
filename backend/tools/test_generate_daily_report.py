"""端到端测试：用当天数据生成日报（docx + PDF）。

不依赖前端；直接调 backend service。

Usage:
    /path/to/.venv_algo/bin/python backend/tools/test_generate_daily_report.py \
        --date 2026-06-26

    # 默认日期 = 今天
    /path/to/.venv_algo/bin/python backend/tools/test_generate_daily_report.py
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

# 解决 import 路径
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(description="生成鱼品质检测日报（端到端）")
    parser.add_argument(
        "--date",
        type=str,
        default=date.today().isoformat(),
        help="报告日期 YYYY-MM-DD（默认今天）",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="生成完成后删掉生成的 docx/pdf（用于 CI）",
    )
    args = parser.parse_args()

    # 数据库连接（脚本里用 SQLAlchemy）
    db_url = os.environ.get(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/fishdb"
    )
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(db_url)
    SessionLocal = sessionmaker(bind=engine)

    # 需要把 app.main 的 startup 跑一遍来补字段
    from app.repositories.startup_migrations import (
        run_detection_report_conclusion_migration,
        run_fish_extra_metric_migration,
        run_frame_detection_tables_migration,
        run_report_tables_migration,
    )

    run_frame_detection_tables_migration()
    run_fish_extra_metric_migration()
    run_report_tables_migration()
    run_detection_report_conclusion_migration()

    from app.services.report_pdf_service import generate_daily_fish_quality_pdf

    report_date = date.fromisoformat(args.date)
    print(f"=== 生成日报：{report_date.isoformat()} ===")
    print(f"db: {db_url.split('@')[-1]}")

    db = SessionLocal()
    try:
        # 1) 简单 summary
        from app.services.report_stats_service import get_fish_quality_summary

        summary = get_fish_quality_summary(db, report_date=args.date)
        print(f"当天统计：total_fish={summary.get('total_fish')}, "
              f"abnormal={summary.get('abnormal_fish')}, "
              f"surface_damage_fish={summary.get('surface_damage_fish_total')}")
        if summary.get("total_fish", 0) <= 0:
            print("[WARN] 当日无数据，PDF 也能生成但内容空。")

        # 2) 生成
        result = generate_daily_fish_quality_pdf(
            db, report_date=report_date, summary=summary
        )

        print()
        print("=== 生成结果 ===")
        for k, v in result.items():
            if k == "summary_json":
                continue
            if isinstance(v, str) and len(v) > 200:
                v = v[:200] + "..."
            print(f"  {k}: {v}")

        # 3) 校验 docx 中无 {{xxx}} 残留
        from app.services.report_docx_service import check_placeholder_leftover
        from app.paths import DATA_DIR

        docx_path = Path(result["docx_path"])
        leftover = check_placeholder_leftover(docx_path)
        print()
        print("=== 占位符残留检查 ===")
        print(f"  残留数: {len(leftover)}")
        if leftover:
            for x in leftover[:20]:
                print(f"    {x}")
        else:
            print("  ✅ 全部占位符已被替换")

        # 4) 校验 PDF 存在
        print()
        print("=== 文件存在性 ===")
        for k in ("docx_path", "pdf_path"):
            p = result.get(k)
            if not p:
                print(f"  {k}: None")
                continue
            pp = Path(p)
            print(f"  {k}: {pp}  exists={pp.exists()}  size={pp.stat().st_size if pp.exists() else 0}")

        # 5) CI cleanup
        if args.cleanup:
            docx_path.unlink(missing_ok=True)
            pdf_path = result.get("pdf_path")
            if pdf_path:
                Path(pdf_path).unlink(missing_ok=True)
            print()
            print("[cleanup] 生成文件已删除")

        return 0 if not leftover else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
