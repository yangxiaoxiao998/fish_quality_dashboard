"""Read-only reconciliation of pipeline JSON, PostgreSQL rows and reports."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile pipeline_result.json, database fish rows and PDF reports"
    )
    parser.add_argument(
        "--video-id",
        action="append",
        default=[],
        help="limit to one or more video_id values (repeat the option)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="JSON report output path",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return exit code 1 when a mismatch is found",
    )
    args = parser.parse_args()

    from app.paths import DATA_DIR
    from app.repositories.db import SessionLocal
    from app.services.result_reconciliation_service import reconcile_results

    db = SessionLocal()
    try:
        report = reconcile_results(db, video_ids=args.video_id or None)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] reconciliation failed: {type(exc).__name__}: {exc}")
        return 1
    finally:
        db.close()

    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    output = args.report or DATA_DIR / "logs" / "migrations" / "result_reconciliation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"[INFO] report={output}")
    print(f"[INFO] ok={report['ok']} total={report['total']} status_counts={report['status_counts']}")
    for item in report["items"]:
        if item["status"] != "ok":
            print(
                f"[WARN] video_id={item['video_id']} status={item['status']} "
                f"issues={[issue.get('code') for issue in item['issues']]}"
            )
    return 1 if args.strict and not report["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
