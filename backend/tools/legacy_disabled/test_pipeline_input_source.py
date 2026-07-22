"""
test_pipeline_input_source.py - pipeline 输入源选择器最小验证脚本

输入：
    --video-id    必填或自动从 batch_input_service 推断
    --source      original | perspective | corrected
    --limit       最多打印多少条 item

行为：
    - 调用 algo_facade.resolve_pipeline_input_root + collect_video_images。
    - 打印 requested_source / actual_source / input_root / count / 前 N 条 rel_path。
    - 不连数据库、不调用重模型、不入库。
    - 不写绝对路径：路径从 DATA_DIR 计算。

用法：
    python tools/test_pipeline_input_source.py --source perspective --limit 5
    python tools/test_pipeline_input_source.py --source original --video-id 20260624_0001_592d984a45a0
    python tools/test_pipeline_input_source.py --source corrected
    python tools/test_pipeline_input_source.py --source perspective --video-id 99999999_9999_deadbeef9999
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Resolve and inspect pipeline input source for a given video_id."
    )
    p.add_argument(
        "--video-id",
        dest="video_id",
        default=None,
        help="target video_id. If omitted, picks the first from list_video_ids(source).",
    )
    p.add_argument(
        "--source",
        dest="source",
        default=None,
        choices=[None, "original", "perspective", "corrected"],
        help=(
            "explicit pipeline input source. "
            "If omitted, uses algo_facade default (settings.pipeline_input_source or 'perspective')."
        ),
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

    from app.paths import DATA_DIR
    from app.services.batch_input_service import (
        collect_video_images,
        list_video_ids,
    )
    from app.services.algo_facade import (
        count_pipeline_input_images,
        resolve_pipeline_input_root,
    )

    requested_source = args.source

    video_id = args.video_id
    if not video_id:
        probe_source = requested_source or "perspective"
        ids = list_video_ids(source=probe_source)
        if not ids:
            summary = {
                "video_id": None,
                "requested_source": requested_source,
                "actual_source": None,
                "input_root": None,
                "image_count": 0,
                "first_items": [],
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        video_id = ids[-1]

    input_root, requested, actual = resolve_pipeline_input_root(
        video_id,
        source=requested_source,
        data_dir=DATA_DIR,
        fallback_to_original=True,
    )

    image_count = count_pipeline_input_images(input_root)

    items = collect_video_images(
        video_id=video_id,
        source=actual,
        data_dir=DATA_DIR,
        limit=args.limit,
        fallback_to_original=False,
    )

    first_items: list[dict] = []
    for it in items:
        first_items.append(
            {
                "rel_path": it.rel_path,
                "frame_index": it.frame_index,
                "chunk_name": it.chunk_name,
            }
        )

    summary = {
        "video_id": video_id,
        "requested_source": requested,
        "actual_source": actual,
        "input_root": str(input_root),
        "image_count": image_count,
        "first_items": first_items,
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
