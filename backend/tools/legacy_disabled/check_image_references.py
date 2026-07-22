#!/usr/bin/env python
# backend/tools/check_image_references.py
#
# 用途：
#   扫描 Fish 表中所有图片相关字段（original_image_path / crop_image_path /
#   corrected_image_path / mask_image_path / wound_mask_path / wound_annotated_path
#   等），把它们转成绝对路径，检查文件是否还存在。
#
#   - 不删除任何东西
#   - 不改数据库
#   - 仅做诊断输出
#
# 输出：
#   - 各字段引用数（数据库里有多少条记录有这个字段）
#   - 文件存在的数量
#   - 文件缺失的数量
#   - 缺失的前 N 条 fish_id + 字段名 + 路径（默认前 20 条）
#
# 用法：
#   python backend/tools/check_image_references.py
#   python backend/tools/check_image_references.py --limit 50
#   python backend/tools/check_image_references.py --json
#
# 退出码：
#   0 = 所有引用都存在
#   1 = 有缺失文件
#   2 = DB 连接失败 / 无法加载 model

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

# 允许直接 python tools/check_image_references.py
# 把 backend/ 加到 sys.path
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# 各字段：fish model 中与图像/掩码相关的 path 列
# （必须与 backend/app/repositories/models.py 的 Fish 模型字段对齐）
IMAGE_FIELDS = [
    "original_image_path",
    "corrected_image_path",
    "mask_image_path",
    "wound_mask_path",
]


def _to_abs(rel_path: str | Path | None) -> Path | None:
    """数据库里存的是 data 相对路径，转成 DATA_DIR 下的绝对路径。"""
    if not rel_path:
        return None
    p = Path(rel_path)
    if p.is_absolute():
        return p
    try:
        from app.paths import DATA_DIR
        return (DATA_DIR / rel_path).resolve()
    except Exception:
        return None


def _iter_records() -> Iterable[Tuple[str, Dict[str, Any]]]:
    """yield (fish_id, {field: value, ...})。"""
    from app.repositories.db import get_db
    from app.repositories.models import Fish
    from sqlalchemy import select

    db_gen = get_db()
    db = next(db_gen)
    try:
        # 直接 fetch 整条 ORM 对象，避免 Text 列 + select(...) unpack 触发 SQLAlchemy 2.0 strict warning。
        rows = db.execute(select(Fish)).scalars().all()
        for f in rows:
            rec = {"fish_id": f.fish_id}
            for field in IMAGE_FIELDS:
                rec[field] = getattr(f, field, None)
            yield rec["fish_id"], rec
    finally:
        try:
            next(db_gen)
        except StopIteration:
            pass


def _gather_stats(
    records: Iterable[Tuple[str, Dict[str, Any]]],
    limit_missing_print: int = 20,
) -> Dict[str, Any]:
    stats: Dict[str, Any] = {
        "fields": {},
        "total_records": 0,
        "missing_examples": [],
    }

    for field in IMAGE_FIELDS:
        stats["fields"][field] = {
            "referenced": 0,
            "exists": 0,
            "missing": 0,
        }

    for fish_id, rec in records:
        stats["total_records"] += 1
        for field in IMAGE_FIELDS:
            value = rec.get(field)
            if not value:
                continue
            stats["fields"][field]["referenced"] += 1
            abs_path = _to_abs(value)
            if abs_path and abs_path.exists():
                stats["fields"][field]["exists"] += 1
            else:
                stats["fields"][field]["missing"] += 1
                if len(stats["missing_examples"]) < limit_missing_print:
                    stats["missing_examples"].append(
                        {
                            "fish_id": fish_id,
                            "field": field,
                            "rel_path": value,
                            "abs_path": str(abs_path) if abs_path else None,
                        }
                    )

    return stats


def _print_human(stats: Dict[str, Any]) -> None:
    print("=" * 70)
    print(" fish_quality_dashboard 图片引用检查")
    print("=" * 70)
    print(f" scanned records: {stats['total_records']}")
    print()
    print(
        f"{'字段':<32}{'引用':>8}{'存在':>8}{'缺失':>8}"
    )
    print("-" * 60)
    total_missing = 0
    for field, s in stats["fields"].items():
        print(
            f"{field:<32}{s['referenced']:>8}{s['exists']:>8}{s['missing']:>8}"
        )
        total_missing += s["missing"]
    print("-" * 60)
    print(f" {'TOTAL missing':<30}{'':>8}{'':>8}{total_missing:>8}")
    print()

    if stats["missing_examples"]:
        print(f" 缺失示例（最多展示 {len(stats['missing_examples'])} 条）:")
        for ex in stats["missing_examples"]:
            print(
                f"  - fish_id={ex['fish_id']}  field={ex['field']}  "
                f"rel={ex['rel_path']!r}"
            )
            print(
                f"      abs={ex['abs_path']}"
            )
    else:
        print(" 没有任何缺失文件。")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="扫描数据库中 fish 表的图片引用，对比磁盘文件是否存在"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="缺失示例最多展示多少条（默认 20）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 格式（机器可读）",
    )
    args = parser.parse_args()

    try:
        records = list(_iter_records())
    except Exception as exc:
        print(f"[ERROR] 无法连接数据库 / 加载 model: {exc}", file=sys.stderr)
        return 2

    stats = _gather_stats(records, limit_missing_print=args.limit)

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        _print_human(stats)

    total_missing = sum(s["missing"] for s in stats["fields"].values())
    return 1 if total_missing > 0 else 0


if __name__ == "__main__":
    sys.exit(main())