"""Inspect/apply legacy PostgreSQL data migrations.

Dry-run is the default.  Use ``--apply`` only after backing up PostgreSQL and
the runtime ``data/`` directory.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _default_report_path() -> Path:
    from app.paths import DATA_DIR

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return DATA_DIR / "logs" / "migrations" / f"legacy_database_{stamp}.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect/apply legacy database, path and measurement migrations"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply source_type removal, path normalization and measurement backfill",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="JSON report output path (default: data/logs/migrations/<timestamp>.json)",
    )
    parser.add_argument(
        "--skip-json",
        action="store_true",
        help="skip scanning pipeline_result.json files",
    )
    args = parser.parse_args()

    from app.repositories.path_migrations import run_legacy_database_migration

    report_path = (args.report or _default_report_path()).expanduser()
    try:
        report = run_legacy_database_migration(
            apply=args.apply,
            include_json=not args.skip_json,
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] legacy migration failed: {type(exc).__name__}: {exc}")
        return 1

    database_paths = report.get("database_paths") or {}
    measurements = report.get("measurements") or {}
    print(f"[INFO] mode={'apply' if args.apply else 'dry-run'}")
    print(f"[INFO] report={report_path}")
    print(f"[INFO] source_type={report.get('source_type')}")
    print(f"[INFO] database paths converted={len(database_paths.get('converted', []))}")
    print(f"[INFO] database paths missing={len(database_paths.get('missing_files', []))}")
    print(f"[INFO] database paths out_of_bounds={len(database_paths.get('out_of_bounds', []))}")
    print(f"[INFO] measurement backfills={len(measurements.get('backfilled', []))}")
    print(f"[INFO] measurement differences={len(measurements.get('differences', []))}")
    print(f"[INFO] summary mismatches={len(report.get('summary_mismatches') or [])}")
    print(f"[INFO] orphan groups={report.get('orphans')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
