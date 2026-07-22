"""统一路径配置。

所有算法文件都应从本模块读取路径，禁止在算法文件中写死绝对路径。
所有路径使用 ``pathlib.Path`` 表示。
"""

from __future__ import annotations

import datetime as _dt
import uuid
from pathlib import Path


def _resolve_project_root() -> Path:
    """解析项目根目录。

    项目根目录为 ``~/code/algorithms20260702-Yzt``，本配置文件位于
    ``<project_root>/algorithms/config/paths.py``，因此向上回溯两级即可。
    """

    here = Path(__file__).resolve()
    project_root = here.parents[2]
    return project_root


PROJECT_ROOT: Path = _resolve_project_root()
ALGORITHMS_ROOT: Path = PROJECT_ROOT / "algorithms"
DATA_ROOT: Path = PROJECT_ROOT / "data"


# ---------------------------------------------------------------------------
# 自动 video_id 生成
# ---------------------------------------------------------------------------


def generate_video_id(prefix: str = "video", now: _dt.datetime | None = None) -> str:
    """生成 ``<prefix>_YYYYMMDD_HHMMSS_<8位uuid>`` 形式的 video_id。

    推荐调用方不传参数；``prefix`` 默认 ``video``，``now`` 默认当前时间。
    测试场景可以传入 ``now`` 以得到稳定的 video_id。
    """

    moment = now or _dt.datetime.now()
    short_uuid = uuid.uuid4().hex[:8]
    return f"{prefix}_{moment.strftime('%Y%m%d_%H%M%S')}_{short_uuid}"


def resolve_input_path(path_str: str | Path | None) -> Path:
    """解析用户从 CLI 传入的输入路径。

    规则：

    1. ``None`` / 空字符串：返回 ``None``（调用方决定如何报错）。
    2. 绝对路径：原样 ``expanduser + resolve``，保留用户原位置。
    3. 相对路径：相对于 ``PROJECT_ROOT`` 解析后 ``resolve``。

    注意：本函数**不会**把外部绝对路径改写到项目内，
    以便测试阶段允许传入项目根目录的绝对路径。
    """

    if path_str is None:
        return None
    text = str(path_str).strip()
    if not text:
        return None
    p = Path(text).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


def resolve_project_path(path_str: str | Path | None) -> Path | None:
    """``resolve_input_path`` 的别名；用于需要明确语义"项目相关路径"的场景。"""

    return resolve_input_path(path_str)


def resolve_data_path(path_str: str | Path | None) -> Path | None:
    """解析 ``data/`` 下的子路径。

    - 相对路径一律按 ``DATA_ROOT`` 解析（无论是否以 ``data/`` 开头）。
    - 绝对路径原样返回（不强制必须在 ``DATA_ROOT`` 之内，便于历史数据回放）。
    """

    if path_str is None:
        return None
    text = str(path_str).strip()
    if not text:
        return None
    p = Path(text).expanduser()
    if not p.is_absolute():
        p = DATA_ROOT / p
    return p.resolve()

INPUT_VIDEOS_DIR: Path = DATA_ROOT / "input_videos"
JOBS_DIR: Path = DATA_ROOT / "jobs"
CALIBRATION_DIR: Path = DATA_ROOT / "calibration"
MODEL_DIR: Path = ALGORITHMS_ROOT / "pth"

LOGS_DIR: Path = DATA_ROOT / "logs"

ALGORITHMS_GAOYUXI_DIR: Path = ALGORITHMS_ROOT / "algorithms" / "gaoyuxi"
ALGORITHMS_DENGCHAO_DIR: Path = ALGORITHMS_ROOT / "algorithms" / "dengchao"
ALGORITHMS_CUIYIFEI_DIR: Path = ALGORITHMS_ROOT / "algorithms" / "cuiyifei"

CALIBRATION_REPO_DIR: Path = ALGORITHMS_GAOYUXI_DIR
GROUNDING_DINO_DIR: Path = ALGORITHMS_GAOYUXI_DIR / "GroundingDINO"
GROUNDED_SAM_DIR: Path = ALGORITHMS_GAOYUXI_DIR / "Grounded-Segment-Anything"
SEGMENT_ANYTHING_DIR: Path = GROUNDED_SAM_DIR / "segment_anything"
BERT_BASE_UNCASED_DIR: Path = ALGORITHMS_GAOYUXI_DIR / "bert-base-uncased"

GROUNDED_DINO_CONFIG_PATH: Path = (
    ALGORITHMS_GAOYUXI_DIR / "grounding_dino_swin-t_finetune_8xb4_50e_fish.py"
)
GROUNDING_DINO_CONFIG_PATH: Path = GROUNDED_DINO_CONFIG_PATH  # 向后兼容命名
GROUNDING_DINO_CHECKPOINT: Path = MODEL_DIR / "groundingdino_swint_ogc.pth"
MM_DETECTION_CHECKPOINT: Path = MODEL_DIR / "best_coco_bbox_mAP_epoch_12.pth"
SAM_VIT_H_CHECKPOINT: Path = MODEL_DIR / "sam_vit_h_4b8939.pth"
SAM_HQ_VIT_H_CHECKPOINT: Path = MODEL_DIR / "sam_hq_vit_h.pth"
TAIL_DETECTOR_CHECKPOINT: Path = MODEL_DIR / "maskrcnn_resnet50_fpn_v2-tail.pth"
WOUND_DETECTOR_CHECKPOINT: Path = MODEL_DIR / "maskrcnn_resnet50_fpn_v2-wound.pth"
SWIN_TINY_CHECKPOINT: Path = MODEL_DIR / "swin_tiny_patch4_window7_224.pth"
CATBOOST_FULL_MODEL_PATH: Path = MODEL_DIR / "catboost_full_model.pkl"
COMPLETENESS_VIT_CHECKPOINT_PATH: Path = MODEL_DIR / "best_com_base.pth"
COMPLETENESS_VIT_CACHE_DIR: Path = MODEL_DIR / "cache" / "vit-base"
BELLY_TREE_MODEL_PATH: Path = MODEL_DIR / "tree_model" / "belly_train_tree.pkl"
RFDETR_SEG_MEDIUM_FP16_ENGINE_PATH: Path = (
    MODEL_DIR / "output_onnx_dynamic" / "rfdetr-seg-medium-b64-fp16.engine"
)


def job_dir(video_id: str) -> Path:
    """返回指定视频的专属作业目录。

    所有与单个视频相关的临时结果都应放在这里。
    """

    return JOBS_DIR / video_id


def video_input_dir(video_id: str) -> Path:
    return job_dir(video_id) / "input"


def video_frames_raw_dir(video_id: str) -> Path:
    return job_dir(video_id) / "frames" / "raw"


def video_frames_adjusted_dir(video_id: str) -> Path:
    return job_dir(video_id) / "frames" / "adjusted"


def video_crop_images_dir(video_id: str) -> Path:
    return job_dir(video_id) / "crop_images"


def video_annotated_frames_dir(video_id: str) -> Path:
    return video_crop_images_dir(video_id) / "annotated_frames"


def video_first_match_dir(video_id: str) -> Path:
    return video_crop_images_dir(video_id) / "first_match"


def video_complete_match_dir(video_id: str) -> Path:
    return video_crop_images_dir(video_id) / "complete_match"


def video_incomplete_match_dir(video_id: str) -> Path:
    return video_crop_images_dir(video_id) / "incomplete_match"


def video_videos_dir(video_id: str) -> Path:
    return job_dir(video_id) / "videos"


def video_grounded_sam_video(video_id: str) -> Path:
    return video_videos_dir(video_id) / "grounded_sam.mp4"


def video_measurements_dir(video_id: str) -> Path:
    return job_dir(video_id) / "measurements"


def video_fish_condition_images_dir(video_id: str) -> Path:
    return video_measurements_dir(video_id) / "fish_condition_images"


def video_long_height_csv(video_id: str) -> Path:
    return video_measurements_dir(video_id) / "long_height_weight_results.csv"


def video_reports_dir(video_id: str) -> Path:
    return job_dir(video_id) / "reports"


def video_pipeline_result_json(video_id: str) -> Path:
    return video_reports_dir(video_id) / "pipeline_result.json"


def video_state_dir(video_id: str) -> Path:
    """``data/jobs/<video_id>/state/`` 目录，保存 pipeline 内部状态文件。"""

    return job_dir(video_id) / "state"


def video_pipeline_state_json(video_id: str) -> Path:
    return video_state_dir(video_id) / "pipeline_state.json"


def video_pipeline_lock_file(video_id: str) -> Path:
    return video_state_dir(video_id) / "pipeline.lock"


# ---------------------------------------------------------------------------
# 长期 results 目录（软件工程师读取这里）
# ---------------------------------------------------------------------------
#
# 目录结构：
#   data/results/<video_id>/
#   ├── input/<video_id>_source_video.<ext>            # 任务内输入视频副本
#   ├── input/input_manifest.json                       # 任务输入元数据
#   ├── videos/grounded_sam.mp4                         # 标注视频
#   ├── fish_images/
#   │   ├── complete_match/                            # 完整鱼图片
#   │   └── incomplete_match/                          # 不完整鱼图片
#   ├── measurements/long_height_weight_results.csv    # 测量结果
#   ├── reports/
#   │   ├── pipeline_result.json                       # 统一对接 JSON
#   │   └── error_conclusion.txt                       # 报错结论（可选）
#   └── calibration/                                    # 标定相关快照
#       ├── current_calibration.json
#       └── sessions/<calibration_id>/...
#

RESULTS_DIR: Path = DATA_ROOT / "results"


def result_dir(video_id: str) -> Path:
    return RESULTS_DIR / video_id


def result_partial_dir(video_id: str) -> Path:
    """发布过程中的临时目录 ``data/results/<video_id>.partial/``。"""

    return RESULTS_DIR / f"{video_id}.partial"


def result_input_dir(video_id: str) -> Path:
    return result_dir(video_id) / "input"


def result_videos_dir(video_id: str) -> Path:
    return result_dir(video_id) / "videos"


def result_grounded_sam_video(video_id: str) -> Path:
    return result_videos_dir(video_id) / "grounded_sam.mp4"


def result_fish_images_dir(video_id: str) -> Path:
    return result_dir(video_id) / "fish_images"


def result_complete_match_dir(video_id: str) -> Path:
    return result_fish_images_dir(video_id) / "complete_match"


def result_incomplete_match_dir(video_id: str) -> Path:
    return result_fish_images_dir(video_id) / "incomplete_match"


def result_measurements_dir(video_id: str) -> Path:
    return result_dir(video_id) / "measurements"


def result_long_height_csv(video_id: str) -> Path:
    return result_measurements_dir(video_id) / "long_height_weight_results.csv"


def result_reports_dir(video_id: str) -> Path:
    return result_dir(video_id) / "reports"


def result_pipeline_result_json(video_id: str) -> Path:
    return result_reports_dir(video_id) / "pipeline_result.json"


def result_error_conclusion_txt(video_id: str) -> Path:
    return result_reports_dir(video_id) / "error_conclusion.txt"


# ---------------------------------------------------------------------------
# results 下不再有 calibration/。
# 透视矩阵统一保存在 ``data/calibration/current_calibration.json``，
# 软件工程师只读取这一份全局文件。
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 标定（calibration）相关路径
# ---------------------------------------------------------------------------

CALIBRATION_TMP_DIR: Path = CALIBRATION_DIR / "tmp"
CALIBRATION_SESSIONS_DIR: Path = CALIBRATION_DIR / "sessions"
CALIBRATION_CURRENT_JSON: Path = CALIBRATION_DIR / "current_calibration.json"


def calibration_session_dir(calibration_id: str) -> Path:
    return CALIBRATION_SESSIONS_DIR / calibration_id


def to_project_relative(path: Path | str) -> str:
    """将绝对路径转换为相对项目根的 POSIX 风格路径字符串。

    用于写入 JSON，避免出现绝对路径泄漏。
    """

    p = Path(path).expanduser()
    try:
        rel = p.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return str(p)
    return rel.as_posix()


def ensure_dir(path: Path | str) -> Path:
    """递归创建目录（如果不存在）并返回 ``Path``。"""

    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


__all__ = [
    "PROJECT_ROOT",
    "ALGORITHMS_ROOT",
    "DATA_ROOT",
    "INPUT_VIDEOS_DIR",
    "JOBS_DIR",
    "RESULTS_DIR",
    "CALIBRATION_DIR",
    "MODEL_DIR",
    "LOGS_DIR",
    "ALGORITHMS_GAOYUXI_DIR",
    "ALGORITHMS_DENGCHAO_DIR",
    "ALGORITHMS_CUIYIFEI_DIR",
    "CALIBRATION_REPO_DIR",
    "GROUNDING_DINO_DIR",
    "GROUNDED_SAM_DIR",
    "SEGMENT_ANYTHING_DIR",
    "BERT_BASE_UNCASED_DIR",
    "GROUNDING_DINO_CONFIG_PATH",
    "GROUNDING_DINO_CONFIG_PATH",
    "GROUNDING_DINO_CHECKPOINT",
    "MM_DETECTION_CHECKPOINT",
    "SAM_VIT_H_CHECKPOINT",
    "SAM_HQ_VIT_H_CHECKPOINT",
    "TAIL_DETECTOR_CHECKPOINT",
    "WOUND_DETECTOR_CHECKPOINT",
    "SWIN_TINY_CHECKPOINT",
    "CATBOOST_FULL_MODEL_PATH",
    "COMPLETENESS_VIT_CHECKPOINT_PATH",
    "COMPLETENESS_VIT_CACHE_DIR",
    "BELLY_TREE_MODEL_PATH",
    "RFDETR_SEG_MEDIUM_FP16_ENGINE_PATH",
    "generate_video_id",
    "job_dir",
    "video_input_dir",
    "video_frames_raw_dir",
    "video_frames_adjusted_dir",
    "video_crop_images_dir",
    "video_annotated_frames_dir",
    "video_first_match_dir",
    "video_complete_match_dir",
    "video_incomplete_match_dir",
    "video_videos_dir",
    "video_grounded_sam_video",
    "video_measurements_dir",
    "video_fish_condition_images_dir",
    "video_long_height_csv",
    "video_reports_dir",
    "video_pipeline_result_json",
    "video_state_dir",
    "video_pipeline_state_json",
    "video_pipeline_lock_file",
    "result_dir",
    "result_partial_dir",
    "result_input_dir",
    "result_videos_dir",
    "result_grounded_sam_video",
    "result_fish_images_dir",
    "result_complete_match_dir",
    "result_incomplete_match_dir",
    "result_measurements_dir",
    "result_long_height_csv",
    "result_reports_dir",
    "result_pipeline_result_json",
    "result_error_conclusion_txt",
    "CALIBRATION_TMP_DIR",
    "CALIBRATION_SESSIONS_DIR",
    "CALIBRATION_CURRENT_JSON",
    "calibration_session_dir",
    "to_project_relative",
    "ensure_dir",
    "resolve_under_data",
    "resolve_input_path",
    "resolve_project_path",
    "resolve_data_path",
]


def resolve_under_data(path: Path | str) -> Path:
    """解析路径并要求其位于 ``DATA_ROOT`` 之下。

    用于清理工具确认要删除的目录在 ``data/`` 内。"""

    abs_path = Path(path).expanduser().resolve()
    data_root = DATA_ROOT.resolve()
    try:
        abs_path.relative_to(data_root)
    except ValueError as exc:
        raise ValueError(
            f"路径 {abs_path} 不在项目数据目录 {data_root} 内，禁止访问。"
        ) from exc
    return abs_path
