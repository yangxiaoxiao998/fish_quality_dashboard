"""
debug_pipeline.py - 端到端调试脚本

不修改数据库。仅走完全部算法链路，输出每一步计数：

    frame_count   - 抽帧出来的图片数
    detect_count  - 模拟检测框数量（累计所有帧）
    track_count   - 跟踪后唯一 track 数
    fish_count    - 唯一 fish 数
    db_insert_count - 模拟入 DB 的 fish 数

用法：

    python tools/debug_pipeline.py <video_path>

或者让脚本自动选择一个 data/uploads/videos/ 下的最新视频：

    python tools/debug_pipeline.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _pick_latest_video() -> Path:
    from app.paths import VID_DIR
    candidates = sorted(
        [p for p in VID_DIR.glob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".avi", ".mkv"}],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"No video files in {VID_DIR}")
    return candidates[0]


def main(argv: list[str]) -> int:
    if len(argv) >= 2:
        video_path = Path(argv[1]).resolve()
    else:
        video_path = _pick_latest_video()

    if not video_path.exists():
        print(f"[ERR] video not found: {video_path}")
        return 1

    print("=" * 60)
    print(f"[debug] video_path = {video_path}")
    print("=" * 60)

    from app.services import algo_facade
    from app.services.tracking_service import get_tracker_stats
    from app.paths import DATA_DIR
    from app.settings import settings

    print(
        f"[debug] settings.max_extract_frames = {settings.max_extract_frames}"
    )
    print(
        f"[debug] settings.max_crop_frames    = {settings.max_crop_frames}"
    )

    video_id = f"debug_{video_path.stem}"

    # 1. 抽帧
    print()
    print("--- step 1: extract_video_frames ---")
    try:
        frame_dir = algo_facade.process_video_frames.__wrapped__ if False else None
    except Exception:
        pass

    from app.services.frame_extract_service import extract_video_frames
    try:
        frame_dir_str = extract_video_frames(
            video_id=video_id,
            video_path=str(video_path),
            output_dir=str(DATA_DIR),
            fps=25,
        )
    except Exception as e:
        print(f"[ERR] extract_video_frames failed: {e!r}")
        return 2

    frame_dir = Path(frame_dir_str)
    frame_count = sum(1 for _ in frame_dir.rglob("*.jpg")) + sum(
        1 for _ in frame_dir.rglob("*.png")
    )
    print(f"[debug] frame_count = {frame_count}")

    # 2. detect + crop + track
    print()
    print("--- step 2: crop_fish_image (detect + crop + track) ---")
    items = algo_facade.crop_fish_image(
        video_id=video_id,
        frame_dir=str(frame_dir),
        output_dir=str(DATA_DIR),
        fps=25,
    )
    detect_count = 0
    track_ids_seen: set[int] = set()
    for it in items:
        bboxes_per_item = 1
        detect_count += bboxes_per_item
        if it.get("track_id") is not None:
            track_ids_seen.add(it["track_id"])

    print(f"[debug] detect_count = {detect_count}")
    print(f"[debug] track_count (unique track_ids) = {len(track_ids_seen)}")
    print(f"[debug] fish_count (crop results) = {len(items)}")

    # 3. 跟踪器统计
    print()
    print("--- step 3: tracker stats ---")
    st = get_tracker_stats(video_id)
    print(f"[debug] {st}")

    # 4. build TrackItem (实际入库前一步)
    print()
    print("--- step 4: build TrackItem ---")
    tracks = algo_facade.process_video_frames(
        video_id=video_id,
        video_path=str(video_path),
        output_dir=str(DATA_DIR),
        fps=25,
    )
    print(f"[debug] TrackItem count = {len(tracks)}")
    if tracks:
        print(f"[debug] sample TrackItem[0]:")
        t = tracks[0]
        for k, v in t.__dict__.items():
            print(f"    {k}: {v}")

    # 5. 总结
    print()
    print("=" * 60)
    print(" SUMMARY")
    print("=" * 60)
    print(f"  frame_count     = {frame_count}")
    print(f"  detect_count    = {detect_count}")
    print(f"  track_count     = {len(track_ids_seen)}")
    print(f"  fish_count      = {len(items)}")
    print(f"  db_insert_count = {len(tracks)} (would be inserted if went through stream_ingest)")
    print()
    print(f"  limits          : max_extract_frames={settings.max_extract_frames} "
          f"max_crop_frames={settings.max_crop_frames}")

    if frame_count == 0:
        print()
        print("[DIAGNOSIS] BREAKAGE AT: extract_video_frames")
        print("  -> cv2.VideoCapture 无法打开视频，或没有保存任何帧。")
        print("  -> 检查视频文件是否损坏、格式是否支持、路径是否正确。")
    elif detect_count == 0 or len(items) == 0:
        print()
        print("[DIAGNOSIS] BREAKAGE AT: crop_fish_image / fish_crop_images")
        print("  -> 帧存在但 crop 阶段没有产出 fish。")
        print("  -> 检查 cv2.imread 是否失败，或 mock 检测是否在所有帧都没生成 bbox。")
    elif len(tracks) == 0:
        print()
        print("[DIAGNOSIS] BREAKAGE AT: process_video_frames -> TrackItem")
        print("  -> crop 有结果但 TrackItem 列表为空。")
        print("  -> 检查 items 是否全是非 dict 类型。")
    else:
        print()
        print("[DIAGNOSIS] 全部链路正常。问题在 stream_ingest 后续流程。")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
