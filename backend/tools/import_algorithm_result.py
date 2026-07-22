"""
import_algorithm_result.py - 把已有 data/results/<video_id>/ 导入数据库

用法：
    python tools/import_algorithm_result.py --video-id video_xxx
    python tools/import_algorithm_result.py --path data/results/<video_id>/reports/pipeline_result.json

注意：本工具不跑算法；只是把已经存在的 pipeline_result.json 写入数据库。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(description="Import pipeline_result.json into DB")
    parser.add_argument("--video-id", default=None, help="video_id（默认从 path 中推断）")
    parser.add_argument(
        "--path",
        default=None,
        help="pipeline_result.json 绝对路径或相对路径（不传 --video-id 时必填）",
    )
    parser.add_argument("--no-commit", action="store_true", help="不自动 commit")
    args = parser.parse_args()

    if not args.video_id and not args.path:
        print("[ERROR] 必须传 --video-id 或 --path")
        return 2

    from app.repositories.db import SessionLocal
    from app.services import algorithm_result_service
    from app.services.video_report_pdf_service import generate_video_pdf_report

    db = SessionLocal()
    try:
        if args.path:
            payload = algorithm_result_service.load_pipeline_result_from_path(args.path)
            result = algorithm_result_service.import_pipeline_result_payload_to_db(
                db, payload, commit=not args.no_commit,
            )
        else:
            result = algorithm_result_service.import_pipeline_result_to_db(
                db, args.video_id, commit=not args.no_commit,
            )

        # 导入成功后自动补一次 DOCX/PDF 报表（失败不影响主流程）
        try:
            if bool(result.get("ok")) and result.get("video_id"):
                report_res = generate_video_pdf_report(result["video_id"])
                result["report_result"] = report_res
                print(
                    f"[INFO] generate report: ok={report_res.get('ok')} "
                    f"docx={report_res.get('report_docx')!r} "
                    f"pdf={report_res.get('report_pdf')!r}"
                )
        except Exception as exc:
            print(f"[WARN] generate report failed: {type(exc).__name__}: {exc}")

        print("=" * 60)
        print("import_algorithm_result")
        print("=" * 60)
        for k, v in result.items():
            print(f"  {k} = {v}")
        print("=" * 60)
        return 0
    except Exception as exc:
        print(f"[ERROR] import failed: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
