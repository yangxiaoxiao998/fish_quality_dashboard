"""视频转帧工具。

把软件录制的视频按帧解码并保存为 12 位编号的 JPEG。

输出文件名形如 ``000000000001.jpg``。OpenCV 内部从 0 起读帧，但
面向输出时仍从 1 开始命名，因此第一张输出图永远是 ``000000000001.jpg``。

读取、排序、解析时兼容旧的 6 位编号（``000001.jpg``）与新 12 位编号。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

THIS_DIR = Path(__file__).resolve().parent
ALGORITHMS_ROOT = THIS_DIR.parent
if str(ALGORITHMS_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS_ROOT))

from config.naming import (  # noqa: E402  pylint: disable=wrong-import-position
    make_frame_name,
)
from config.paths import (  # noqa: E402  pylint: disable=wrong-import-position
    PROJECT_ROOT,
    ensure_dir,
    resolve_input_path,
    to_project_relative,
    video_frames_raw_dir,
)


def _resolve_project_relative(path: str | Path | None) -> Path | None:
    """``config.paths.resolve_input_path`` 的本地别名。"""

    return resolve_input_path(path)


def _resolve_input_video(cli_value: str | None) -> Path:
    """解析输入视频路径；必填项 ``--input-video``。"""

    if not cli_value:
        raise FileNotFoundError("未指定 --input-video（必填参数）。")
    resolved = resolve_input_path(cli_value)
    if resolved is None:
        raise FileNotFoundError(f"--input-video 路径无效：{cli_value!r}")
    return resolved


def _resolve_output_dir(cli_value: str | None, video_id: str | None) -> Path:
    resolved = resolve_input_path(cli_value)
    if resolved is not None:
        return resolved
    if not video_id:
        raise ValueError("必须传入 --video-id 或显式 --output-dir。")
    return video_frames_raw_dir(video_id).resolve()


def _log_path(p: Path) -> str:
    """日志输出时尽量展示相对项目根目录的路径。"""

    return to_project_relative(p)


def _resolve_frame_range(
    total_frames: int,
    frame_start: int | None,
    frame_end: int | None,
) -> tuple[int, int]:
    """把用户给定的 1-based 起止帧转换成 OpenCV 的 0-based 索引区间。"""

    start_user = frame_start if frame_start is not None else 1
    end_user = frame_end if frame_end is not None else total_frames

    if start_user < 1:
        raise ValueError(f"--frame-start 必须从 1 开始，收到 {start_user}")
    if end_user < start_user:
        raise ValueError(
            f"--frame-end ({end_user}) 必须大于等于 --frame-start ({start_user})"
        )
    if start_user > total_frames:
        raise ValueError(
            f"--frame-start={start_user} 超过视频总帧数 {total_frames}"
        )

    end_user = min(end_user, total_frames)
    return start_user - 1, end_user  # 转成 0-based 左闭右闭


def extract_frames(
    input_video: Path,
    output_dir: Path,
    frame_start: int | None = None,
    frame_end: int | None = None,
    overwrite: bool = False,
) -> dict:
    """从视频里抽帧并写入 ``output_dir``。

    返回一个汇总字典，包含 ``frame_count``、``first_frame``、``last_frame`` 等信息。
    """

    input_video = Path(input_video).expanduser().resolve()
    if not input_video.exists():
        raise FileNotFoundError(
            f"输入视频不存在：{_log_path(input_video)}\n"
            f"绝对路径：{input_video}"
        )

    output_dir = Path(output_dir).expanduser().resolve()

    if output_dir.exists():
        if not overwrite:
            raise FileExistsError(
                f"输出目录已存在：{_log_path(output_dir)}。"
                "如需重新抽帧请加上 --overwrite；"
                "否则请指定空目录或删除现有数据。"
            )
        # overwrite 时清空现有内容，但保留目录本身
        for child in output_dir.iterdir():
            if child.is_dir() and not child.is_symlink():
                import shutil

                shutil.rmtree(child)
            else:
                try:
                    child.unlink()
                except FileNotFoundError:
                    continue

    ensure_dir(output_dir)

    import cv2  # 懒加载

    capture = cv2.VideoCapture(str(input_video))
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f"无法打开视频：{_log_path(input_video)}")

    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 0.0
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))

    if total_frames <= 0:
        # OpenCV 在某些 mp4 上拿不到准确的总帧数，需要 fallback。
        # 这里采用逐帧读取直到 EOF。
        total_frames = _count_frames(capture)
        capture.release()
        capture = cv2.VideoCapture(str(input_video))
        if not capture.isOpened():
            raise RuntimeError(f"无法重新打开视频：{_log_path(input_video)}")

    cap_start, cap_end = _resolve_frame_range(total_frames, frame_start, frame_end)

    written = 0
    first_frame_name: str | None = None
    last_frame_name: str | None = None
    frame_index = 1  # 面向输出文件名的 1-based 编号

    capture.set(cv2.CAP_PROP_POS_FRAMES, cap_start)
    for cap_idx in range(cap_start, cap_end):
        ok, frame = capture.read()
        if not ok or frame is None:
            print(f"[WARN] 第 {cap_idx + 1} 帧读取失败，提前结束。")
            break

        # 输出帧编号从 1 开始，永不输出 000000000000.jpg
        file_name = make_frame_name(frame_index)
        target_path = output_dir / file_name
        cv2.imwrite(str(target_path), frame)

        if first_frame_name is None:
            first_frame_name = file_name
        last_frame_name = file_name

        written += 1
        frame_index += 1

        if written % 100 == 0:
            print(
                f"进度：已写入 {written} 帧（{_log_path(output_dir)}）"
            )

    capture.release()

    summary = {
        "input_video": input_video,
        "output_dir": output_dir,
        "frame_count": written,
        "first_frame": first_frame_name,
        "last_frame": last_frame_name,
        "total_frames_in_video": total_frames,
        "fps": fps,
        "image_width": width,
        "image_height": height,
    }
    return summary


def _count_frames(capture) -> int:
    """逐帧读取以估算视频总帧数（OpenCV 对某些编码拿不到 ``CAP_PROP_FRAME_COUNT``）。"""

    count = 0
    while True:
        ok, _ = capture.read()
        if not ok:
            break
        count += 1
    return count


def _print_summary(video_id: str | None, summary: dict) -> None:
    print()
    print("==== video_to_frames 完成 ====")
    if video_id:
        print(f"video_id      : {video_id}")
    print(f"input_video   : {_log_path(summary['input_video'])}")
    print(f"output_dir    : {_log_path(summary['output_dir'])}")
    print(f"frame_count   : {summary['frame_count']}")
    if summary.get("first_frame"):
        print(f"first_frame   : {summary['first_frame']}")
    if summary.get("last_frame"):
        print(f"last_frame    : {summary['last_frame']}")
    if summary.get("image_width") and summary.get("image_height"):
        print(
            f"resolution    : {summary['image_width']}x{summary['image_height']}"
        )
    if summary.get("fps"):
        print(f"fps           : {summary['fps']:.3f}")
    print("================================")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把软件录制的视频按帧解码为 12 位编号 JPEG 图片"
    )
    parser.add_argument("--video-id", required=True, help="视频 ID（必填）")
    parser.add_argument(
        "--input-video",
        required=True,
        help=(
            "输入视频路径（必填，支持绝对路径或相对于项目根目录的相对路径）。"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="输出目录；不传则默认 data/jobs/<video-id>/frames/raw/",
    )
    parser.add_argument(
        "--frame-start",
        type=int,
        default=None,
        help="起始帧（1-based，默认从第一帧开始）",
    )
    parser.add_argument(
        "--frame-end",
        type=int,
        default=None,
        help="结束帧（1-based，包含；默认到最后一帧）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="输出目录已存在时是否覆盖；不传则保留原数据并报错",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)

    try:
        input_video = _resolve_input_video(args.input_video)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        return 2

    try:
        output_dir = _resolve_output_dir(args.output_dir, args.video_id)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        return 2

    try:
        summary = extract_frames(
            input_video=input_video,
            output_dir=output_dir,
            frame_start=args.frame_start,
            frame_end=args.frame_end,
            overwrite=args.overwrite,
        )
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}")
        return 2

    _print_summary(args.video_id, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())