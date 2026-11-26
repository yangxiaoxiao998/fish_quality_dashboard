"""一键 pipeline 编排脚本。

按以下顺序串联各算法子模块：

1. ``calibrate``（可选）：如果传入了 ``--calibration-source-image``，
   先用 ``core.adjust calibrate`` 重新生成
   ``data/calibration/current_calibration.json``；否则直接使用
   ``--calibration-json`` 或默认的 ``data/calibration/current_calibration.json``。
2. ``tools.video_to_frames``：把输入视频抽帧到 ``frames/raw/``。
3. ``core.adjust batch``：把 raw 帧矫正到 ``frames/adjusted/``，按需删除 raw。
4. ``core.grounded_sam_mmdet_batch_fix``：检测 + 分割 + 跟踪，写 ``annotated_frames/``、
   ``first_match/``、``videos/grounded_sam.mp4``。传送带运动方向和位置边界
   从 ``algorithms/config/adjust_calibration_margin.json`` 读取；缺少方向字段时
   默认按自上而下处理。
5. ``core.filter_complete_fish``：分完整鱼到 ``complete_match/``、不完整鱼到
   ``incomplete_match/``。
6. ``core.fish_condition``：计算含尾全长、体长、体高、体重、肥满度，输出
   ``measurements/long_height_weight_results.csv`` 和测量可视化图片。
7. ``core/arched-back.py``：读取已对齐的完整鱼 crop/mask 对，将
   ``arched_back_ratio`` 回填到上述测量 CSV。
8. ``publish_results``：把上述**长期可交付**产物从 ``data/jobs/<video_id>/``
   发布到 ``data/results/<video_id>/``，使用 ``<video_id>.partial/`` 临时目录。
9. 默认清理 jobs 中间过程（raw / adjusted / annotated_frames / first_match），
   保留测量可视化图片、CSV、JSON、标注视频、complete_match、
   incomplete_match、输入副本。

video_id 规则：

- 如果用户**未传** ``--video-id``，由 ``config.paths.generate_video_id`` 生成
  形如 ``video_YYYYMMDD_HHMMSS_<8位uuid>`` 的 ID。
- 如果用户传入 ``--video-id``，沿用用户传入的值；推荐**不传**。
- jobs 和 results 共享同一个 video_id。

各步骤都通过 Python 函数调用完成（不依赖子进程）。所有路径都来自
``config/paths.py``；CSV / JSON 中的图片路径都是相对项目根目录的 POSIX 形式。
测试阶段允许用户在 CLI 传入绝对路径，但代码内不允许出现硬编码绝对路径。
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import os
import runpy
import shutil
import sys
from pathlib import Path
from typing import Iterable

THIS_DIR = Path(__file__).resolve().parent
ALGORITHMS_ROOT = THIS_DIR.parent
PROJECT_DIR = ALGORITHMS_ROOT.parent

# 该脚本既支持在 ``algorithms/tools/`` 中直接执行，也支持从项目根目录以
# ``python -m`` 启动。前者需要项目根目录才能解析 ``algorithms.*``，而各
# 阶段保留的 ``from config ...`` 导入仍需要 ``algorithms/`` 本身在搜索路径中。
for import_root in (PROJECT_DIR, ALGORITHMS_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from config.cleanup import remove_dir, remove_dir_contents  # noqa: E402
from config.paths import (  # noqa: E402
    ADJUST_CALIBRATION_MARGIN_CONFIG_PATH,
    CALIBRATION_CURRENT_JSON,
    CALIBRATION_SESSIONS_DIR,
    JOBS_DIR,
    PROJECT_ROOT,
    RESULTS_DIR,
    ensure_dir,
    generate_video_id,
    job_dir,
    resolve_input_path,
    result_arched_back_dir,
    result_complete_match_dir,
    result_dir,
    result_error_conclusion_txt,
    result_fish_images_dir,
    result_grounded_sam_video,
    result_incomplete_match_dir,
    result_input_dir,
    result_long_height_csv,
    result_partial_dir,
    result_pipeline_result_json,
    result_reports_dir,
    result_videos_dir,
    to_project_relative,
    video_annotated_frames_dir,
    video_arched_back_dir,
    video_complete_match_dir,
    video_crop_images_dir,
    video_fish_condition_images_dir,
    video_first_match_dir,
    video_frames_adjusted_dir,
    video_pipeline_state_json,
    video_frames_raw_dir,
    video_grounded_sam_video,
    video_incomplete_match_dir,
    video_input_dir,
    video_long_height_csv,
    video_measurements_dir,
    video_pipeline_result_json,
    video_reports_dir,
    video_state_dir,
    video_videos_dir,
)
from config.state import (  # noqa: E402
    PipelineLock,
    PipelineState,
    STATE_DONE,
    STATE_FAILED,
    STATE_INTERRUPTED,
    STATE_PUBLISHING,
    STATE_RUNNING,
    STATE_STAGE_DONE,
    atomic_write_json,
    atomic_write_text,
    now_str,
    scan_interrupted_jobs,
)
from algorithms.core.filter_complete_fish import (  # noqa: E402
    DEFAULT_COMPLETE_THRESHOLD,
    validate_complete_threshold,
)


def _resolve_input(path: str | Path | None) -> Path | None:
    """``config.paths.resolve_input_path`` 的本地别名。"""

    return resolve_input_path(path)


def _log(p: Path) -> str:
    return to_project_relative(p)


def _load_complete_threshold_from_config() -> float | None:
    """从 ``algorithms/config/adjust_calibration_margin.json`` 读取管理员设置的完整性阈值。

    配置文件缺失、字段缺失或数值非法时返回 ``None``，由调用方回退到默认值。
    """

    try:
        if not ADJUST_CALIBRATION_MARGIN_CONFIG_PATH.exists():
            return None
        raw = json.loads(
            ADJUST_CALIBRATION_MARGIN_CONFIG_PATH.read_text(encoding="utf-8")
        )
        value = raw.get("complete_threshold")
        if value is None:
            return None
        return validate_complete_threshold(value)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def resolve_complete_threshold(cli_value: float | None) -> float:
    """解析最终使用的完整性阈值。

    优先级：显式 CLI 参数 > 管理员配置（adjust_calibration_margin.json）> 默认值。
    这样管理员在界面上设置的数值会应用到后续所有视频 pipeline 任务。
    """

    if cli_value is not None:
        return validate_complete_threshold(cli_value)
    configured = _load_complete_threshold_from_config()
    if configured is not None:
        return configured
    return DEFAULT_COMPLETE_THRESHOLD


def _call_submodule_main(module_name: str, argv: list[str]) -> int:
    """通过 ``python -m <module>`` 等价的 Python 函数调用执行子模块。"""

    print(f"[exec] python -m {module_name} {' '.join(argv)}")
    module = __import__(module_name, fromlist=["main"])
    return int(module.main(argv))


def _call_script_main(script_path: Path, argv: list[str]) -> int:
    """调用文件名不适合普通 Python import 的项目脚本。

    ``arched-back.py`` 带连字符，因此通过 ``runpy`` 加载其命名空间，
    再以与其它 pipeline 模块相同的方式调用 ``main(argv)``。
    """

    print(f"[exec] python {script_path} {' '.join(argv)}")
    namespace = runpy.run_path(str(script_path), run_name="_pipeline_arched_back")
    main_func = namespace.get("main")
    if not callable(main_func):
        raise RuntimeError(f"脚本缺少可调用的 main(argv): {script_path}")
    return int(main_func(argv))


# ---------------------------------------------------------------------------
# 故障注入
# ---------------------------------------------------------------------------

FAULT_AFTER_VIDEO_TO_FRAMES = "after_video_to_frames"
FAULT_AFTER_ADJUST_BATCH = "after_adjust_batch"
FAULT_AFTER_GROUNDED_SAM = "after_grounded_sam"
FAULT_AFTER_FILTER_COMPLETE_FISH = "after_filter_complete_fish"
FAULT_BEFORE_FISH_CONDITION_CSV = "before_fish_condition_csv"
FAULT_AFTER_ARCHED_BACK = "after_arched_back"
FAULT_BEFORE_PUBLISH_RESULTS = "before_publish_results"

VALID_FAULT_STAGES: tuple[str, ...] = (
    "none",
    FAULT_AFTER_VIDEO_TO_FRAMES,
    FAULT_AFTER_ADJUST_BATCH,
    FAULT_AFTER_GROUNDED_SAM,
    FAULT_AFTER_FILTER_COMPLETE_FISH,
    FAULT_BEFORE_FISH_CONDITION_CSV,
    FAULT_AFTER_ARCHED_BACK,
    FAULT_BEFORE_PUBLISH_RESULTS,
)


def maybe_simulate_failure(args, stage: str) -> None:
    """仅供内部测试使用。命中 ``--simulate-failure-stage`` 时抛 ``RuntimeError``。"""

    target = getattr(args, "simulate_failure_stage", "none")
    if target == stage:
        raise RuntimeError(f"模拟故障：{stage}")


def _ensure_safe_to_clean(directory: Path, expected_root: Path, label: str) -> bool:
    """确认 ``directory`` 在 ``expected_root`` 内，否则拒绝清理。"""

    try:
        directory.resolve().relative_to(expected_root.resolve())
    except ValueError:
        print(f"[WARN] {label} 目录 {_log(directory)} 不在预期位置 {_log(expected_root)}，拒绝清理。")
        return False
    return True


def step_calibrate(args, ctx: dict) -> int:  # noqa: ARG001  保留兼容旧入口转发
    """已废弃：``--calibration-source-image`` 不再触发 calibrate 子命令。

    软件工程师正式流程请使用：

    .. code-block:: bash

        python -m algorithms.core.adjust_calibration \\
            --source-image /abs/path/calib.png \\
            --camera-id default --device cuda --overwrite

    之后再调用 ``run_video_pipeline``，无需再传 ``--calibration-source-image``。
    """

    print(
        "[WARN] --calibration-source-image 已废弃：pipeline 不再执行标定。"
        "请先运行 ``python -m algorithms.core.adjust_calibration`` 生成 "
        "``data/calibration/current_calibration.json``。"
    )
    return 0


def stage_input_video(
    video_id: str,
    original_input_video: Path,
    calibration_source_image: Path | None = None,
    calibration_json: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """把用户传入的输入视频复制到 ``data/jobs/<video_id>/input/`` 下，并写 ``input_manifest.json``。

    返回任务内副本 ``data/jobs/<video_id>/input/<video_id>_source_video.<ext>`` 的
    **绝对路径**。该副本是后续 ``video_to_frames`` 等阶段的输入。

    约定：

    1. 原始输入视频**不会被删除或修改**。
    2. ``--overwrite`` 允许覆盖已有的任务内副本；否则已存在时报错。
    3. 命名固定：``<video_id>_source_video.<原扩展名>``，原扩展名为小写。
    """

    if original_input_video is None:
        raise FileNotFoundError("未提供 --input-video")

    original_input_video = Path(original_input_video).expanduser().resolve()
    if not original_input_video.is_file():
        raise FileNotFoundError(f"输入视频不存在：{original_input_video}")

    ext = original_input_video.suffix.lower() or ".mp4"
    job_input_dir = video_input_dir(video_id)
    ensure_dir(job_input_dir)
    job_input_video = job_input_dir / f"{video_id}_source_video{ext}"

    if job_input_video.exists():
        if not overwrite:
            raise FileExistsError(
                f"任务内输入视频已存在：{_log(job_input_video)}。"
                "如需重新复制请加上 --overwrite。"
            )
        job_input_video.unlink()

    shutil.copyfile(str(original_input_video), str(job_input_video))
    print(f"[stage] 已复制输入视频：{original_input_video} -> {_log(job_input_video)}")

    _write_input_manifest(
        video_id=video_id,
        original_input_video=original_input_video,
        job_input_video=job_input_video,
        calibration_source_image=calibration_source_image,
        calibration_json=calibration_json,
    )
    return job_input_video


def _write_input_manifest(
    *,
    video_id: str,
    original_input_video: Path,
    job_input_video: Path,
    calibration_source_image: Path | None = None,
    calibration_json: Path | None = None,
) -> Path:
    """写 ``data/jobs/<video_id>/input/input_manifest.json``。"""

    job_input_dir = video_input_dir(video_id)
    ensure_dir(job_input_dir)

    def _serialize(value):
        if value is None:
            return ""
        return _log(Path(value))

    payload = {
        "video_id": video_id,
        "original_input_video": str(Path(original_input_video).expanduser().resolve()),
        "job_input_video": _log(job_input_video),
        "calibration_source_image": (
            str(Path(calibration_source_image).expanduser().resolve())
            if calibration_source_image
            else ""
        ),
        "calibration_json": _serialize(calibration_json),
        "created_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    manifest_path = job_input_dir / "input_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    print(f"[stage] 已写 input_manifest.json：{_log(manifest_path)}")
    return manifest_path


def step_video_to_frames(args, ctx: dict) -> int:
    """``video_to_frames`` 必须读取任务内副本，不直接读取用户传入的原始视频。"""

    staged_video: Path | None = ctx.get("job_input_video")
    if staged_video is None:
        raise RuntimeError(
            "step_video_to_frames 必须在 stage_input_video 之后调用，"
            "ctx['job_input_video'] 未设置。"
        )
    argv = [
        "--video-id", args.video_id,
        "--input-video", str(staged_video),
    ]
    if args.overwrite:
        argv.append("--overwrite")
    if args.frame_start is not None:
        argv.extend(["--frame-start", str(args.frame_start)])
    if args.frame_end is not None:
        argv.extend(["--frame-end", str(args.frame_end)])
    rc = _call_submodule_main("algorithms.tools.video_to_frames", argv)
    maybe_simulate_failure(args, FAULT_AFTER_VIDEO_TO_FRAMES)
    return rc


def step_adjust(args, ctx: dict) -> int:
    """batch 模式矫正 raw → adjusted，并按需清理 raw。"""

    input_dir = video_frames_raw_dir(args.video_id)
    output_dir = video_frames_adjusted_dir(args.video_id)
    calibration_json = (
        args.calibration_json
        if args.calibration_json
        else str(CALIBRATION_CURRENT_JSON)
    )

    argv = [
        "batch",
        "--video-id", args.video_id,
        "--input-dir", str(input_dir),
        "--output-dir", str(output_dir),
        "--calibration-json", str(calibration_json),
    ]
    if args.keep_raw_frames:
        argv.append("--keep-raw-frames")
    rc = _call_submodule_main("algorithms.core.adjust", argv)

    # core.adjust.batch 在默认（非 keep_raw_frames）情况下已经清理 raw；
    # 这里再做一次安全确认 + 日志，绝不触碰视频或标定原图所在目录。
    if not args.keep_raw_frames:
        job_root = job_dir(args.video_id)
        if _ensure_safe_to_clean(input_dir, job_root, "raw 帧"):
            try:
                remove_dir_contents(input_dir)
                print(f"[cleanup] 已清理 raw 帧目录：{_log(input_dir)}")
            except Exception as exc:
                print(f"[WARN] 清理 raw 帧失败：{exc}")
    else:
        print(f"[INFO] 用户要求保留 raw 帧：{_log(input_dir)}")
    maybe_simulate_failure(args, FAULT_AFTER_ADJUST_BATCH)
    return rc


def step_grounded_sam(args, ctx: dict) -> int:
    argv = [
        "--video-id", args.video_id,
        "--input-dir", str(video_frames_adjusted_dir(args.video_id)),
        "--video-fps", str(args.video_fps),
        "--sam-version", args.sam_version,
        "--device", args.device,
    ]
    if args.box_threshold is not None:
        argv.extend(["--box-threshold", str(args.box_threshold)])
    argv.extend(
        [
            "--top-edge-ratio", str(args.top_edge_ratio),
            "--bottom-edge-ratio", str(args.bottom_edge_ratio),
            "--left-edge-ratio", str(args.left_edge_ratio),
            "--right-edge-ratio", str(args.right_edge_ratio),
        ]
    )
    if getattr(args, "grounded_config", None):
        argv.extend(["--config", str(args.grounded_config)])
    if getattr(args, "grounded_checkpoint", None):
        argv.extend(["--grounded-checkpoint", str(args.grounded_checkpoint)])
    if getattr(args, "sam_checkpoint", None):
        argv.extend(["--sam-checkpoint", str(args.sam_checkpoint)])
    if getattr(args, "sam_hq_checkpoint", None):
        argv.extend(["--sam-hq-checkpoint", str(args.sam_hq_checkpoint)])
    if getattr(args, "use_sam_hq", False):
        argv.append("--use-sam-hq")
    if getattr(args, "transform_matrix", None):
        argv.extend(["--transform-matrix", str(args.transform_matrix)])
    # 该 902 修复版使用配套的检测核心；核心会从共享标定边界配置读取
    # conveyor_direction，并将其应用到检测门控和首帧样本选择。
    rc = _call_submodule_main("algorithms.core.grounded_sam_mmdet_batch_fix", argv)
    maybe_simulate_failure(args, FAULT_AFTER_GROUNDED_SAM)
    return rc


def step_filter_complete(args, ctx: dict) -> int:
    complete_threshold = resolve_complete_threshold(
        getattr(args, "complete_threshold", None)
    )
    argv = [
        "--video-id", args.video_id,
        "--complete-threshold", str(complete_threshold),
    ]
    if getattr(args, "force_all_complete", False):
        argv.append("--force-all-complete")
        print("[INFO] 跳过完整性 ViT 判断：所有可正常处理的分割鱼按完整鱼处理。")
    rc = _call_submodule_main("algorithms.core.filter_complete_fish", argv)
    maybe_simulate_failure(args, FAULT_AFTER_FILTER_COMPLETE_FISH)
    return rc


def step_fish_condition(args, ctx: dict) -> int:
    argv = [
        "--video-id", args.video_id,
        "--device", str(args.device),
    ]
    if args.mm_per_pixel is not None:
        argv.extend(["--mm-per-pixel", str(args.mm_per_pixel)])
    if args.keep_measurement_debug_images:
        argv.append("--keep-measurement-debug-images")
    # wound 体表破损计算：默认启用；只有显式传 --disable-wound 才跳过。
    if getattr(args, "disable_wound", False):
        argv.append("--disable-wound")
    if getattr(args, "wound_checkpoint", None):
        argv.extend(["--wound-checkpoint", str(args.wound_checkpoint)])
    if getattr(args, "wound_threshold", None) is not None:
        argv.extend(["--wound-threshold", str(args.wound_threshold)])
    rc = _call_submodule_main("algorithms.core.fish_condition", argv)
    maybe_simulate_failure(args, FAULT_BEFORE_FISH_CONDITION_CSV)
    return rc


def step_arched_back(args, ctx: dict) -> int:  # noqa: ARG001
    """计算完整鱼弓背指标，并回填 fish_condition 生成的 CSV。"""

    script_path = ALGORITHMS_ROOT / "core" / "arched-back.py"
    rc = _call_script_main(script_path, ["--video-id", args.video_id])
    maybe_simulate_failure(args, FAULT_AFTER_ARCHED_BACK)
    return rc


# 正式 pipeline stages：标定（calibrate）已经独立到 adjust_calibration，
# 不再作为 stage。pipeline 只负责抽帧 / 矫正 / 检测分割 / 完整鱼筛选 /
# 鱼体测量 / 弓背指标 / 发布。
# 旧的 "calibrate" 仍保留兼容入口（会立即返回 0），不再参与 stages。
STEPS = [
    ("calibrate", step_calibrate),
    ("video_to_frames", step_video_to_frames),
    ("adjust_batch", step_adjust),
    ("grounded_sam", step_grounded_sam),
    ("filter_complete_fish", step_filter_complete),
    ("fish_condition", step_fish_condition),
    ("arched_back", step_arched_back),
]


def prepare_job_dirs(video_id: str) -> None:
    for d in [
        job_dir(video_id),
        video_input_dir(video_id),
        video_frames_raw_dir(video_id),
        video_frames_adjusted_dir(video_id),
        video_crop_images_dir(video_id),
        video_annotated_frames_dir(video_id),
        video_first_match_dir(video_id),
        video_complete_match_dir(video_id),
        video_incomplete_match_dir(video_id),
        video_videos_dir(video_id),
        video_measurements_dir(video_id),
        video_arched_back_dir(video_id),
        video_fish_condition_images_dir(video_id),
        video_reports_dir(video_id),
    ]:
        ensure_dir(d)


def _read_csv_rows(csv_path: Path) -> list[dict]:
    if not csv_path.is_file():
        return []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        return [dict(row) for row in reader]


def _csv_row_to_fish_record(row: dict, *, video_id: str) -> dict:
    """把 CSV 行转换成 ``pipeline_result.json`` 中的 fish 记录。

    长期字段精简后只保留 CSV 一致的核心字段：
    ``track_id / fish_uid / frame_index / crop_image_path /
    body_length_cm / body_length_no_tail_cm / body_height_cm /
    pred_weight_g / condition_factor / hex_color / surface_damage_ratio /
    arched_back_ratio / status / error_message``。
    完整性由 ``status`` 推断（``complete`` = 完整，``incomplete`` / ``failed`` = 其他）。
    """

    def _maybe_number(value: str) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _maybe_int(value: str) -> int | None:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _rewrite_crop_path_to_results(value: str) -> str:
        if not value:
            return value
        v = str(value)
        if f"data/jobs/{video_id}/crop_images/complete_match/" in v:
            return v.replace(
                f"data/jobs/{video_id}/crop_images/complete_match/",
                f"data/results/{video_id}/fish_images/complete_match/",
            )
        if f"data/jobs/{video_id}/crop_images/incomplete_match/" in v:
            return v.replace(
                f"data/jobs/{video_id}/crop_images/incomplete_match/",
                f"data/results/{video_id}/fish_images/incomplete_match/",
            )
        return v

    # 历史 CSV 使用 done；新 CSV 使用 complete。
    status = (row.get("status") or "").strip().lower()
    if status == "done":
        status = "complete"
    has_full_metrics = status == "complete"

    record = {
        "video_id": row.get("video_id", ""),
        "track_id": _maybe_int(row.get("track_id", "")),
        "fish_uid": row.get("fish_uid", ""),
        "frame_index": _maybe_int(row.get("frame_index", "")),
        "crop_image_path": _rewrite_crop_path_to_results(row.get("crop_image_path", "")),
        "fish_condition_image_path": row.get("fish_condition_image_path", "") or None,
        "arched_back_image_path": row.get("arched_back_image_path", "") or None,
        "body_length_cm": _maybe_number(row.get("body_length_cm", "")) if has_full_metrics else None,
        "body_length_no_tail_cm": _maybe_number(row.get("body_length_no_tail_cm", "")) if has_full_metrics else None,
        "body_height_cm": _maybe_number(row.get("body_height_cm", "")) if has_full_metrics else None,
        "pred_weight_g": _maybe_number(row.get("pred_weight_g", "")) if has_full_metrics else None,
        "condition_factor": _maybe_number(row.get("condition_factor", "")) if has_full_metrics else None,
        "hex_color": row.get("hex_color", "") or None,
        "surface_damage_ratio": _maybe_number(row.get("surface_damage_ratio", "")) if has_full_metrics else None,
        # 弓背计算与 fish_condition 几何测量独立；即使几何测量失败，
        # 只要 arched-back 阶段成功，仍保留该比值。
        "arched_back_ratio": _maybe_number(row.get("arched_back_ratio", "")),
        "status": status or "incomplete",
        "error_message": row.get("error_message", "") or "",
    }
    return record


def build_pipeline_result(
    *,
    video_id: str,
    status: str,
    input_video: Path,
    original_input_video: Path | None = None,
    calibration_source_image: Path | None = None,
    calibration_json: Path | None = None,
    csv_path: Path,
    failed_stage: str | None = None,
    error_message: str = "",
    selected_steps: list[str] | None = None,
    completed_stages: list[str] | None = None,
) -> dict:
    """从 CSV 生成 ``pipeline_result.json`` 内容。

    所有可交付路径都指向 ``data/results/<video_id>/``，原始视频路径保留在
    ``original_input_video`` 字段中。
    """

    payload: dict = {
        "video_id": video_id,
        "status": status,
        "input_video": _log(input_video),
        "original_input_video": (
            str(Path(original_input_video).expanduser().resolve())
            if original_input_video is not None
            else ""
        ),
        "calibration_source_image": _log(calibration_source_image) if calibration_source_image else "",
        "calibration_json": _log(calibration_json) if calibration_json else _log(CALIBRATION_CURRENT_JSON),
        "grounded_video": _log(result_grounded_sam_video(video_id)),
        "measurement_csv": _log(result_long_height_csv(video_id)),
        "fish_condition_visualizations_dir": _log(
            result_fish_images_dir(video_id) / "fish_condition_images"
        ),
        "arched_back_results_dir": _log(result_arched_back_dir(video_id)),
        "complete_match_dir": _log(result_complete_match_dir(video_id)),
        "incomplete_match_dir": _log(result_incomplete_match_dir(video_id)),
        "pipeline_result_path": _log(result_pipeline_result_json(video_id)),
        "error_conclusion": _log(result_error_conclusion_txt(video_id)),
        "summary": {
            "total_fish": 0,
            "complete_fish": 0,
            "incomplete_fish": 0,
            "failed_fish": 0,
            "damaged_fish": 0,
            "avg_surface_damage_ratio": 0.0,
            "max_surface_damage_ratio": 0.0,
        },
        "fish": [],
    }
    if failed_stage:
        payload["failed_stage"] = failed_stage
        payload["error_message"] = error_message
    if completed_stages is not None:
        payload["completed_stages"] = list(completed_stages)
    if selected_steps is not None:
        payload["steps"] = selected_steps

    rows = _read_csv_rows(csv_path)
    if rows:
        payload["fish"] = [_csv_row_to_fish_record(r, video_id=video_id) for r in rows]
        wound_ratios: list[float] = []
        for record in payload["fish"]:
            status_value = (record.get("status") or "").strip()
            if status_value == "complete":
                payload["summary"]["complete_fish"] += 1
                sp = record.get("surface_damage_ratio")
                if isinstance(sp, (int, float)) and sp is not None:
                    wound_ratios.append(float(sp))
                    if sp > 0:
                        payload["summary"]["damaged_fish"] += 1
            elif status_value == "incomplete":
                payload["summary"]["incomplete_fish"] += 1
            elif status_value == "failed":
                payload["summary"]["failed_fish"] += 1
        payload["summary"]["total_fish"] = (
            payload["summary"]["complete_fish"]
            + payload["summary"]["incomplete_fish"]
            + payload["summary"]["failed_fish"]
        )
        if wound_ratios:
            payload["summary"]["avg_surface_damage_ratio"] = round(
                sum(wound_ratios) / len(wound_ratios), 6
            )
            payload["summary"]["max_surface_damage_ratio"] = round(
                max(wound_ratios), 6
            )
    return payload


def write_pipeline_result(payload: dict, video_id: str) -> Path:
    """写 ``data/jobs/<video_id>/reports/pipeline_result.json``（jobs 中间版本）。"""

    report_path = video_pipeline_result_json(video_id)
    ensure_dir(report_path.parent)
    with open(report_path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    return report_path


def write_error_conclusion(video_id: str, payload: dict) -> Path:
    """写 ``data/jobs/<video_id>/reports/error_conclusion.txt``。

    失败时给软件工程师和用户的可读结论；成功时也写一份"成功"结论。
    UTF-8 编码，使用中文标签。
    """

    status = (payload.get("status") or "").strip()
    failed_stage = (payload.get("failed_stage") or "").strip()
    error_message = (payload.get("error_message") or "").strip()
    completed_stages = payload.get("completed_stages") or []
    summary = payload.get("summary") or {}
    results_report = payload.get("pipeline_result_path") or _log(
        result_pipeline_result_json(video_id)
    )
    txt_path = video_reports_dir(video_id) / "error_conclusion.txt"
    ensure_dir(txt_path.parent)

    if status == "failed":
        lines = [
            "任务状态：失败",
            f"视频ID：{video_id}",
            f"失败阶段：{failed_stage or '（未识别）'}",
            f"错误原因：{error_message or '（未提供）'}",
            f"已完成阶段：{', '.join(completed_stages) if completed_stages else '（无）'}",
            (
                f"summary: total={summary.get('total_fish', 0)}, "
                f"complete={summary.get('complete_fish', 0)}, "
                f"incomplete={summary.get('incomplete_fish', 0)}, "
                f"failed={summary.get('failed_fish', 0)}, "
                f"damaged_fish={summary.get('damaged_fish', 0)}, "
                f"avg_surface_damage_ratio={summary.get('avg_surface_damage_ratio', 0)}, "
                f"max_surface_damage_ratio={summary.get('max_surface_damage_ratio', 0)}"
            ),
            (
                "结论：算法处理中断，未生成完整可交付结果，"
                "results/reports/pipeline_result.json 中 status=failed，"
                "不能进入正常统计。"
            ),
            f"结果JSON：{results_report}",
            f"状态文件：data/jobs/{video_id}/state/pipeline_state.json",
            "建议：检查 data/jobs/<video_id>/state/pipeline_state.json 与运行日志后，加 --overwrite 重新运行。",
        ]
    elif status == "done":
        lines = [
            "任务状态：成功",
            f"视频ID：{video_id}",
            "结论：算法处理完成，结果文件已生成。",
            f"结果目录：data/results/{video_id}",
            (
                "summary: "
                f"total={summary.get('total_fish', 0)}, "
                f"complete={summary.get('complete_fish', 0)}, "
                f"incomplete={summary.get('incomplete_fish', 0)}, "
                f"failed={summary.get('failed_fish', 0)}, "
                f"damaged_fish={summary.get('damaged_fish', 0)}, "
                f"avg_surface_damage_ratio={summary.get('avg_surface_damage_ratio', 0)}, "
                f"max_surface_damage_ratio={summary.get('max_surface_damage_ratio', 0)}"
            ),
            f"标注视频：data/results/{video_id}/videos/grounded_sam.mp4",
            f"测量CSV：data/results/{video_id}/measurements/long_height_weight_results.csv",
            f"结果JSON：{results_report}",
        ]
    else:
        lines = [
            f"任务状态：{status or '未知'}",
            f"视频ID：{video_id}",
            f"失败阶段：{failed_stage}",
            f"错误原因：{error_message}",
            f"已完成阶段：{', '.join(completed_stages) if completed_stages else '（无）'}",
            (
                "summary: "
                f"total={summary.get('total_fish', 0)}, "
                f"complete={summary.get('complete_fish', 0)}, "
                f"incomplete={summary.get('incomplete_fish', 0)}, "
                f"failed={summary.get('failed_fish', 0)}"
            ),
            f"结果JSON：{results_report}",
        ]

    atomic_write_text(txt_path, "\n".join(lines) + "\n")
    return txt_path


# ---------------------------------------------------------------------------
# publish_results
# ---------------------------------------------------------------------------


def _safe_copy_file(src: Path, dst: Path) -> None:
    ensure_dir(dst.parent)
    shutil.copyfile(str(src), str(dst))


def _safe_copy_tree(src: Path, dst: Path) -> None:
    ensure_dir(dst)
    for child in src.iterdir():
        target = dst / child.name
        if child.is_dir() and not child.is_symlink():
            _safe_copy_tree(child, target)
        else:
            _safe_copy_file(child, target)


def _rewrite_csv_paths_to_results(
    csv_path: Path,
    video_id: str,
    *,
    fish_condition_images_dir: Path | None = None,
    arched_back_dir: Path | None = None,
) -> None:
    """把 CSV 中指向 ``data/jobs/<video_id>/crop_images/...`` 的路径改写到
    ``data/results/<video_id>/fish_images/...``。

    发布鱼体测量和弓背可视化时，同时按 ``track_id + frame_index``
    将每条鱼的两张代表图路径写入 CSV。只有源图真实存在时才写入，
    不完整鱼或生成失败的记录保持空路径。
    """

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    visualization_fields = (
        "fish_condition_image_path",
        "arched_back_image_path",
    )
    include_visualization_paths = (
        fish_condition_images_dir is not None or arched_back_dir is not None
    )
    if include_visualization_paths:
        for field in visualization_fields:
            if field not in fieldnames:
                fieldnames.append(field)

    old_complete = f"data/jobs/{video_id}/crop_images/complete_match/"
    new_complete = f"data/results/{video_id}/fish_images/complete_match/"
    old_incomplete = f"data/jobs/{video_id}/crop_images/incomplete_match/"
    new_incomplete = f"data/results/{video_id}/fish_images/incomplete_match/"

    def _rewrite(value: str) -> str:
        if not value:
            return value
        v = str(value)
        if old_complete in v:
            return v.replace(old_complete, new_complete)
        if old_incomplete in v:
            return v.replace(old_incomplete, new_incomplete)
        return v

    for row in rows:
        # CSV 精简后只剩 crop_image_path，不再有 mask_image_path 字段。
        for col in ("crop_image_path",):
            if col in row and row[col]:
                row[col] = _rewrite(row[col])

        if not include_visualization_paths:
            continue

        row["fish_condition_image_path"] = ""
        row["arched_back_image_path"] = ""
        try:
            track_id = int(row.get("track_id", ""))
            frame_index = int(row.get("frame_index", ""))
        except (TypeError, ValueError):
            continue

        stem = f"id_{track_id}_{frame_index:012d}"
        if fish_condition_images_dir is not None:
            fish_condition_image = (
                fish_condition_images_dir
                / stem
                / f"{stem}_with_line.jpg"
            )
            if fish_condition_image.is_file():
                row["fish_condition_image_path"] = (
                    f"data/results/{video_id}/fish_images/"
                    f"fish_condition_images/{stem}/{stem}_with_line.jpg"
                )

        if arched_back_dir is not None:
            arched_back_image = (
                arched_back_dir / "overlays" / f"{stem}_overlay.png"
            )
            if arched_back_image.is_file():
                row["arched_back_image_path"] = (
                    f"data/results/{video_id}/fish_images/"
                    f"arched_back/overlays/{stem}_overlay.png"
                )

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _rewrite_pipeline_result_paths(payload: dict, video_id: str) -> dict:
    """把 ``payload`` 中残留的 ``data/jobs/<video_id>/...`` 改写为
    ``data/results/<video_id>/...``，并校正 input_video 指向 results。

    注意：精简后 ``fish[]`` 不再含 ``mask_image_path``，不需要改写。
    """

    import copy

    out = copy.deepcopy(payload)
    job_prefix = f"data/jobs/{video_id}/"
    res_prefix = f"data/results/{video_id}/"

    def _rewrite(value):
        if not value or not isinstance(value, str):
            return value
        if value.startswith(job_prefix):
            return res_prefix + value[len(job_prefix):]
        return value

    for key in (
        "input_video",
        "grounded_video",
        "measurement_csv",
        "arched_back_results_dir",
        "complete_match_dir",
        "incomplete_match_dir",
    ):
        if key in out:
            out[key] = _rewrite(out[key])
    for record in out.get("fish", []) or []:
        for key in ("crop_image_path",):
            if key in record:
                record[key] = _rewrite(record[key])
    return out


def publish_results(
    video_id: str,
    *,
    overwrite: bool = False,
    original_input_video: Path | None = None,
) -> Path:
    """把 jobs 下的可交付产物发布到 ``data/results/<video_id>/``。

    使用 ``data/results/<video_id>.partial/`` 作为临时目录；全部内容复制
    完成后，再 ``rename`` 到正式目录，避免半成品被软件工程师误读。

    约定：

    1. ``data/results/<video_id>.partial/`` 保留用于发布中断时的排查。
    2. ``--overwrite`` 允许删除旧的 ``partial`` 和已存在的 results 目录。
    3. CSV 中的 ``crop_image_path / mask_image_path`` 在 partial 阶段被改写
       为指向 results 下的 fish_images/。
    4. ``pipeline_result.json`` 写两份：
       - ``data/results/<video_id>/reports/pipeline_result.json``（最终交付）；
       - ``data/jobs/<video_id>/reports/pipeline_result.json``（jobs 中间版）。
    5. ``error_conclusion.txt`` 同样双写。
    """

    final_dir = result_dir(video_id)
    partial_dir = result_partial_dir(video_id)

    if partial_dir.exists():
        if not overwrite:
            raise FileExistsError(
                f"发现未完成发布目录：{_log(partial_dir)}，请检查或加 --overwrite 重新发布。"
            )
        remove_dir(partial_dir)
    if final_dir.exists():
        if not overwrite:
            raise FileExistsError(
                f"长期结果目录已存在：{_log(final_dir)}，如需覆盖请加 --overwrite。"
            )
        remove_dir(final_dir)

    ensure_dir(partial_dir)

    # 临时路径辅助：所有最终可交付物都先写到 partial/ 下，
    # 全部完成后 partial_dir.rename(final_dir) 一次性生效。
    def _partial_input_dir() -> Path:
        return partial_dir / "input"

    def _partial_videos_dir() -> Path:
        return partial_dir / "videos"

    def _partial_fish_images_dir() -> Path:
        return partial_dir / "fish_images"

    def _partial_measurements_dir() -> Path:
        return partial_dir / "measurements"

    def _partial_reports_dir() -> Path:
        return partial_dir / "reports"

    # 1. input/ 整个目录复制（含 input_manifest.json 和任务内视频副本）
    src_input = video_input_dir(video_id)
    if src_input.exists():
        _safe_copy_tree(src_input, _partial_input_dir())

    # 2. videos/grounded_sam.mp4
    src_video = video_grounded_sam_video(video_id)
    if src_video.is_file():
        _safe_copy_file(src_video, _partial_videos_dir() / "grounded_sam.mp4")

    # 3. complete_match / incomplete_match：长期只保留 *_crop.jpg。
    # mask、调试图等其它文件**不**长期保留，避免存储冗余。
    for label, src in (
        ("complete_match", video_complete_match_dir(video_id)),
        ("incomplete_match", video_incomplete_match_dir(video_id)),
    ):
        if src.exists():
            dst = _partial_fish_images_dir() / label
            ensure_dir(dst)
            for entry in sorted(src.iterdir()):
                if not entry.is_file():
                    continue
                # 只复制 *_crop.jpg；跳过 mask / 调试 / 中间产物。
                if not entry.name.endswith("_crop.jpg"):
                    continue
                _safe_copy_file(entry, dst / entry.name)

    src_fish_condition_images = video_fish_condition_images_dir(video_id)
    src_arched_back = video_arched_back_dir(video_id)

    # 4. measurements/long_height_weight_results.csv
    src_csv = video_long_height_csv(video_id)
    if src_csv.is_file():
        dst_csv = _partial_measurements_dir() / "long_height_weight_results.csv"
        _safe_copy_file(src_csv, dst_csv)
        # 改写 CSV 内路径
        _rewrite_csv_paths_to_results(
            dst_csv,
            video_id,
            fish_condition_images_dir=src_fish_condition_images,
            arched_back_dir=src_arched_back,
        )

    # 5. 鱼体测量过程和最终标注图，与 complete_match 一起放在 fish_images/。
    if src_fish_condition_images.is_dir():
        _safe_copy_tree(
            src_fish_condition_images,
            _partial_fish_images_dir() / "fish_condition_images",
        )

    # 6. 弓背指标的全部明细、mask 和可视化中间结果。
    if src_arched_back.is_dir():
        _safe_copy_tree(
            src_arched_back,
            _partial_fish_images_dir() / "arched_back",
        )

    # 7. reports/pipeline_result.json（jobs 版已存在；改写路径后写到 partial）
    jobs_report = video_pipeline_result_json(video_id)
    if jobs_report.is_file():
        with open(jobs_report, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
        payload = _rewrite_pipeline_result_paths(payload, video_id)
        # 校正 input_video：指向 partial/input/...（rename 后即 final）
        if original_input_video is not None:
            ext = Path(original_input_video).suffix.lower() or ".mp4"
            payload["input_video"] = _log(
                _partial_input_dir() / f"{video_id}_source_video{ext}"
            )
        # JSON 内的路径字符串还带 ".partial/..."，rename 后再二次改写。
        result_report = _partial_reports_dir() / "pipeline_result.json"
        ensure_dir(result_report.parent)
        with open(result_report, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)

    # 同步更新 jobs/reports/pipeline_result.json（去掉 .partial，前端路径不变）
    _fix_partial_paths_in_jobs_reports(video_id)

    # 8. reports/error_conclusion.txt（如果存在）
    jobs_err = video_reports_dir(video_id) / "error_conclusion.txt"
    if jobs_err.is_file():
        _safe_copy_file(jobs_err, _partial_reports_dir() / "error_conclusion.txt")

    # 注意：不再在 results/<video_id>/calibration/ 复制任何标定副本。
    # 透视矩阵是全局相机标定，软件统一读 data/calibration/current_calibration.json。
    # 历史 sessions/<calibration_id>/ 也仅活在 data/calibration/sessions/。

    # 7. 原子 rename：partial -> final
    partial_dir.rename(final_dir)
    # 8. 修正 partial 阶段写入的 JSON / TXT 中残留的 ".partial/" 字符串
    _strip_partial_in_text_files(final_dir, video_id)
    print(f"[publish] 已发布长期结果：{_log(final_dir)}")
    return final_dir


def _strip_partial_in_text_files(final_dir: Path, video_id: str) -> None:
    """把 final_dir 下的 .json / .txt 中字符串 ``data/results/<video_id>.partial/...``
    改写为 ``data/results/<video_id>/...``，避免 rename 前写下的 partial 字面路径残留。
    """

    partial_root_prefix = f"data/results/{video_id}.partial/"
    new_root_prefix = f"data/results/{video_id}/"

    for path in final_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in (".json", ".txt"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if partial_root_prefix not in text:
            continue
        new_text = text.replace(partial_root_prefix, new_root_prefix)
        try:
            path.write_text(new_text, encoding="utf-8")
        except Exception:
            pass


def _fix_partial_paths_in_jobs_reports(video_id: str) -> None:
    """修正 jobs/reports/pipeline_result.json 中残留的 ``.partial/`` 字符串。"""

    jobs_report = video_pipeline_result_json(video_id)
    if not jobs_report.is_file():
        return
    try:
        text = jobs_report.read_text(encoding="utf-8")
    except Exception:
        return
    partial_root_prefix = f"data/results/{video_id}.partial/"
    new_root_prefix = f"data/results/{video_id}/"
    if partial_root_prefix not in text:
        return
    new_text = text.replace(partial_root_prefix, new_root_prefix)
    try:
        jobs_report.write_text(new_text, encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 默认清理
# ---------------------------------------------------------------------------


def cleanup_intermediate_dirs(args) -> None:
    """按 ``--keep-*`` 开关安全删除 jobs 中间过程目录。

    默认行为：删除 raw / adjusted / annotated_frames / first_match；保留
    fish_condition_images、complete_match、incomplete_match、标注视频、CSV、
    pipeline_result.json、input/。这些保留项会随后被 ``publish_results`` 复制到
    ``data/results/<video_id>/`` 长期目录。
    """

    job_root = job_dir(args.video_id)

    # 1. raw 帧（默认删除，--keep-raw-frames 保留）
    if not args.keep_raw_frames:
        raw_dir = video_frames_raw_dir(args.video_id)
        if _ensure_safe_to_clean(raw_dir, job_root, "raw 帧"):
            remove_dir_contents(raw_dir)
            print(f"[cleanup] 已清理 raw 帧目录：{_log(raw_dir)}")
    else:
        print(f"[keep] raw 帧保留：{_log(video_frames_raw_dir(args.video_id))}")

    # 2. adjusted 帧（默认删除，--keep-adjusted-frames 保留）
    if not args.keep_adjusted_frames:
        adj_dir = video_frames_adjusted_dir(args.video_id)
        if _ensure_safe_to_clean(adj_dir, job_root, "adjusted 帧"):
            remove_dir_contents(adj_dir)
            print(f"[cleanup] 已清理 adjusted 帧目录：{_log(adj_dir)}")
    else:
        print(f"[keep] adjusted 帧保留：{_log(video_frames_adjusted_dir(args.video_id))}")

    # 3. first_match（默认删除，--keep-first-match 保留）
    if not args.keep_first_match:
        fm_dir = video_first_match_dir(args.video_id)
        if _ensure_safe_to_clean(fm_dir, job_root, "first_match"):
            remove_dir_contents(fm_dir)
            print(f"[cleanup] 已清理 first_match 目录：{_log(fm_dir)}")
    else:
        print(f"[keep] first_match 保留：{_log(video_first_match_dir(args.video_id))}")

    # 4. annotated_frames（默认删除，--keep-annotated-frames 保留）
    if not args.keep_annotated_frames:
        af_dir = video_annotated_frames_dir(args.video_id)
        if _ensure_safe_to_clean(af_dir, job_root, "annotated_frames"):
            remove_dir_contents(af_dir)
            print(f"[cleanup] 已清理 annotated_frames 目录：{_log(af_dir)}")
    else:
        print(f"[keep] annotated_frames 保留：{_log(video_annotated_frames_dir(args.video_id))}")

    # 5. measurements/fish_condition_images：默认保留，方便复核全长/体长/体高。
    if not args.keep_measurement_debug_images:
        fc_dir = video_fish_condition_images_dir(args.video_id)
        expected_root = video_measurements_dir(args.video_id)
        if _ensure_safe_to_clean(fc_dir, expected_root, "fish_condition_images"):
            remove_dir_contents(fc_dir)
            print(f"[cleanup] 已清理 fish_condition_images 目录：{_log(fc_dir)}")
    else:
        print(f"[keep] fish_condition_images 保留：{_log(video_fish_condition_images_dir(args.video_id))}")

    # 6. 必须保留（供 publish_results 复制到 results/）
    for label, path in [
        ("input/", video_input_dir(args.video_id)),
        ("complete_match", video_complete_match_dir(args.video_id)),
        ("incomplete_match", video_incomplete_match_dir(args.video_id)),
        ("videos/grounded_sam.mp4", video_grounded_sam_video(args.video_id)),
        ("measurements CSV", video_long_height_csv(args.video_id)),
        ("arched_back 中间结果", video_arched_back_dir(args.video_id)),
        ("reports/pipeline_result.json", video_pipeline_result_json(args.video_id)),
    ]:
        print(f"[keep] {label} 保留：{_log(path)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "一键鱼体检测 pipeline：calibrate(可选) → video_to_frames → adjust → "
            "grounded_sam → filter_complete_fish → fish_condition → arched_back "
            "→ publish_results + 默认清理。"
            "所有路径来自 config/paths；CLI 允许传入绝对路径或相对于项目根的相对路径。"
            "默认不传 --video-id 时，系统自动生成 video_YYYYMMDD_HHMMSS_<8位uuid>。"
        )
    )

    parser.add_argument(
        "--video-id",
        default=None,
        help=(
            "可选：视频 ID。省略时由 config.paths.generate_video_id() 自动生成"
            "（形如 video_YYYYMMDD_HHMMSS_<8位uuid>）。"
        ),
    )
    parser.add_argument(
        "--input-video",
        required=True,
        help="输入视频路径，支持绝对路径或相对于项目根目录的相对路径",
    )

    # 标定相关参数
    parser.add_argument(
        "--calibration-source-image",
        default=None,
        help="可选：用于重新跑 calibrate 的标定图片，支持绝对路径或相对路径。"
             "传入时优先重新标定，生成新的 data/calibration/current_calibration.json。",
    )
    parser.add_argument(
        "--calibration-json",
        default=None,
        help="可选：直接使用已有 calibration JSON；省略时默认 data/calibration/current_calibration.json。"
             "支持绝对路径或相对路径。",
    )
    parser.add_argument("--camera-id", default="default", help="摄像头逻辑 ID，默认 default")
    parser.add_argument("--calibration-id", default=None, help="可选：手动指定 calibration_id")
    parser.add_argument(
        "--real-length-mm",
        type=float,
        default=None,
        help="可选：传送带长边真实长度（毫米），用于估算 mm_per_pixel",
    )
    parser.add_argument("--calibration-note", default=None, help="可选：写入 calibration.json 的注释")

    # 运行参数
    parser.add_argument("--video-fps", type=int, default=25, help="视频 FPS，默认 25")
    parser.add_argument("--device", default="cuda", help="推理设备，默认 cuda")
    parser.add_argument("--sam-version", default="vit_h", help="SAM ViT 版本：vit_b / vit_l / vit_h，默认 vit_h")
    parser.add_argument(
        "--grounded-config",
        default=None,
        help="GroundingDINO 配置 .py 路径；相对项目根或绝对路径。"
             "默认 = algorithms/algorithms/gaoyuxi/grounding_dino_swin-t_finetune_8xb4_50e_fish.py",
    )
    parser.add_argument(
        "--grounded-checkpoint",
        default=None,
        help="GroundingDINO 权重 .pth 路径；相对项目根或绝对路径。"
             "默认 = algorithms/pth/best_coco_bbox_mAP_epoch_12.pth。"
             "必须与 --grounded-config 配套。",
    )
    parser.add_argument(
        "--sam-checkpoint",
        default=None,
        help="SAM 权重路径；相对项目根或绝对路径。默认 algorithms/pth/sam_vit_h_4b8939.pth",
    )
    parser.add_argument(
        "--sam-hq-checkpoint",
        default=None,
        help="SAM-HQ 权重路径；相对项目根或绝对路径。默认 algorithms/pth/sam_hq_vit_h.pth",
    )
    parser.add_argument("--use-sam-hq", action="store_true", help="使用 SAM-HQ 而非基础 SAM")
    parser.add_argument("--box-threshold", type=float, default=0.5, help="检测框阈值，默认 0.5")
    parser.add_argument(
        "--top-edge-ratio",
        type=float,
        default=0.0,
        help="顶部有效检测区域向内收缩的画面高度比例",
    )
    parser.add_argument(
        "--bottom-edge-ratio",
        type=float,
        default=0.0,
        help="底部有效检测区域向内收缩的画面高度比例",
    )
    parser.add_argument(
        "--left-edge-ratio",
        type=float,
        default=0.0,
        help="左侧允许检测区域向画面外扩展的画面宽度比例",
    )
    parser.add_argument(
        "--right-edge-ratio",
        type=float,
        default=0.0,
        help="右侧允许检测区域向画面外扩展的画面宽度比例",
    )
    parser.add_argument("--frame-start", type=int, default=None)
    parser.add_argument("--frame-end", type=int, default=None)
    parser.add_argument("--transform-matrix", default=None)
    parser.add_argument("--mm-per-pixel", type=float, default=None)

    # wound 体表破损计算（正式流程默认启用）
    parser.add_argument(
        "--disable-wound",
        action="store_true",
        help="【内部测试用】跳过 wound 体表破损计算。正式生产流程**不要**使用。",
    )
    parser.add_argument(
        "--wound-checkpoint",
        default=None,
        help="wound 模型权重路径；不传则使用 FISH_WOUND_CHECKPOINT 或 algorithms/pth/maskrcnn_resnet50_fpn_v2-wound.pth 默认路径。",
    )
    parser.add_argument(
        "--wound-threshold",
        type=float,
        default=0.5,
        help="wound 检测 score 阈值，默认 0.5",
    )
    parser.add_argument(
        "--complete-threshold",
        type=validate_complete_threshold,
        default=None,
        metavar="FLOAT",
        help=(
            "完整类别概率阈值；只有 P(complete) >= 此值才归为完整。"
            "未显式传入时读取 algorithms/config/adjust_calibration_margin.json 的 "
            f"complete_threshold；配置文件缺失时回退默认 {DEFAULT_COMPLETE_THRESHOLD:.1f}"
        ),
    )
    parser.add_argument(
        "--force-all-complete",
        action="store_true",
        help=(
            "跳过完整性 ViT 判断；所有可正常处理的分割鱼都按完整鱼处理，"
            "但仍执行方向校正以保证后续测量数据格式不变。"
        ),
    )

    # 行为开关
    parser.add_argument("--overwrite", action="store_true",
                        help="允许覆盖已有的 jobs/<video_id>/ 和 results/<video_id>/ 目录。")
    parser.add_argument("--clean", action="store_true",
                        help="运行前先清空 data/jobs/<video-id>/（不影响 results）。")

    parser.add_argument("--keep-raw-frames", action="store_true",
                        help="保留 frames/raw/")
    parser.add_argument("--keep-adjusted-frames", action="store_true",
                        help="保留 frames/adjusted/")
    parser.add_argument("--keep-first-match", action="store_true",
                        help="保留 crop_images/first_match/")
    parser.add_argument("--keep-annotated-frames", action="store_true",
                        help="保留 crop_images/annotated_frames/")
    parser.add_argument(
        "--keep-measurement-debug-images",
        dest="keep_measurement_debug_images",
        action="store_true",
        default=True,
        help="保留 measurements/fish_condition_images/（默认行为）",
    )
    parser.add_argument(
        "--discard-measurement-debug-images",
        dest="keep_measurement_debug_images",
        action="store_false",
        help="发布后删除 jobs 中的 measurements/fish_condition_images/",
    )

    parser.add_argument(
        "--steps",
        default=",".join(name for name, _ in STEPS),
        help=f"逗号分隔的步骤名；默认全部。选项: {','.join(name for name, _ in STEPS)}",
    )
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="调试用：跳过 publish_results 阶段，不发布到 data/results/<video_id>/",
    )
    parser.add_argument(
        "--simulate-failure-stage",
        default="none",
        choices=VALID_FAULT_STAGES,
        help=(
            "【内部测试参数】模拟在某阶段后抛 RuntimeError 触发失败流程。"
            "生产环境不要使用。可选：none / after_video_to_frames / "
            "after_adjust_batch / after_grounded_sam / after_filter_complete_fish / "
            "before_fish_condition_csv / after_arched_back / before_publish_results。"
        ),
    )
    return parser


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    argv_list = list(argv) if argv is not None else []
    # 0. 子命令：scan-interrupted
    scan_subcmd: list[str] | None = None
    if "scan-interrupted" in argv_list:
        idx = argv_list.index("scan-interrupted")
        scan_subcmd = argv_list[idx + 1 :]
        return _cmd_scan_interrupted(scan_subcmd)

    # 把模块名（若有）剥掉后再交给 parse_args
    if argv_list and argv_list[0] in (
        "algorithms.tools.run_video_pipeline",
        "run_video_pipeline.py",
    ):
        argv = argv_list[1:]

    args = parse_args(argv)

    # 0. 自动生成 video_id（用户未传时）
    user_provided_video_id = bool(getattr(args, "video_id", None))
    if not user_provided_video_id:
        args.video_id = generate_video_id()
        print(f"[init] 未传 --video-id，系统自动生成：{args.video_id}")
    else:
        print(f"[init] 使用用户传入 video_id：{args.video_id}")

    # 1. 校验：--input-video 必须存在
    input_video = _resolve_input(args.input_video)
    if input_video is None or not input_video.exists():
        print(f"[ERROR] 输入视频不存在：{args.input_video}")
        return 2

    # 1.1 校验：current_calibration.json 必须已存在；pipeline 不再计算透视矩阵。
    # 软件工程师正式流程应先运行 ``python -m algorithms.core.adjust_calibration``。
    calibration_json: Path | None = None
    if args.calibration_json:
        calibration_json = _resolve_input(args.calibration_json)
        if calibration_json is None or not calibration_json.exists():
            print(
                f"[ERROR] 标定 JSON 不存在：{args.calibration_json}。"
                "请先运行 ``python -m algorithms.core.adjust_calibration`` 生成 "
                "``data/calibration/current_calibration.json``，或传入有效 --calibration-json。"
            )
            return 2
    elif CALIBRATION_CURRENT_JSON.is_file():
        calibration_json = CALIBRATION_CURRENT_JSON
    else:
        print(
            f"[ERROR] 找不到透视矩阵：{CALIBRATION_CURRENT_JSON}。"
            "请先运行 ``python -m algorithms.core.adjust_calibration`` 生成 "
            "``data/calibration/current_calibration.json``。"
        )
        return 2

    if getattr(args, "calibration_source_image", None):
        print(
            "[WARN] --calibration-source-image 已废弃：pipeline 不再执行标定。"
            "请先运行 ``python -m algorithms.core.adjust_calibration`` 生成 "
            "``data/calibration/current_calibration.json``。"
        )

    # 1.5 检查 jobs/results 目录冲突
    job_root = job_dir(args.video_id)
    final_results = result_dir(args.video_id)
    partial_results = result_partial_dir(args.video_id)

    if job_root.exists():
        if args.clean or args.overwrite:
            if _ensure_safe_to_clean(job_root, JOBS_DIR, "job 根"):
                remove_dir_contents(job_root)
                print(f"[cleanup] 已清空 job 根：{_log(job_root)}")
        else:
            print(
                f"[ERROR] job 目录已存在：{_log(job_root)}。"
                "如需复用请加 --clean 或 --overwrite。"
            )
            return 2
    if final_results.exists():
        if args.overwrite:
            if _ensure_safe_to_clean(final_results, RESULTS_DIR, "results"):
                remove_dir(final_results)
                print(f"[cleanup] 已清空旧 results：{_log(final_results)}")
        else:
            print(
                f"[ERROR] 长期结果目录已存在：{_log(final_results)}。"
                "如需覆盖请加 --overwrite。"
            )
            return 2
    if partial_results.exists():
        if args.overwrite:
            if _ensure_safe_to_clean(partial_results, RESULTS_DIR, "partial"):
                remove_dir(partial_results)
                print(f"[cleanup] 已清空旧 partial：{_log(partial_results)}")
        else:
            print(
                f"[ERROR] 发现未完成发布目录：{_log(partial_results)}。"
                "如需覆盖请加 --overwrite。"
            )
            return 2

    print(f"==== pipeline start: video_id={args.video_id} ====")
    print(f"  input_video              : {_log(input_video)}")
    if args.calibration_source_image:
        print(f"  calibration_source_image : {args.calibration_source_image}")
    if calibration_json:
        print(f"  calibration_json         : {_log(calibration_json)}")

    prepare_job_dirs(args.video_id)

    # 2. 把输入视频复制到 data/jobs/<video_id>/input/，写 input_manifest.json
    calibration_source_image = (
        _resolve_input(args.calibration_source_image) if args.calibration_source_image else None
    )
    try:
        job_input_video = stage_input_video(
            video_id=args.video_id,
            original_input_video=input_video,
            calibration_source_image=calibration_source_image,
            calibration_json=calibration_json,
            overwrite=args.overwrite,
        )
    except (FileNotFoundError, FileExistsError) as exc:
        print(f"[ERROR] {exc}")
        return 2

    selected = [name for name, _ in STEPS if name in set(args.steps.split(","))]
    if not selected:
        print(f"[ERROR] --steps 解析为空：{args.steps}")
        return 2

    # 3. 初始化 PipelineState + PipelineLock
    state = PipelineState(
        args.video_id,
        command=[sys.argv[0], *argv_list] if argv_list else [sys.argv[0]],
    )
    state.payload["input_video"] = _log(job_input_video)
    state.payload["result_dir"] = _log(result_dir(args.video_id))
    state.mark_running()

    lock = PipelineLock(args.video_id, command=state.payload.get("command", []))
    if not lock.acquire(overwrite_stale=args.overwrite):
        if args.overwrite:
            print(f"[ERROR] 无法获取 pipeline.lock（{_log(lock.path)}）；请手动删除 stale lock。")
        else:
            print(
                f"[ERROR] pipeline.lock 已存在：{_log(lock.path)}。"
                "如有其他进程正在运行或上次任务异常退出，请加 --overwrite 覆盖 stale lock。"
            )
        state.fail("__init__", f"无法获取 pipeline.lock ({lock.path})")
        return 2

    try:
        return _run_pipeline(args, state, ctx=None)
    finally:
        lock.release()


def _run_pipeline(args, state: PipelineState, ctx=None) -> int:
    """主流程：state 已经在外部初始化，这里负责跑步骤 + 发布 + 失败处理。"""

    input_video = state.payload.get("original_input_video_path") or _resolve_input(args.input_video)
    if not isinstance(input_video, Path):
        input_video = Path(input_video) if input_video else None
    if input_video is None:
        input_video = _resolve_input(args.input_video)
    if input_video is None:
        print(f"[ERROR] 输入视频不存在：{args.input_video}")
        state.fail("__init__", f"输入视频不存在：{args.input_video}")
        return 2
    state.payload["original_input_video_path"] = str(input_video)

    calibration_source_image = (
        _resolve_input(args.calibration_source_image) if args.calibration_source_image else None
    )
    calibration_json: Path | None = None
    if args.calibration_json:
        calibration_json = _resolve_input(args.calibration_json)

    job_input_video = video_input_dir(args.video_id) / f"{args.video_id}_source_video{(input_video.suffix or '.mp4').lower()}"

    selected = [name for name, _ in STEPS if name in set(args.steps.split(","))]
    if not selected:
        print(f"[ERROR] --steps 解析为空：{args.steps}")
        state.fail("__init__", "--steps 解析为空")
        return 2

    ctx = ctx or {
        "video_id": args.video_id,
        "original_input_video": input_video,
        "job_input_video": job_input_video,
    }
    ctx["state"] = state

    failed_stage: str | None = None
    error_message = ""
    for step_name in selected:
        state.start_stage(step_name)
        func = dict(STEPS)[step_name]
        print(f"\n[step:{step_name}] 开始")
        try:
            rc = func(args, ctx)
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
            error_message = f"submodule {step_name} SystemExit({rc})"
        except Exception as exc:
            rc = 1
            error_message = f"{type(exc).__name__}: {exc}"
        if rc != 0:
            failed_stage = step_name
            state.fail(step_name, error_message or f"返回 {rc}")
            print(f"[ERROR] step:{step_name} 返回 {rc}: {error_message}")
            break
        state.complete_stage(step_name)
        print(f"[step:{step_name}] 完成")

        # before_publish_results 钩子
        if step_name == "arched_back":
            maybe_simulate_failure(args, FAULT_BEFORE_PUBLISH_RESULTS)

    csv_path = video_long_height_csv(args.video_id)

    # 失败分支
    if failed_stage is not None:
        _finalize_failure(
            state=state,
            args=args,
            failed_stage=failed_stage,
            error_message=error_message,
            input_video=input_video,
            calibration_source_image=calibration_source_image,
            calibration_json=calibration_json,
            csv_path=csv_path,
            selected=selected,
        )
        return 1

    # 成功分支
    cleanup_intermediate_dirs(args)

    payload = build_pipeline_result(
        video_id=args.video_id,
        status="done",
        input_video=job_input_video,
        original_input_video=input_video,
        calibration_source_image=calibration_source_image,
        calibration_json=calibration_json,
        csv_path=csv_path,
        selected_steps=selected,
        completed_stages=state.completed_stages,
    )
    write_pipeline_result(payload, args.video_id)
    write_error_conclusion(args.video_id, payload)

    if not args.no_publish:
        state.publishing()
        try:
            final_dir = publish_results(
                args.video_id,
                overwrite=args.overwrite,
                original_input_video=input_video,
            )
            print(f"[publish] 长期结果：{_log(final_dir)}")
        except FileExistsError as exc:
            print(f"[ERROR] {exc}")
            state.fail("publish_results", str(exc))
            _ensure_failure_results(state, args, "publish_results", str(exc), selected=selected)
            return 2
    else:
        print("[publish] --no-publish，跳过发布到 results/")

    state.done()
    print()
    print("==== pipeline 完成 ====")
    print(f"video_id              : {args.video_id}")
    print(f"job_input_video       : {_log(job_input_video)}")
    print(f"original_input_video  : {input_video}")
    print(f"results_dir           : {_log(result_dir(args.video_id))}")
    print(f"grounded_video        : {payload['grounded_video']}")
    print(f"measurement_csv       : {payload['measurement_csv']}")
    print(f"arched_back_results   : {payload['arched_back_results_dir']}")
    print(f"complete_match        : {payload['complete_match_dir']}")
    print(f"incomplete_match      : {payload['incomplete_match_dir']}")
    print(f"pipeline_result       : {payload['measurement_csv'] and 'results/reports/pipeline_result.json'}")
    print(f"summary               : total={payload['summary']['total_fish']}, "
          f"complete={payload['summary']['complete_fish']}, "
          f"incomplete={payload['summary']['incomplete_fish']}, "
          f"failed={payload['summary']['failed_fish']}, "
          f"damaged_fish={payload['summary'].get('damaged_fish', 0)}, "
          f"avg_surface_damage_ratio={payload['summary'].get('avg_surface_damage_ratio', 0)}, "
          f"max_surface_damage_ratio={payload['summary'].get('max_surface_damage_ratio', 0)}")
    return 0


def _finalize_failure(
    *,
    state: PipelineState,
    args,
    failed_stage: str,
    error_message: str,
    input_video: Path,
    calibration_source_image: Path | None,
    calibration_json: Path | None,
    csv_path: Path,
    selected: list[str],
) -> None:
    """失败时也必须写 jobs/reports/* 和 results/reports/*，并生成 error_conclusion.txt。"""

    job_input_video = video_input_dir(args.video_id) / f"{args.video_id}_source_video{(input_video.suffix or '.mp4').lower()}"
    payload = build_pipeline_result(
        video_id=args.video_id,
        status="failed",
        input_video=job_input_video,
        original_input_video=input_video,
        calibration_source_image=calibration_source_image,
        calibration_json=calibration_json,
        csv_path=csv_path,
        failed_stage=failed_stage,
        error_message=error_message,
        selected_steps=selected,
        completed_stages=state.completed_stages,
    )
    write_pipeline_result(payload, args.video_id)
    write_error_conclusion(args.video_id, payload)
    _ensure_failure_results(
        state=state,
        args=args,
        failed_stage=failed_stage,
        error_message=error_message,
        selected=selected,
        input_video=input_video,
    )


def _ensure_failure_results(
    *,
    state: PipelineState,
    args,
    failed_stage: str,
    error_message: str,
    selected: list[str],
    input_video: Path | None = None,
) -> None:
    """失败时也生成 ``data/results/<video_id>/``，至少含 reports/ + input/。"""

    if input_video is None:
        input_video = _resolve_input(args.input_video)
    if input_video is None:
        return
    # 1. results/ 下放最小失败包
    ensure_dir(result_dir(args.video_id))
    ensure_dir(result_reports_dir(args.video_id))
    ensure_dir(result_input_dir(args.video_id))

    # 复制 input/（包括 input_manifest.json + 任务内视频副本）
    src_input = video_input_dir(args.video_id)
    if src_input.exists():
        try:
            _safe_copy_tree(src_input, result_input_dir(args.video_id))
        except Exception as exc:
            print(f"[WARN] 复制 input/ 到 results/ 失败：{exc}")

    # 复制 state/pipeline_state.json 到 results/state_snapshot.json
    state_path = state.path
    if state_path.is_file():
        try:
            _safe_copy_file(state_path, result_reports_dir(args.video_id) / "state_snapshot.json")
        except Exception as exc:
            print(f"[WARN] 复制 state 快照失败：{exc}")

    # 重写 results/reports/pipeline_result.json，确保 status=failed
    jobs_report = video_pipeline_result_json(args.video_id)
    if jobs_report.is_file():
        try:
            with open(jobs_report, "r", encoding="utf-8") as fp:
                payload = json.load(fp)
            payload = _rewrite_pipeline_result_paths(payload, args.video_id)
            if input_video is not None:
                ext = input_video.suffix.lower() or ".mp4"
                payload["input_video"] = _log(
                    result_input_dir(args.video_id) / f"{args.video_id}_source_video{ext}"
                )
            payload["error_conclusion"] = _log(result_error_conclusion_txt(args.video_id))
            results_report = result_pipeline_result_json(args.video_id)
            with open(results_report, "w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f"[WARN] 写 results/reports/pipeline_result.json 失败：{exc}")

    # 复制 error_conclusion.txt 到 results/
    jobs_err = video_reports_dir(args.video_id) / "error_conclusion.txt"
    if jobs_err.is_file():
        try:
            _safe_copy_file(jobs_err, result_error_conclusion_txt(args.video_id))
        except Exception as exc:
            print(f"[WARN] 复制 error_conclusion.txt 失败：{exc}")

    print(
        f"\n[ERROR] pipeline 失败（stage={failed_stage}）。"
        f"报告：{_log(result_pipeline_result_json(args.video_id))}"
    )


def _cmd_scan_interrupted(argv: list[str]) -> int:
    """``python -m algorithms.tools.run_video_pipeline --scan-interrupted`` 子命令。"""

    parser = argparse.ArgumentParser(
        prog="python -m algorithms.tools.run_video_pipeline --scan-interrupted",
        description=(
            "扫描 data/jobs/<video_id>/state/pipeline_state.json，"
            "把 pid 已死但 status 仍为 running/publishing/stage_done 的标记为 interrupted，"
            "并为缺失 results/ 的 video_id 创建最小失败 results 目录。"
        ),
    )
    parser.add_argument(
        "--video-id",
        default=None,
        help="可选：只扫描指定 video_id",
    )
    args = parser.parse_args(argv)

    flagged = scan_interrupted_jobs()
    if args.video_id:
        flagged = [p for p in flagged if args.video_id in str(p)]
    if not flagged:
        print("[OK] 没有发现 interrupted 的 pipeline 状态。")
        return 0

    print(f"[scan] 发现 {len(flagged)} 个 interrupted 状态：")
    for state_path in flagged:
        video_id = state_path.parent.parent.name
        print(f"  - {state_path}")
        try:
            _ensure_minimal_failure_results(video_id)
        except Exception as exc:
            print(f"     [WARN] 创建最小失败 results 失败：{exc}")
    return 0


def _ensure_minimal_failure_results(video_id: str) -> None:
    """对 interrupted 的 video_id，确保 ``data/results/<video_id>/`` 存在并写入最小失败包。"""

    state_path = video_pipeline_state_json(video_id)
    if not state_path.is_file():
        return
    with open(state_path, "r", encoding="utf-8") as fp:
        state_payload = json.load(fp)
    status = state_payload.get("status", "")
    if status != STATE_INTERRUPTED:
        return

    ensure_dir(result_dir(video_id))
    ensure_dir(result_reports_dir(video_id))
    ensure_dir(result_input_dir(video_id))

    # 1. input/ 复制
    src_input = video_input_dir(video_id)
    if src_input.exists():
        _safe_copy_tree(src_input, result_input_dir(video_id))

    # 2. 写 results/reports/pipeline_result.json
    jobs_csv = video_long_height_csv(video_id)
    has_csv = jobs_csv.is_file()
    pipeline_result = {
        "video_id": video_id,
        "status": "failed",
        "failed_stage": state_payload.get("failed_stage", "") or state_payload.get("current_stage", ""),
        "error_message": state_payload.get("error_message", "进程被终止、断电、机器重启等极端中断"),
        "summary": {
            "total_fish": 0,
            "complete_fish": 0,
            "incomplete_fish": 0,
            "failed_fish": 0,
        },
        "fish": [],
    }
    pipeline_result["error_conclusion"] = _log(result_error_conclusion_txt(video_id))
    with open(result_pipeline_result_json(video_id), "w", encoding="utf-8") as fp:
        json.dump(pipeline_result, fp, ensure_ascii=False, indent=2)

    # 3. 写 results/reports/error_conclusion.txt
    txt_lines = [
        "任务状态：中断",
        f"视频ID：{video_id}",
        f"失败阶段：{pipeline_result['failed_stage']}",
        f"错误原因：{pipeline_result['error_message']}",
        "可能原因：进程被终止、断电、机器重启、显卡崩溃",
        "已完成阶段：" + ", ".join(state_payload.get("completed_stages", []) or []),
        "结论：算法任务在中断前可能已部分完成，但 final pipeline_result.json 中 status=failed，不能进入正常统计。",
        "建议：检查 data/jobs/<video_id>/state/pipeline_state.json 和运行环境后，加 --overwrite 重新运行。",
    ]
    atomic_write_text(result_error_conclusion_txt(video_id), "\n".join(txt_lines) + "\n")

    # 4. state 快照
    _safe_copy_file(state_path, result_reports_dir(video_id) / "state_snapshot.json")

    print(f"     -> 已写 {result_pipeline_result_json(video_id)}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
