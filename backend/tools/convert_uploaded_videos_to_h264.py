"""
convert_uploaded_videos_to_h264.py

一次性批量把 data/uploads/videos/<video_id>.mp4 转码成浏览器可播放的 H.264 副本。

用法：
    PYTHONPATH=backend:algorithms python backend/tools/convert_uploaded_videos_to_h264.py [--video-id <id> ...]

行为：
    1. 扫描 data/uploads/videos/*.mp4（自动跳过 <video_id>_h264.mp4 副本）；
    2. 用 ffprobe 检查 codec；
    3. codec=h264 且 pix_fmt=yuv420p：跳过；
    4. 否则调用 video_playback_service.ensure_browser_playable_video() 生成
       data/uploads/videos/playable/<video_id>_h264.mp4。
    5. 不覆盖原始 mp4v 文件。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 让脚本直接被 `python backend/tools/convert_uploaded_videos_to_h264.py` 调用时
# 也能 import app.* 模块
BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
for p in (str(PROJECT_ROOT / "backend"), str(PROJECT_ROOT / "algorithms")):
    if p not in sys.path:
        sys.path.insert(0, p)


def main():
    parser = argparse.ArgumentParser(description="将历史 mp4v 视频批量转码为 H.264 playable 副本。")
    parser.add_argument(
        "--video-id",
        action="append",
        default=[],
        help="指定只处理某个 video_id；可多次传入；不传则扫描 data/uploads/videos/ 全部。",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重新转码（即使 playable 副本已存在）。",
    )
    args = parser.parse_args()

    from app.services.video_playback_service import (
        batch_ensure_browser_playable,
        ensure_browser_playable_video,
    )

    video_ids = [v for v in args.video_id if v]
    if video_ids:
        converted = 0
        skipped = 0
        failed = 0
        for vid in video_ids:
            r = ensure_browser_playable_video(vid, force_reconvert=args.force)
            if not r.get("ok"):
                failed += 1
                print(f"[fail] {vid}: {r.get('error_message')}")
                continue
            if r.get("converted"):
                converted += 1
                print(f"[convert] {vid} -> {r.get('playable_path')}")
            else:
                skipped += 1
                print(f"[skip] {vid} already playable ({r.get('codec_name')}/{r.get('pix_fmt')})")
        print(
            f"[done] total={len(video_ids)} converted={converted} skipped={skipped} failed={failed}"
        )
        return

    summary = batch_ensure_browser_playable()
    for item in summary.get("items", []):
        if not item.get("ok"):
            print(f"[fail] {item.get('video_id')}: {item.get('error_message')}")
            continue
        if item.get("converted"):
            print(f"[convert] {item.get('video_id')} -> {item.get('playable_path')}")
        else:
            print(
                f"[skip] {item.get('video_id')} already playable "
                f"({item.get('codec_name')}/{item.get('pix_fmt')})"
            )
    print(
        f"[done] total={summary.get('total')} converted={summary.get('converted')} "
        f"skipped={summary.get('skipped')} failed={summary.get('failed')}"
    )


if __name__ == "__main__":
    main()