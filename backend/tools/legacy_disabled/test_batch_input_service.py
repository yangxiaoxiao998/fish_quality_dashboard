"""
test_batch_input_service.py - batch_input_service 最小验证脚本

不在该脚本中：
    - 连接数据库
    - 调用算法模型
    - 写绝对路径

只做：
    - 扫描指定 source 下指定 video_id 的图片
    - 打印 video_id、source、图片数量、前 5 条 rel_path / frame_index / chunk_name

用法：
    python tools/test_batch_input_service.py --source perspective --limit 5
    python tools/test_batch_input_service.py --video-id 20260624_0003_892c0f12364a --source perspective
    python tools/test_batch_input_service.py --source original
    python tools/test_batch_input_service.py --source corrected --limit 10
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
        description="Scan images for a given video_id/source via batch_input_service."
    )
    p.add_argument(
        "--video-id",
        dest="video_id",
        default=None,
        help="target video_id (YYYYMMDD_NNNN_xxxx). If omitted, will use the first id from list_video_ids().",
    )
    p.add_argument(
        "--source",
        dest="source",
        default="perspective",
        choices=["original", "perspective", "corrected"],
        help="image source type (default: perspective)",
    )
    p.add_argument(
        "--limit",
        dest="limit",
        type=int,
        default=5,
        help="max number of items to display (default: 5)",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)

    from app.services.batch_input_service import (
        collect_video_images,
        list_video_ids,
    )

    video_id = args.video_id
    if not video_id:
        ids = list_video_ids(source=args.source)
        if not ids:
            print(f"[INFO] no video ids found for source={args.source!r}")
            print("video_id  = <none>")
            print("source    =", args.source)
            print("count     = 0")
            return 0
        video_id = ids[-1]

    items = collect_video_images(
        video_id=video_id,
        source=args.source,
        limit=args.limit,
        fallback_to_original=True,
    )

    print("video_id  =", video_id)
    print("source    =", args.source)
    print("count     =", len(items))

    show_n = min(args.limit, len(items)) if args.limit and args.limit > 0 else len(items)
    for it in items[:show_n]:
        print(
            "  -",
            f"rel_path={it.rel_path}",
            f"frame_index={it.frame_index}",
            f"chunk_name={it.chunk_name}",
        )

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
