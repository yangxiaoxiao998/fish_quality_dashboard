"""Classify black-background fish crops with the local ViT model.

Crop/mask pairs from ``first_match`` are classified into ``complete_match`` or
``incomplete_match``.  Complete fish retain the existing direction-correction
workflow before they are saved.

With ``--force-all-complete``, the ViT classification is skipped.  Every
processable crop/mask pair still follows the complete-fish direction-correction
workflow and is saved to ``complete_match``.
"""

from __future__ import annotations

import argparse
import math
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Sequence, TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型检查，运行时延后加载
    import cv2  # noqa: F401


DEFAULT_COMPLETE_THRESHOLD = 0.9


def validate_complete_threshold(value: float | str) -> float:
    """Validate and normalize the completeness score threshold.

    The ViT returns probabilities in ``[0, 1]``.  Keeping this validation in
    the filtering module lets both its CLI and the top-level pipeline share
    exactly the same threshold contract.
    """

    try:
        threshold = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"--complete-threshold must be a number in [0, 1], got {value!r}"
        ) from error
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ValueError(
            f"--complete-threshold must be in [0, 1], got {threshold!r}"
        )
    return threshold


def is_complete_score(
    complete_score: float,
    threshold: float = DEFAULT_COMPLETE_THRESHOLD,
) -> bool:
    """Return whether a complete-class probability passes the threshold."""

    threshold = validate_complete_threshold(threshold)
    try:
        score = float(complete_score)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"complete score must be a number in [0, 1], got {complete_score!r}"
        ) from error
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError(
            f"complete score must be in [0, 1], got {score!r}"
        )
    return score >= threshold


def classify_completeness_result(
    result,
    complete_threshold: float = DEFAULT_COMPLETE_THRESHOLD,
) -> tuple[bool, str, float]:
    """Apply the business threshold to one raw ViT result.

    ``result.label``/``result.is_complete`` describe the model's argmax.  The
    filtering policy is intentionally evaluated from the raw complete score so
    a high-confidence requirement can override that argmax without changing
    the model inference contract.
    """

    complete_score = float(result.scores["complete"])
    is_complete = is_complete_score(complete_score, complete_threshold)
    final_label = "complete" if is_complete else "incomplete"
    return is_complete, final_label, complete_score


def _cv2():
    import cv2  # type: ignore

    return cv2

THIS_DIR = Path(__file__).resolve().parent
ALGORITHMS_ROOT = THIS_DIR.parent
if str(ALGORITHMS_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS_ROOT))

from config.naming import (  # noqa: E402  pylint: disable=wrong-import-position
    FISH_CROP_SUFFIX,
    FISH_MASK_SUFFIX,
    make_fish_crop_name,
    make_fish_mask_name,
    parse_fish_file_name,
)
from config.paths import (  # noqa: E402  pylint: disable=wrong-import-position
    ALGORITHMS_DENGCHAO_DIR,
    PROJECT_ROOT,
    ensure_dir,
    to_project_relative,
    video_complete_match_dir,
    video_first_match_dir,
    video_incomplete_match_dir,
)


if str(ALGORITHMS_DENGCHAO_DIR) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS_DENGCHAO_DIR))


def _import_processing_functions():
    """Import orientation and ViT helpers only when processing starts."""

    from algorithms.core.merge_funs import (  # type: ignore[import-not-found]  # noqa: WPS433
        DEFAULT_ANGLE_THRESHOLD,
        DEFAULT_MIN_AREA,
        DEFAULT_THRESHOLD,
        compute_belly_orientation,
        compute_head_orientation,
        estimate_head_half_angle,
        estimate_level_angle,
        get_fish_mask,
        rotate_bound,
    )
    from algorithms.core.vit_completeness import get_default_classifier  # type: ignore[import-not-found]  # noqa: WPS433
    return {
        "DEFAULT_ANGLE_THRESHOLD": DEFAULT_ANGLE_THRESHOLD,
        "DEFAULT_MIN_AREA": DEFAULT_MIN_AREA,
        "DEFAULT_THRESHOLD": DEFAULT_THRESHOLD,
        "compute_belly_orientation": compute_belly_orientation,
        "compute_head_orientation": compute_head_orientation,
        "estimate_head_half_angle": estimate_head_half_angle,
        "estimate_level_angle": estimate_level_angle,
        "get_fish_mask": get_fish_mask,
        "get_default_classifier": get_default_classifier,
        "rotate_bound": rotate_bound,
    }


def _processing_functions():
    """Return cached orientation helpers and the ViT classifier factory."""

    global DEFAULT_ANGLE_THRESHOLD, DEFAULT_MIN_AREA, DEFAULT_THRESHOLD
    global compute_belly_orientation, compute_head_orientation
    global estimate_head_half_angle, estimate_level_angle, get_default_classifier
    global get_fish_mask, rotate_bound

    if compute_belly_orientation is None:
        loaded = _import_processing_functions()
        for name, value in loaded.items():
            globals()[name] = value
    return {
        "DEFAULT_ANGLE_THRESHOLD": DEFAULT_ANGLE_THRESHOLD,
        "DEFAULT_MIN_AREA": DEFAULT_MIN_AREA,
        "DEFAULT_THRESHOLD": DEFAULT_THRESHOLD,
        "compute_belly_orientation": compute_belly_orientation,
        "compute_head_orientation": compute_head_orientation,
        "estimate_head_half_angle": estimate_head_half_angle,
        "estimate_level_angle": estimate_level_angle,
        "get_fish_mask": get_fish_mask,
        "get_default_classifier": get_default_classifier,
        "rotate_bound": rotate_bound,
    }


# Lazy-import placeholders populated by _processing_functions().
DEFAULT_ANGLE_THRESHOLD = None  # type: ignore
DEFAULT_MIN_AREA = None  # type: ignore
DEFAULT_THRESHOLD = None  # type: ignore
compute_belly_orientation = None  # type: ignore
compute_head_orientation = None  # type: ignore
estimate_head_half_angle = None  # type: ignore
estimate_level_angle = None  # type: ignore
get_fish_mask = None  # type: ignore
get_default_classifier = None  # type: ignore
rotate_bound = None  # type: ignore


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------


def _resolve_project_relative(path: str | Path) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


def _log_path(p: Path) -> str:
    return to_project_relative(p)


def resolve_dirs(args) -> tuple[Path, Path, Path]:
    """根据 ``--video-id`` 或显式参数解析 ``(input, complete, incomplete)`` 三个目录。

    - ``--video-id`` 给定时：所有目录按 ``data/jobs/<video-id>/crop_images/...`` 自动推导。
    - 否则：使用 ``--input-dir``、``--complete-output-dir``、``--incomplete-output-dir``。
      其中 ``--output-dir`` 是 ``--complete-output-dir`` 的兼容别名；
      ``--complete-dir``/``--incomplete-dir`` 同样保留兼容。
    """

    video_id = getattr(args, "video_id", None)

    if video_id:
        input_dir = (
            _resolve_project_relative(args.input_dir)
            if getattr(args, "input_dir", None)
            else video_first_match_dir(video_id)
        )
        complete_dir = video_complete_match_dir(video_id)
        incomplete_dir = video_incomplete_match_dir(video_id)
        return input_dir, complete_dir, incomplete_dir

    input_dir = (
        _resolve_project_relative(args.input_dir)
        if getattr(args, "input_dir", None)
        else None
    )
    if input_dir is None:
        raise SystemExit(
            "请指定 --input-dir 或 --video-id（同时也需要 --video-id 自动推导 input 目录）。"
        )

    complete_dir = (
        _resolve_project_relative(args.complete_output_dir)
        if getattr(args, "complete_output_dir", None)
        else (
            _resolve_project_relative(args.output_dir)
            if getattr(args, "output_dir", None)
            else _resolve_project_relative(getattr(args, "complete_dir", None))
            if getattr(args, "complete_dir", None)
            else input_dir.parent / "complete_match"
        )
    )
    incomplete_dir = (
        _resolve_project_relative(args.incomplete_output_dir)
        if getattr(args, "incomplete_output_dir", None)
        else (
            _resolve_project_relative(getattr(args, "incomplete_dir", None))
            if getattr(args, "incomplete_dir", None)
            else input_dir.parent / "incomplete_match"
        )
    )

    return input_dir, complete_dir, incomplete_dir


# ---------------------------------------------------------------------------
# 输入扫描 / 配对
# ---------------------------------------------------------------------------


def iter_crop_mask_pairs(input_dir: Path):
    """扫描 ``input_dir`` 返回 ``(stem, crop_path, mask_path)``。

    兼容 6 位 / 12 位：只要文件名满足 ``id_<track>_<frame>_(crop|mask_crop_bw).{jpg,png}`` 即可。
    输出文件名始终统一为 12 位 ``id_<track>_<012d>_crop.jpg``。
    """

    pairs: dict[tuple[int, int], dict[str, Path]] = {}
    if not input_dir.exists():
        return
    for entry in input_dir.iterdir():
        if not entry.is_file():
            continue
        info = parse_fish_file_name(entry.name)
        if info is None:
            continue
        key = (info.track_id, info.frame_index)
        bucket = pairs.setdefault(key, {})
        if info.kind == "crop":
            bucket["crop"] = entry
        elif info.kind == "mask":
            bucket["mask"] = entry
    for (track_id, frame_index), parts in sorted(pairs.items()):
        crop = parts.get("crop")
        mask = parts.get("mask")
        if crop is None or mask is None:
            continue
        canonical_stem = f"id_{track_id}_{frame_index:012d}"
        yield canonical_stem, crop, mask, track_id, frame_index


def read_crop_mask_images(crop_path: Path, mask_path: Path):
    crop_image = _cv2().imread(str(crop_path), _cv2().IMREAD_COLOR)
    if crop_image is None:
        raise ValueError(f"cannot read crop image: {crop_path}")

    mask_image = _cv2().imread(str(mask_path), _cv2().IMREAD_GRAYSCALE)
    if mask_image is None:
        raise ValueError(f"cannot read mask image: {mask_path}")

    if crop_image.shape[:2] != mask_image.shape[:2]:
        raise ValueError(
            f"image/mask size mismatch: {crop_path.name} {crop_image.shape[:2]} "
            f"vs {mask_path.name} {mask_image.shape[:2]}"
        )

    _, binary_mask = _cv2().threshold(mask_image, 127, 255, _cv2().THRESH_BINARY)
    return crop_image, binary_mask


def make_black_background_fish_from_images(crop_image, binary_mask):
    return _cv2().bitwise_and(crop_image, crop_image, mask=binary_mask)


def binarize_mask(mask_image):
    _, binary_mask = _cv2().threshold(mask_image, 1, 255, _cv2().THRESH_BINARY)
    return binary_mask


def rotate_aligned_images(black_background_fish, crop_image, mask_image, angle: float,
                        rotate_bound_fn):
    cv2 = _cv2()
    rotated_fish = rotate_bound_fn(black_background_fish, angle, interpolation=cv2.INTER_LINEAR)
    rotated_crop = rotate_bound_fn(crop_image, angle, interpolation=cv2.INTER_LINEAR)
    rotated_mask = rotate_bound_fn(mask_image, angle, interpolation=cv2.INTER_NEAREST)
    return rotated_fish, rotated_crop, binarize_mask(rotated_mask)


def crop_aligned_images(black_background_fish, crop_image, mask_image, get_fish_mask_fn):
    cv2 = _cv2()
    fish_mask = get_fish_mask_fn(black_background_fish, threshold=DEFAULT_THRESHOLD)
    fish_mask = binarize_mask(fish_mask)
    if cv2.countNonZero(fish_mask) == 0:
        raise ValueError("no fish pixels found after rotation")

    x, y, width, height = cv2.boundingRect(fish_mask)
    return (
        black_background_fish[y:y + height, x:x + width],
        crop_image[y:y + height, x:x + width],
        mask_image[y:y + height, x:x + width],
    )


def align_complete_pair(crop_image, mask_image, mf: dict):
    cv2 = _cv2()
    black_background_fish = make_black_background_fish_from_images(crop_image, mask_image)

    level_angle = mf["estimate_level_angle"](
        black_background_fish, threshold=DEFAULT_THRESHOLD, min_area=DEFAULT_MIN_AREA,
    )
    black_background_fish, crop_image, mask_image = rotate_aligned_images(
        black_background_fish, crop_image, mask_image, level_angle,
        rotate_bound_fn=mf["rotate_bound"],
    )
    black_background_fish, crop_image, mask_image = crop_aligned_images(
        black_background_fish, crop_image, mask_image,
        get_fish_mask_fn=mf["get_fish_mask"],
    )

    (_left, _right, _score, _can_judge, head_position, _head_direction,
     _reason, _confidence) = mf["compute_head_orientation"](
        black_background_fish, threshold=DEFAULT_THRESHOLD
    )

    head_level_angle = 0.0
    head_level_rotated = False
    if head_position != "不确定":
        head_level_angle = mf["estimate_head_half_angle"](
            black_background_fish,
            head_position=head_position,
            threshold=DEFAULT_THRESHOLD,
            min_area=DEFAULT_MIN_AREA,
        )
        if abs(head_level_angle) > DEFAULT_ANGLE_THRESHOLD:
            black_background_fish, crop_image, mask_image = rotate_aligned_images(
                black_background_fish, crop_image, mask_image, head_level_angle,
                rotate_bound_fn=mf["rotate_bound"],
            )
            head_level_rotated = True

    belly_position, _scores = mf["compute_belly_orientation"](black_background_fish)
    belly_flipped = False
    if belly_position == "top":
        crop_image = cv2.flip(crop_image, 0)
        mask_image = cv2.flip(mask_image, 0)
        belly_flipped = True

    return (
        crop_image,
        binarize_mask(mask_image),
        level_angle,
        head_position,
        head_level_angle,
        head_level_rotated,
        belly_position,
        belly_flipped,
    )


def save_aligned_pair(track_id: int, frame_index: int,
                      crop_image, mask_image,
                      output_dir: Path) -> None:
    ensure_dir(output_dir)
    crop_name = make_fish_crop_name(track_id, frame_index)
    mask_name = make_fish_mask_name(track_id, frame_index)
    _cv2().imwrite(str(output_dir / crop_name), crop_image)
    _cv2().imwrite(str(output_dir / mask_name), mask_image)


def save_incomplete_pair(crop_path: Path, mask_path: Path,
                         track_id: int, frame_index: int,
                         output_dir: Path) -> None:
    """不完整鱼原样拷贝，文件名统一 12 位编号。"""

    ensure_dir(output_dir)
    crop_image = _cv2().imread(str(crop_path))
    mask_image = _cv2().imread(str(mask_path), _cv2().IMREAD_GRAYSCALE)
    if crop_image is None:
        raise ValueError(f"cannot read crop image: {crop_path}")
    if mask_image is None:
        raise ValueError(f"cannot read mask image: {mask_path}")
    crop_name = make_fish_crop_name(track_id, frame_index)
    mask_name = make_fish_mask_name(track_id, frame_index)
    _cv2().imwrite(str(output_dir / crop_name), crop_image)
    _cv2().imwrite(str(output_dir / mask_name), mask_image)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def process_directory(
    input_dir: Path,
    complete_dir: Path,
    incomplete_dir: Path,
    *,
    force_all_complete: bool = False,
    complete_threshold: float = DEFAULT_COMPLETE_THRESHOLD,
) -> int:
    complete_threshold = validate_complete_threshold(complete_threshold)
    if not input_dir.is_dir():
        print(f"[ERROR] 输入目录不存在：{_log_path(input_dir)}")
        return 1

    ensure_dir(complete_dir)
    ensure_dir(incomplete_dir)

    # Direction-correction helpers are needed in both modes.  The ViT model is
    # deliberately initialized only for the normal completeness-classification
    # path.
    try:
        mf = _processing_functions()
    except Exception as exc:
        print(f"[ERROR] failed to initialize fish orientation helpers: {exc}")
        return 1

    classifier = None
    if force_all_complete:
        print(
            "[INFO] 跳过完整性 ViT 判断：所有可正常处理的分割鱼按完整鱼处理，"
            "并保留方向校正。"
        )
    else:
        # Initialise the classifier once per stage.  A resource/configuration
        # error must fail the stage rather than skip every fish below.
        try:
            classifier = mf["get_default_classifier"]()
        except Exception as exc:
            print(f"[ERROR] failed to initialize ViT completeness classifier: {exc}")
            return 1

    total = 0
    complete_count = 0
    incomplete_count = 0
    skipped_count = 0

    temp_dir_context = (
        nullcontext(None)
        if force_all_complete
        else tempfile.TemporaryDirectory(prefix="fish_complete_")
    )
    with temp_dir_context as temp_dir_name:
        temp_dir = Path(temp_dir_name) if temp_dir_name is not None else None

        for stem, crop_path, mask_path, track_id, frame_index in iter_crop_mask_pairs(input_dir):
            total += 1
            if not mask_path.is_file():
                skipped_count += 1
                print(f"[skip] {stem}: missing mask {mask_path.name}")
                continue
            try:
                crop_image, mask_image = read_crop_mask_images(crop_path, mask_path)
                if force_all_complete:
                    is_complete = True
                    label = "forced_complete"
                    model_label = None
                    complete_score = None
                    confidence = None
                else:
                    black_background_fish = make_black_background_fish_from_images(
                        crop_image, mask_image
                    )
                    temp_image_path = temp_dir / f"{stem}_black_bg.png"
                    _cv2().imwrite(str(temp_image_path), black_background_fish)
                    result = classifier.predict_path(temp_image_path)
                    is_complete, label, complete_score = classify_completeness_result(
                        result,
                        complete_threshold,
                    )
                    model_label = result.label
                    confidence = result.confidence

                if is_complete:
                    (
                        aligned_crop,
                        aligned_mask,
                        level_angle,
                        head_position,
                        head_level_angle,
                        head_level_rotated,
                        belly_position,
                        belly_flipped,
                    ) = align_complete_pair(crop_image, mask_image, mf)
                    save_aligned_pair(track_id, frame_index,
                                       aligned_crop, aligned_mask, complete_dir)
                else:
                    save_incomplete_pair(crop_path, mask_path,
                                         track_id, frame_index,
                                         incomplete_dir)
            except Exception as exc:
                skipped_count += 1
                print(f"[skip] {stem}: {exc}")
                continue

            if is_complete:
                complete_count += 1
                action = (
                    "aligned"
                    f", level_angle={level_angle:+.4f}"
                    f", head_position={head_position}"
                    f", head_level_angle={head_level_angle:+.4f}"
                    f", head_level_rotated={head_level_rotated}"
                    f", belly={belly_position}"
                    f", flipped={belly_flipped}"
                )
            else:
                incomplete_count += 1
                action = "saved to incomplete"

            if confidence is None:
                print(f"[{label}] {stem}: completeness classification skipped, {action}")
            else:
                print(
                    f"[{label}] {stem}: model_label={model_label}, "
                    f"complete_score={complete_score:.6f}, "
                    f"threshold={complete_threshold:.6f}, "
                    f"model_confidence={confidence:.6f}, {action}"
                )

    print()
    print("==== filter_complete_fish 完成 ====")
    print(f"total       : {total}")
    print(f"complete    : {complete_count}")
    print(f"incomplete  : {incomplete_count}")
    print(f"skipped     : {skipped_count}")
    print(f"complete_dir   : {_log_path(complete_dir)}")
    print(f"incomplete_dir : {_log_path(incomplete_dir)}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="鱼体完整性过滤；完整鱼进 complete_match，不完整鱼进 incomplete_match"
    )
    parser.add_argument("--video-id", default=None,
                        help="视频 ID；传入时自动按 data/jobs/<video-id>/crop_images/{first,complete,incomplete}_match 解析")
    parser.add_argument("--input-dir", dest="input_dir", default=None,
                        help="first_match 输入目录（默认 data/jobs/<video-id>/crop_images/first_match）")

    parser.add_argument("--complete-output-dir", "--complete_output_dir",
                        dest="complete_output_dir", default=None,
                        help="完整鱼输出目录（默认 data/jobs/<video-id>/crop_images/complete_match）")
    parser.add_argument("--incomplete-output-dir", "--incomplete_output_dir",
                        dest="incomplete_output_dir", default=None,
                        help="不完整鱼输出目录（默认 data/jobs/<video-id>/crop_images/incomplete_match）")

    # 兼容旧参数：
    parser.add_argument("--output-dir", dest="output_dir", default=None,
                        help="兼容旧参数：相当于 --complete-output-dir")
    parser.add_argument("--complete-dir", dest="complete_dir", default=None,
                        help="兼容旧参数：相当于 --complete-output-dir")
    parser.add_argument("--incomplete-dir", dest="incomplete_dir", default=None,
                        help="兼容旧参数：相当于 --incomplete-output-dir")
    parser.add_argument(
        "--complete-threshold",
        type=validate_complete_threshold,
        default=DEFAULT_COMPLETE_THRESHOLD,
        metavar="FLOAT",
        help=(
            "完整类别概率阈值；只有 P(complete) >= 此值才归为完整，"
            f"默认 {DEFAULT_COMPLETE_THRESHOLD:.1f}"
        ),
    )
    parser.add_argument(
        "--force-all-complete",
        action="store_true",
        help=(
            "跳过完整性 ViT 判断；所有可正常处理的分割鱼均写入 complete_match，"
            "但仍执行完整鱼方向校正。"
        ),
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        input_dir, complete_dir, incomplete_dir = resolve_dirs(args)
    except SystemExit as exc:
        print(f"[ERROR] {exc}")
        return 2
    return process_directory(
        input_dir,
        complete_dir,
        incomplete_dir,
        force_all_complete=args.force_all_complete,
        complete_threshold=args.complete_threshold,
    )


if __name__ == "__main__":
    raise SystemExit(main())
