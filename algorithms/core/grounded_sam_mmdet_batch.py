"""
RF-DETR Seg Medium + ByteTrack fish tracking for an ordered image sequence.

The script reads supported images in natural filename order, runs TensorRT
inference with batch size 64, performs score filtering/query selection, box
post-processing, and mask resizing on the GPU, updates ByteTrack sequentially,
and writes the visualized tracking video through two parallel CPU render workers, an ordered FFmpeg writer thread, and an FFmpeg pipe. Image reading for the next batch overlaps current-batch inference. Each output frame contains:
1. instance-segmentation masks and contours;
2. bounding boxes;
3. fish class, track ID, and confidence labels.

The script keeps one BGR image crop and one crop-aligned binary mask for every
confirmed track ID. Tracking/visualized segmentation starts only after the
middle of the fish body passes the configured conveyor position, and the saved
pair comes from the first frame where that middle passes the configured crop
position. Selected instance masks are resized to the full source-image
resolution on the GPU and then copied to the CPU.

Saved outputs use exactly the same paths and names as
``grounded_sam_mmdet_batch.py``:

    crop_images/annotated_frames/<12-digit>_grounded_sam.jpg
    crop_images/first_match/id_<track_id>_<12-digit>_crop.jpg
    crop_images/first_match/id_<track_id>_<12-digit>_mask_crop_bw.png
    videos/grounded_sam.mp4

All paths are resolved through ``config.paths``. Successful runs do not create
additional timing/config files, so downstream code sees the same output layout
as the Grounded-SAM pipeline.

No trajectories, conveyor-direction constraints, order constraints, custom relinking,
or other post-processing association rules are used. Cross-image association is
handled only by ByteTrack, using natural image order as frame order.
"""

from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
from functools import lru_cache
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Queue
from typing import Any, Iterable, Sequence

import cv2
import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn.functional as F
try:
    import supervision as sv
except ImportError:
    sv = None  # type: ignore[assignment]

# Make the script usable both as ``python algorithms/core/<file>.py`` and as a
# module imported by tools/run_video_pipeline.py.
ALGORITHMS_ROOT = Path(__file__).resolve().parent.parent
if str(ALGORITHMS_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS_ROOT))

from config.naming import (
    extract_frame_index_from_path,
    make_annotated_frame_name,
    make_fish_crop_name,
    make_fish_mask_name,
)
from config.paths import (
    ADJUST_CALIBRATION_MARGIN_CONFIG_PATH,
    RFDETR_SEG_MEDIUM_FP16_ENGINE_PATH,
    ensure_dir,
    resolve_input_path,
    video_annotated_frames_dir,
    video_first_match_dir,
    video_frames_adjusted_dir,
    video_grounded_sam_video,
)

try:
    import tensorrt as trt
except ImportError:
    trt = None  # type: ignore[assignment]

try:
    from trackers import ByteTrackTracker
except ImportError:
    ByteTrackTracker = None  # type: ignore[assignment,misc]


DEFAULT_INPUT_DIR = None
DEFAULT_CHECKPOINT = RFDETR_SEG_MEDIUM_FP16_ENGINE_PATH
DEFAULT_OUTPUT_DIR = None
DEFAULT_FIRST_MATCH_OUTPUT_DIR = None
BATCH_SIZE = 64
VISUALIZATION_WORKERS = 2
VISUALIZATION_QUEUE_SIZE = 64
RENDER_RESULT_QUEUE_SIZE = 64
ROI_MARGIN_PIXELS = 4
GPU_MASK_RESIZE_CHUNK_SIZE = 32
OPENCV_NUM_THREADS = 1
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
TRACKING_INPUT_CONFIDENCE_THRESHOLD = 0.6
UNTRACKED_DETECTION_ID = -2
UNCLIPPED_XYXY_DATA_KEY = "unclipped_xyxy"

# ===========================================================================
# Conveyor-position gates
# ===========================================================================
# Ratios are intentionally not CLI options. They are loaded together with the
# adjusted-canvas margin from config/adjust_calibration_margin.json so all runs
# use the same physical positions on the conveyor body.
POSITION_GATE_VERSION = "conveyor_position_gate_v1_20260813"
BEST_SAMPLE_CROP_SCALE = 1.20


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Track fish in an ordered image sequence with RF-DETR Seg Medium, "
            "TensorRT batch=64, and ByteTrack."
        )
    )
    parser.add_argument("--video-id", default=None,
                        help="pipeline video ID; resolves the standard job input/output paths")
    parser.add_argument(
        "--input-dir",
        "--input_dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="input frames; defaults to data/jobs/<video-id>/frames/adjusted with --video-id",
    )
    parser.add_argument("--input-image", "--input_image", dest="input_image", type=Path,
                        default=None, help="process one image instead of a directory")
    parser.add_argument(
        "--checkpoint",
        "--engine",
        type=Path,
        default=DEFAULT_CHECKPOINT,
        help="RF-DETR Seg Medium TensorRT engine",
    )
    parser.add_argument(
        "--output-dir",
        "--output_dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="annotated-frame output directory; matches Grounded-SAM --output-dir",
    )
    parser.add_argument("--video-output", "--video_output", dest="video_output", type=Path,
                        default=None, help="exact output video path")
    parser.add_argument("--annotated-output-dir", dest="annotated_output_dir", type=Path,
                        default=None, help="directory for 12-digit *_grounded_sam.jpg frames")
    parser.add_argument(
        "--first-match-output-dir",
        "--first_match_output_dir",
        type=Path,
        default=DEFAULT_FIRST_MATCH_OUTPUT_DIR,
        help=(
            "first_match output; defaults to data/jobs/<video-id>/crop_images/first_match"
        ),
    )
    parser.add_argument(
        "--frame-rate",
        "--video-fps",
        "--video_fps",
        type=float,
        default=10.0,
        help=(
            "logical frame rate used by ByteTrack and used as the output-video frame rate; "
            "the input is an image sequence rather than a video"
        ),
    )
    parser.add_argument(
        "--ffmpeg-bin",
        type=str,
        default="ffmpeg",
        help="FFmpeg executable name or absolute path",
    )
    parser.add_argument(
        "--video-encoder",
        choices=("auto", "h264_nvenc", "libx264"),
        default="auto",
        help=(
            "FFmpeg video encoder. 'auto' prefers NVIDIA h264_nvenc and "
            "falls back to CPU libx264 when NVENC is unavailable."
        ),
    )

    # RF-DETR keeps lower-confidence segmentation results for visualization;
    # ByteTrack receives only detections above TRACKING_INPUT_CONFIDENCE_THRESHOLD.
    parser.add_argument(
        "--model-threshold",
        "--conf",
        "--box-threshold",
        "--box_threshold",
        dest="model_threshold",
        type=float,
        default=0.5,
        help="RF-DETR segmentation prediction threshold",
    )
    parser.add_argument(
        "--top-edge-ratio",
        "--top_edge_ratio",
        type=float,
        default=0.0,
        help="shrink the effective detection region from the top by this image-height ratio",
    )
    parser.add_argument(
        "--bottom-edge-ratio",
        "--bottom_edge_ratio",
        type=float,
        default=0.0,
        help="shrink the effective detection region from the bottom by this image-height ratio",
    )
    parser.add_argument(
        "--left-edge-ratio",
        "--left_edge_ratio",
        type=float,
        default=0.0,
        help="expand the allowed detection region beyond the left edge by this image-width ratio",
    )
    parser.add_argument(
        "--right-edge-ratio",
        "--right_edge_ratio",
        type=float,
        default=0.0,
        help="expand the allowed detection region beyond the right edge by this image-width ratio",
    )
    parser.add_argument(
        "--track-activation-threshold",
        type=float,
        default=0.10,
        help="minimum confidence used by ByteTrack",
    )
    parser.add_argument(
        "--high-conf-threshold",
        type=float,
        default=0.40,
        help="boundary between ByteTrack high- and low-confidence detections",
    )
    parser.add_argument(
        "--lost-track-buffer",
        type=int,
        default=60,
        help="images for which ByteTrack keeps an unmatched track alive",
    )
    parser.add_argument(
        "--minimum-consecutive-frames",
        type=int,
        default=2,
        help="consecutive matches required before a track is confirmed",
    )
    parser.add_argument(
        "--minimum-iou-threshold",
        type=float,
        default=0.10,
        help="minimum IoU used for ByteTrack association",
    )

    parser.add_argument(
        "--best-sample-mode",
        choices=("position",),
        default="position",
        help=(
            "fish crop selection strategy; the position is read from "
            "adjust_calibration_margin.json"
        ),
    )

    parser.add_argument(
        "--fp16",
        action="store_true",
        help="report that the TensorRT engine precision is fixed at build time",
    )
    parser.add_argument("--mask-alpha", type=float, default=0.6, help="mask opacity in [0, 1]")
    parser.add_argument(
        "--preview",
        action="store_true",
        help="show the visualized tracking frames; press q or Esc to stop",
    )
    parser.add_argument(
        "--max-images",
        "--max-frames",
        dest="max_images",
        type=int,
        default=None,
        help="optional maximum number of images, useful for a quick test",
    )
    parser.add_argument(
        "--timing-warmup-frames",
        type=int,
        default=10,
        help="exclude this many initial images from steady-state timing statistics",
    )
    # Accepted for drop-in CLI compatibility with grounded_sam_mmdet_batch.
    # RF-DETR is a closed-vocabulary instance-segmentation model, so these
    # GroundingDINO/SAM-specific switches intentionally do not affect inference.
    parser.add_argument("--config", "--grounded-config", dest="config", default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument("--grounded-checkpoint", "--grounded_checkpoint",
                        dest="grounded_checkpoint", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--sam-version", "--sam_version", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--sam-checkpoint", "--sam_checkpoint", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--sam-hq-checkpoint", "--sam_hq_checkpoint", default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument("--use-sam-hq", "--use_sam_hq", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--text-prompt", "--text_prompt", default="fish.", help=argparse.SUPPRESS)
    parser.add_argument("--text-threshold", "--text_threshold", type=float, default=0.25,
                        help=argparse.SUPPRESS)
    parser.add_argument("--device", default="cuda", help=argparse.SUPPRESS)
    parser.add_argument("--frame-start", "--frame_start", type=int, default=0)
    parser.add_argument("--frame-end", "--frame_end", type=int, default=10**6)
    parser.add_argument("--transform-matrix", "--transform_matrix", default=None,
                        help=argparse.SUPPRESS)
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    if not args.input_dir.is_dir():
        raise FileNotFoundError(f"Input image directory not found: {args.input_dir}")
    if not args.checkpoint.is_file():
        raise FileNotFoundError(f"Checkpoint not found: {args.checkpoint}")

    missing = []
    if trt is None:
        missing.append("tensorrt")
    if sv is None:
        missing.append("supervision")
    if ByteTrackTracker is None:
        missing.append("trackers>=2.4.0")
    if missing:
        raise RuntimeError(
            "Missing TensorRT pipeline dependencies: " + ", ".join(missing) +
            ". Activate the RF-DETR/TensorRT environment before running inference."
        )

    probability_fields = {
        "--model-threshold": args.model_threshold,
        "--track-activation-threshold": args.track_activation_threshold,
        "--high-conf-threshold": args.high_conf_threshold,
        "--minimum-iou-threshold": args.minimum_iou_threshold,
        "--mask-alpha": args.mask_alpha,
        "--top-edge-ratio": args.top_edge_ratio,
        "--bottom-edge-ratio": args.bottom_edge_ratio,
        "--left-edge-ratio": args.left_edge_ratio,
        "--right-edge-ratio": args.right_edge_ratio,
    }
    for name, value in probability_fields.items():
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be in [0, 1], got {value}")

    edge_ratios = {
        "--top-edge-ratio": args.top_edge_ratio,
        "--bottom-edge-ratio": args.bottom_edge_ratio,
        "--left-edge-ratio": args.left_edge_ratio,
        "--right-edge-ratio": args.right_edge_ratio,
    }
    for name, value in edge_ratios.items():
        if value >= 1.0:
            raise ValueError(f"{name} must be smaller than 1.0, got {value}")
    if args.top_edge_ratio + args.bottom_edge_ratio >= 1.0:
        raise ValueError(
            "--top-edge-ratio plus --bottom-edge-ratio must be smaller than 1.0"
        )

    if args.track_activation_threshold >= args.high_conf_threshold:
        raise ValueError(
            "--track-activation-threshold must be smaller than --high-conf-threshold "
            "so ByteTrack has separate low- and high-confidence stages."
        )
    if args.model_threshold > TRACKING_INPUT_CONFIDENCE_THRESHOLD:
        print(
            "[WARN] --model-threshold is above the tracker input threshold. "
            "RF-DETR will discard some detections before they can reach ByteTrack.",
            file=sys.stderr,
        )
    if args.lost_track_buffer < 0:
        raise ValueError("--lost-track-buffer must be >= 0")
    if args.minimum_consecutive_frames < 1:
        raise ValueError("--minimum-consecutive-frames must be >= 1")
    if args.frame_rate <= 0:
        raise ValueError("--frame-rate must be > 0")
    if args.max_images is not None and args.max_images < 1:
        raise ValueError("--max-images must be >= 1")
    if args.timing_warmup_frames < 0:
        raise ValueError("--timing-warmup-frames must be >= 0")


def synchronize_cuda() -> None:
    """Synchronize CUDA so measured inference time includes completed GPU work."""
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def load_conveyor_position_config(
    config_path: Path = ADJUST_CALIBRATION_MARGIN_CONFIG_PATH,
) -> dict[str, float]:
    """Load canvas margin and conveyor-position gates from the shared JSON."""
    try:
        with config_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Could not read conveyor position config: {config_path}: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError(
            f"Conveyor position config must contain a JSON object: {config_path}"
        )

    required_keys = (
        "vertical_margin_ratio",
        "tracking_start_ratio",
        "crop_save_ratio",
    )
    missing_keys = [key for key in required_keys if key not in payload]
    if missing_keys:
        raise RuntimeError(
            f"Conveyor position config is missing {missing_keys}: {config_path}"
        )

    values: dict[str, float] = {}
    for key in required_keys:
        raw_value = payload[key]
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise RuntimeError(
                f"Conveyor position config {key} must be numeric, got "
                f"{raw_value!r}: {config_path}"
            )
        value = float(raw_value)
        if not np.isfinite(value):
            raise RuntimeError(
                f"Conveyor position config {key} must be finite, got "
                f"{raw_value!r}: {config_path}"
            )
        values[key] = value

    vertical_margin_ratio = values["vertical_margin_ratio"]
    if not 0.0 <= vertical_margin_ratio <= 2.0:
        raise RuntimeError(
            "Conveyor position config vertical_margin_ratio must be in [0, 2], "
            f"got {vertical_margin_ratio}: {config_path}"
        )

    for key in ("tracking_start_ratio", "crop_save_ratio"):
        if not 0.0 <= values[key] <= 1.0:
            raise RuntimeError(
                f"Conveyor position config {key} must be in [0, 1], "
                f"got {values[key]}: {config_path}"
            )

    if values["tracking_start_ratio"] >= values["crop_save_ratio"]:
        raise RuntimeError(
            "Conveyor position config tracking_start_ratio must be smaller than "
            f"crop_save_ratio: {config_path}"
        )
    return values


def natural_image_sort_key(path: Path) -> tuple[Any, ...]:
    """Sort names such as 1.jpg, 2.jpg, 10.jpg in temporal order."""
    relative_text = path.as_posix().lower()
    parts = re.split(r"(\d+)", relative_text)
    return tuple(int(part) if part.isdigit() else part for part in parts)


def collect_image_paths(input_dir: Path) -> list[Path]:
    """Collect supported images recursively and return them in natural order."""
    image_paths = [
        path
        for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    image_paths.sort(key=lambda path: natural_image_sort_key(path.relative_to(input_dir)))
    return image_paths


class TensorRTRFDETRSegMedium:
    """
    TensorRT 10.x wrapper that preserves the RF-DETR ``predict`` interface
    required by the existing tracking pipeline.

    The TensorRT engine returns the same raw tensors as the exported model:
    ``dets`` (normalized cxcywh boxes), ``labels`` (classification logits),
    and ``masks`` (mask logits). This wrapper applies the same post-processing
    as the previous ONNX implementation and returns ``supervision.Detections``.

    This implementation uses a fixed runtime batch size of 64.
    """

    means = np.asarray([0.485, 0.456, 0.406], dtype=np.float32)
    stds = np.asarray([0.229, 0.224, 0.225], dtype=np.float32)
    num_select = 300

    def __init__(
        self,
        checkpoint: Path,
        use_fp16: bool = False,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self.checkpoint = Path(checkpoint)
        self.batch_size = int(batch_size)
        if self.batch_size != BATCH_SIZE:
            raise ValueError(f"This script is configured for batch={BATCH_SIZE}, got {self.batch_size}.")
        self.class_names = ["fish"]
        self.device = torch.device("cuda:0")

        if self.checkpoint.suffix.lower() not in {".engine", ".plan"}:
            raise ValueError(
                "Expected a TensorRT engine ending in .engine or .plan, "
                f"got: {self.checkpoint}"
            )
        if not torch.cuda.is_available():
            raise RuntimeError("TensorRT inference requires a CUDA-capable GPU.")

        torch.cuda.set_device(self.device)

        self.logger = trt.Logger(trt.Logger.WARNING)
        self.runtime = trt.Runtime(self.logger)

        print(f"[INFO] Loading TensorRT engine: {self.checkpoint}")
        engine_bytes = self.checkpoint.read_bytes()
        self.engine = self.runtime.deserialize_cuda_engine(engine_bytes)
        if self.engine is None:
            raise RuntimeError(
                f"TensorRT could not deserialize engine: {self.checkpoint}"
            )

        self.context = self.engine.create_execution_context()
        if self.context is None:
            raise RuntimeError("TensorRT could not create an execution context.")

        self.input_names: list[str] = []
        self.output_names: list[str] = []

        for index in range(self.engine.num_io_tensors):
            name = self.engine.get_tensor_name(index)
            mode = self.engine.get_tensor_mode(name)
            if mode == trt.TensorIOMode.INPUT:
                self.input_names.append(name)
            else:
                self.output_names.append(name)

        if len(self.input_names) != 1:
            raise RuntimeError(
                f"Expected one TensorRT input, found: {self.input_names}"
            )

        self.input_name = self.input_names[0]
        required_outputs = {"dets", "labels", "masks"}
        missing_outputs = required_outputs.difference(self.output_names)
        if missing_outputs:
            raise RuntimeError(
                "The TensorRT engine is not a complete RF-DETR segmentation "
                f"engine. Missing outputs: {sorted(missing_outputs)}; "
                f"actual outputs: {self.output_names}"
            )

        engine_input_shape = tuple(
            int(value) for value in self.engine.get_tensor_shape(self.input_name)
        )
        if len(engine_input_shape) != 4:
            raise RuntimeError(
                f"Expected TensorRT NCHW input, got: {engine_input_shape}"
            )

        runtime_input_shape = list(engine_input_shape)
        if runtime_input_shape[0] == -1:
            runtime_input_shape[0] = self.batch_size
        elif runtime_input_shape[0] != self.batch_size:
            raise RuntimeError(
                f"This script requires batch={self.batch_size}, but the TensorRT "
                f"engine input batch is {runtime_input_shape[0]}."
            )

        if runtime_input_shape[1] not in (3, -1):
            raise RuntimeError(
                f"Expected three input channels, got: {engine_input_shape}"
            )
        if runtime_input_shape[1] == -1:
            runtime_input_shape[1] = 3

        if runtime_input_shape[2] <= 0 or runtime_input_shape[3] <= 0:
            raise RuntimeError(
                "The TensorRT engine has dynamic spatial dimensions. This code "
                "requires fixed H/W dimensions; rebuild the engine with a fixed "
                f"input shape. Engine shape: {engine_input_shape}"
            )

        self.input_shape = tuple(runtime_input_shape)
        if any(value == -1 for value in engine_input_shape):
            shape_set = self.context.set_input_shape(
                self.input_name,
                self.input_shape,
            )
            if shape_set is False:
                raise RuntimeError(
                    f"Could not set TensorRT input shape to {self.input_shape}."
                )

        self.input_height = int(self.input_shape[2])
        self.input_width = int(self.input_shape[3])

        self.tensor_buffers: dict[str, torch.Tensor] = {}
        self._allocate_and_bind_buffers()

        if use_fp16:
            print(
                "[INFO] --fp16 was specified. TensorRT precision is fixed when "
                "the engine is built; the runtime will use the engine's precision."
            )

        print(f"[INFO] TensorRT version: {trt.__version__}")
        print(
            f"[INFO] TensorRT input: {self.input_name}, "
            f"shape={self.input_shape}, "
            f"dtype={self.engine.get_tensor_dtype(self.input_name)}"
        )
        for name in self.output_names:
            print(
                f"[INFO] TensorRT output: {name}, "
                f"shape={tuple(self.context.get_tensor_shape(name))}, "
                f"dtype={self.engine.get_tensor_dtype(name)}"
            )

    @staticmethod
    def _torch_dtype(tensorrt_dtype: trt.DataType) -> torch.dtype:
        mapping: dict[trt.DataType, torch.dtype] = {
            trt.float32: torch.float32,
            trt.float16: torch.float16,
            trt.int8: torch.int8,
            trt.int32: torch.int32,
            trt.int64: torch.int64,
            trt.bool: torch.bool,
        }
        if tensorrt_dtype not in mapping:
            raise RuntimeError(
                f"Unsupported TensorRT tensor dtype: {tensorrt_dtype}"
            )
        return mapping[tensorrt_dtype]

    def _allocate_and_bind_buffers(self) -> None:
        for index in range(self.engine.num_io_tensors):
            name = self.engine.get_tensor_name(index)
            shape = tuple(
                int(value) for value in self.context.get_tensor_shape(name)
            )
            if any(value < 0 for value in shape):
                raise RuntimeError(
                    f"TensorRT tensor {name} still has unresolved shape: {shape}"
                )

            tensor = torch.empty(
                shape,
                dtype=self._torch_dtype(self.engine.get_tensor_dtype(name)),
                device=self.device,
            )
            self.tensor_buffers[name] = tensor

            bound = self.context.set_tensor_address(
                name,
                int(tensor.data_ptr()),
            )
            if bound is False:
                raise RuntimeError(
                    f"Could not bind TensorRT tensor address for: {name}"
                )

    @staticmethod
    def _sigmoid(values: np.ndarray) -> np.ndarray:
        values = np.asarray(values, dtype=np.float32)
        values = np.clip(values, -50.0, 50.0)
        return 1.0 / (1.0 + np.exp(-values))

    def _preprocess_batch(self, images_rgb: list[np.ndarray]) -> np.ndarray:
        """Preprocess up to 64 RGB images and pad the final batch to 64."""
        actual_batch_size = len(images_rgb)
        if not 1 <= actual_batch_size <= self.batch_size:
            raise ValueError(
                f"Expected 1 to {self.batch_size} images, got {actual_batch_size}."
            )

        batch = np.empty(self.input_shape, dtype=np.float32)
        for index, image_rgb in enumerate(images_rgb):
            resized = cv2.resize(
                image_rgb,
                (self.input_width, self.input_height),
                interpolation=cv2.INTER_LINEAR,
            )
            image = resized.astype(np.float32) / 255.0
            image = (image - self.means) / self.stds
            batch[index] = np.transpose(image, (2, 0, 1))

        # The TensorRT context always executes batch=64. For the final incomplete
        # batch, repeat the last valid image and discard all padded outputs later.
        # This supports both a fixed-batch-64 engine and a dynamic engine set to 64.
        if actual_batch_size < self.batch_size:
            batch[actual_batch_size:] = batch[actual_batch_size - 1]

        return np.ascontiguousarray(batch, dtype=np.float32)

    def _select_topk(
        self,
        logits: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        probabilities = self._sigmoid(logits)

        if probabilities.ndim != 2:
            raise RuntimeError(
                f"Expected label logits with shape (queries, classes), "
                f"got: {probabilities.shape}"
            )

        num_classes = probabilities.shape[1]
        flattened = probabilities.reshape(-1)
        count = min(self.num_select, flattened.size)

        if count == 0:
            return (
                np.empty(0, dtype=np.float32),
                np.empty(0, dtype=np.int64),
                np.empty(0, dtype=np.int64),
            )

        if count == flattened.size:
            topk_indexes = np.argsort(-flattened)
        else:
            topk_indexes = np.argpartition(
                -flattened,
                count - 1,
            )[:count]
            topk_indexes = topk_indexes[
                np.argsort(-flattened[topk_indexes])
            ]

        scores = flattened[topk_indexes].astype(np.float32, copy=False)
        query_indexes = (topk_indexes // num_classes).astype(
            np.int64,
            copy=False,
        )
        class_ids = (topk_indexes % num_classes).astype(
            np.int64,
            copy=False,
        )
        return scores, class_ids, query_indexes

    @staticmethod
    def _boxes_to_xyxy(
        boxes_cxcywh: np.ndarray,
        image_height: int,
        image_width: int,
    ) -> np.ndarray:
        boxes_cxcywh = boxes_cxcywh.astype(np.float32, copy=False)
        center_x = boxes_cxcywh[:, 0]
        center_y = boxes_cxcywh[:, 1]
        box_width = boxes_cxcywh[:, 2]
        box_height = boxes_cxcywh[:, 3]

        boxes_xyxy = np.stack(
            (
                center_x - box_width / 2.0,
                center_y - box_height / 2.0,
                center_x + box_width / 2.0,
                center_y + box_height / 2.0,
            ),
            axis=1,
        )
        boxes_xyxy *= np.asarray(
            [image_width, image_height, image_width, image_height],
            dtype=np.float32,
        )
        boxes_xyxy[:, [0, 2]] = np.clip(
            boxes_xyxy[:, [0, 2]],
            0.0,
            float(image_width),
        )
        boxes_xyxy[:, [1, 3]] = np.clip(
            boxes_xyxy[:, [1, 3]],
            0.0,
            float(image_height),
        )
        return boxes_xyxy.astype(np.float32, copy=False)

    @staticmethod
    def _resize_masks(
        mask_logits: np.ndarray,
        image_height: int,
        image_width: int,
    ) -> np.ndarray:
        masks = np.empty(
            (len(mask_logits), image_height, image_width),
            dtype=bool,
        )

        for index, raw_mask in enumerate(mask_logits):
            raw_mask = np.squeeze(np.asarray(raw_mask))
            if raw_mask.ndim != 2:
                raise RuntimeError(
                    f"Expected a 2D mask logit, got shape: {raw_mask.shape}"
                )

            resized_mask = cv2.resize(
                raw_mask.astype(np.float32, copy=False),
                (image_width, image_height),
                interpolation=cv2.INTER_LINEAR,
            )
            masks[index] = resized_mask > 0.0

        return masks

    def _infer_raw_gpu(self, input_tensor: np.ndarray) -> dict[str, torch.Tensor]:
        """
        Run TensorRT and keep all raw outputs in their preallocated GPU buffers.

        The original implementation converted every output, including all mask
        queries, to float32 on the GPU and then copied everything to the CPU.
        This version leaves the outputs on the GPU so filtering can happen first.
        """
        input_buffer = self.tensor_buffers[self.input_name]
        host_tensor = torch.from_numpy(input_tensor)

        # Copy directly from the NumPy-backed CPU tensor into the already-bound
        # TensorRT input buffer. This avoids creating a temporary CUDA tensor and
        # then performing an additional device-to-device copy.
        input_buffer.copy_(host_tensor, non_blocking=False)

        stream = torch.cuda.current_stream(self.device)
        success = self.context.execute_async_v3(
            stream_handle=stream.cuda_stream
        )
        if not success:
            raise RuntimeError("TensorRT execute_async_v3() failed.")

        # Do not synchronize here. The GPU post-processing operations below use
        # the same CUDA stream and are therefore ordered after TensorRT inference.
        return self.tensor_buffers

    @staticmethod
    def _select_topk_gpu(
        logits: torch.Tensor,
        num_select: int,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Apply the original sigmoid/Top-K logic on the GPU in float32."""
        logits_float = logits.to(dtype=torch.float32).clamp(-50.0, 50.0)
        probabilities = torch.sigmoid(logits_float)

        if probabilities.ndim != 2:
            raise RuntimeError(
                "Expected label logits with shape (queries, classes), "
                f"got: {tuple(probabilities.shape)}"
            )

        num_classes = int(probabilities.shape[1])
        flattened = probabilities.reshape(-1)
        count = min(int(num_select), int(flattened.numel()))

        if count == 0:
            return (
                torch.empty(0, dtype=torch.float32, device=logits.device),
                torch.empty(0, dtype=torch.int64, device=logits.device),
                torch.empty(0, dtype=torch.int64, device=logits.device),
            )

        scores, topk_indexes = torch.topk(
            flattened,
            k=count,
            largest=True,
            sorted=True,
        )
        query_indexes = torch.div(
            topk_indexes,
            num_classes,
            rounding_mode="floor",
        ).to(dtype=torch.int64)
        class_ids = torch.remainder(topk_indexes, num_classes).to(
            dtype=torch.int64
        )
        return scores, class_ids, query_indexes

    @staticmethod
    def _boxes_to_xyxy_gpu(
        boxes_cxcywh: torch.Tensor,
        image_height: int,
        image_width: int,
        clip: bool = True,
    ) -> torch.Tensor:
        """Convert normalized cxcywh boxes to pixel xyxy boxes on GPU."""
        boxes = boxes_cxcywh.to(dtype=torch.float32)
        center_x, center_y, box_width, box_height = boxes.unbind(dim=1)

        boxes_xyxy = torch.stack(
            (
                center_x - box_width / 2.0,
                center_y - box_height / 2.0,
                center_x + box_width / 2.0,
                center_y + box_height / 2.0,
            ),
            dim=1,
        )
        # Multiply columns directly to avoid allocating a new scale tensor on
        # every image.
        boxes_xyxy[:, 0].mul_(float(image_width))
        boxes_xyxy[:, 2].mul_(float(image_width))
        boxes_xyxy[:, 1].mul_(float(image_height))
        boxes_xyxy[:, 3].mul_(float(image_height))
        if clip:
            boxes_xyxy[:, 0].clamp_(0.0, float(image_width))
            boxes_xyxy[:, 2].clamp_(0.0, float(image_width))
            boxes_xyxy[:, 1].clamp_(0.0, float(image_height))
            boxes_xyxy[:, 3].clamp_(0.0, float(image_height))
        return boxes_xyxy

    @staticmethod
    def _empty_detections(
        image_height: int,
        image_width: int,
    ) -> sv.Detections:
        detections = sv.Detections(
            xyxy=np.empty((0, 4), dtype=np.float32),
            confidence=np.empty(0, dtype=np.float32),
            class_id=np.empty(0, dtype=np.int64),
            mask=np.empty((0, image_height, image_width), dtype=bool),
        )
        detections.data["class_name"] = np.empty(0, dtype=object)
        detections.data[UNCLIPPED_XYXY_DATA_KEY] = np.empty((0, 4), dtype=np.float32)
        return detections

    @torch.inference_mode()
    def predict_batch(
        self,
        images_rgb: list[np.ndarray],
        threshold: float = 0.5,
    ) -> list[sv.Detections]:
        """Run one TensorRT batch and return full-resolution masks in source order."""
        actual_batch_size = len(images_rgb)
        if not 1 <= actual_batch_size <= self.batch_size:
            raise ValueError(
                f"Expected 1 to {self.batch_size} RGB images, got {actual_batch_size}."
            )

        image_sizes: list[tuple[int, int]] = []
        normalized_images: list[np.ndarray] = []
        for image_rgb in images_rgb:
            image_rgb = np.asarray(image_rgb)
            if image_rgb.ndim != 3 or image_rgb.shape[2] != 3:
                raise ValueError(
                    "TensorRTRFDETRSegMedium.predict_batch() expects RGB images "
                    f"with shape (H, W, 3), got: {image_rgb.shape}"
                )
            image_sizes.append((int(image_rgb.shape[0]), int(image_rgb.shape[1])))
            normalized_images.append(image_rgb)

        input_tensor = self._preprocess_batch(normalized_images)
        outputs = self._infer_raw_gpu(input_tensor)

        raw_boxes = outputs["dets"]
        raw_logits = outputs["labels"]
        raw_masks = outputs["masks"]

        if raw_boxes.ndim != 3 or raw_boxes.shape[0] != self.batch_size:
            raise RuntimeError(
                f"Expected dets shape ({self.batch_size}, queries, 4), "
                f"got: {tuple(raw_boxes.shape)}"
            )
        if raw_logits.ndim != 3 or raw_logits.shape[0] != self.batch_size:
            raise RuntimeError(
                f"Expected labels shape ({self.batch_size}, queries, classes), "
                f"got: {tuple(raw_logits.shape)}"
            )
        if raw_masks.ndim not in (4, 5) or raw_masks.shape[0] != self.batch_size:
            raise RuntimeError(
                f"Expected masks shape ({self.batch_size}, queries, H, W), "
                f"got: {tuple(raw_masks.shape)}"
            )

        raw_boxes = raw_boxes[:actual_batch_size]
        raw_logits = raw_logits[:actual_batch_size]
        raw_masks = raw_masks[:actual_batch_size]
        if raw_masks.ndim == 5 and raw_masks.shape[2] == 1:
            raw_masks = raw_masks[:, :, 0]

        # Vectorized GPU sigmoid and Top-K across all real images in this batch.
        logits_float = raw_logits.to(dtype=torch.float32).clamp(-50.0, 50.0)
        probabilities = torch.sigmoid(logits_float)
        num_classes = int(probabilities.shape[2])
        flattened = probabilities.reshape(actual_batch_size, -1)
        topk_count = min(self.num_select, int(flattened.shape[1]))

        scores_all, topk_indexes_all = torch.topk(
            flattened,
            k=topk_count,
            dim=1,
            largest=True,
            sorted=True,
        )
        query_indexes_all = torch.div(
            topk_indexes_all,
            num_classes,
            rounding_mode="floor",
        ).to(dtype=torch.int64)
        class_ids_all = torch.remainder(
            topk_indexes_all,
            num_classes,
        ).to(dtype=torch.int64)

        boxes_parts: list[torch.Tensor] = []
        unclipped_boxes_parts: list[torch.Tensor] = []
        scores_parts: list[torch.Tensor] = []
        class_id_parts: list[torch.Tensor] = []
        mask_parts: list[torch.Tensor] = []
        selected_counts: list[int] = []

        for batch_index, (image_height, image_width) in enumerate(image_sizes):
            keep_gpu = scores_all[batch_index] > float(threshold)
            scores_gpu = scores_all[batch_index][keep_gpu]
            class_ids_gpu = class_ids_all[batch_index][keep_gpu]
            query_indexes_gpu = query_indexes_all[batch_index][keep_gpu]
            selected_count = int(query_indexes_gpu.numel())
            selected_counts.append(selected_count)

            if selected_count == 0:
                continue

            selected_boxes_gpu = raw_boxes[batch_index].index_select(
                0, query_indexes_gpu
            )
            selected_masks_gpu = raw_masks[batch_index].index_select(
                0, query_indexes_gpu
            )
            unclipped_boxes_xyxy_gpu = self._boxes_to_xyxy_gpu(
                selected_boxes_gpu,
                image_height=image_height,
                image_width=image_width,
                clip=False,
            )
            boxes_xyxy_gpu = unclipped_boxes_xyxy_gpu.clone()
            boxes_xyxy_gpu[:, 0].clamp_(0.0, float(image_width))
            boxes_xyxy_gpu[:, 2].clamp_(0.0, float(image_width))
            boxes_xyxy_gpu[:, 1].clamp_(0.0, float(image_height))
            boxes_xyxy_gpu[:, 3].clamp_(0.0, float(image_height))

            boxes_parts.append(boxes_xyxy_gpu)
            unclipped_boxes_parts.append(unclipped_boxes_xyxy_gpu)
            scores_parts.append(scores_gpu)
            class_id_parts.append(class_ids_gpu)
            mask_parts.append(selected_masks_gpu)

        if not boxes_parts:
            torch.cuda.current_stream(self.device).synchronize()
            return [
                self._empty_detections(height, width)
                for height, width in image_sizes
            ]

        # Restore the original full-resolution mask path. All sequence images
        # must have the same size so selected masks from the complete TensorRT
        # batch can be resized together in bounded CUDA chunks.
        unique_image_sizes = set(image_sizes)
        if len(unique_image_sizes) != 1:
            raise RuntimeError(
                "GPU mask batching requires all images in one batch to have the "
                f"same size, got: {sorted(unique_image_sizes)}"
            )

        target_height, target_width = image_sizes[0]
        boxes_cpu = torch.cat(boxes_parts, dim=0).cpu().numpy()
        unclipped_boxes_cpu = torch.cat(unclipped_boxes_parts, dim=0).cpu().numpy()
        scores_cpu = torch.cat(scores_parts, dim=0).cpu().numpy()
        class_ids_cpu = torch.cat(class_id_parts, dim=0).cpu().numpy()

        selected_masks_gpu = torch.cat(mask_parts, dim=0).to(dtype=torch.float32)
        if selected_masks_gpu.ndim != 3:
            raise RuntimeError(
                "Expected selected mask logits with shape (objects, H, W), "
                f"got: {tuple(selected_masks_gpu.shape)}"
            )

        resized_mask_chunks: list[np.ndarray] = []
        for chunk_start in range(
            0,
            int(selected_masks_gpu.shape[0]),
            GPU_MASK_RESIZE_CHUNK_SIZE,
        ):
            chunk = selected_masks_gpu[
                chunk_start : chunk_start + GPU_MASK_RESIZE_CHUNK_SIZE
            ]
            resized_chunk = F.interpolate(
                chunk.unsqueeze(1),
                size=(target_height, target_width),
                mode="bilinear",
                align_corners=False,
            )[:, 0]
            resized_mask_chunks.append((resized_chunk > 0.0).cpu().numpy())

        masks_cpu = np.concatenate(resized_mask_chunks, axis=0)

        detections_batch: list[sv.Detections] = []
        offset = 0
        for (image_height, image_width), selected_count in zip(
            image_sizes,
            selected_counts,
        ):
            if selected_count == 0:
                detections_batch.append(
                    self._empty_detections(image_height, image_width)
                )
                continue

            next_offset = offset + selected_count
            boxes_xyxy = boxes_cpu[offset:next_offset]
            unclipped_boxes_xyxy = unclipped_boxes_cpu[offset:next_offset]
            scores = scores_cpu[offset:next_offset]
            class_ids = class_ids_cpu[offset:next_offset]
            masks = masks_cpu[offset:next_offset]
            offset = next_offset

            detections = sv.Detections(
                xyxy=boxes_xyxy.astype(np.float32, copy=False),
                confidence=scores.astype(np.float32, copy=False),
                class_id=class_ids.astype(np.int64, copy=False),
                mask=masks.astype(bool, copy=False),
            )
            detections.data["class_name"] = np.asarray(
                [
                    self.class_names[class_id]
                    if 0 <= class_id < len(self.class_names)
                    else f"class_{class_id}"
                    for class_id in detections.class_id
                ],
                dtype=object,
            )
            detections.data[UNCLIPPED_XYXY_DATA_KEY] = unclipped_boxes_xyxy.astype(
                np.float32,
                copy=False,
            )
            detections_batch.append(detections)

        return detections_batch


def load_rfdetr_model(
    checkpoint: Path,
    use_fp16: bool,
) -> TensorRTRFDETRSegMedium:
    """Load an RF-DETR segmentation TensorRT engine with batch size 64."""
    return TensorRTRFDETRSegMedium(
        checkpoint=checkpoint,
        use_fp16=use_fp16,
        batch_size=BATCH_SIZE,
    )


def build_tracker(args: argparse.Namespace, fps: float) -> ByteTrackTracker:
    """Build ByteTrack with no additional custom association constraints."""
    return ByteTrackTracker(
        lost_track_buffer=args.lost_track_buffer,
        frame_rate=fps,
        track_activation_threshold=args.track_activation_threshold,
        minimum_consecutive_frames=args.minimum_consecutive_frames,
        minimum_iou_threshold=args.minimum_iou_threshold,
        high_conf_det_threshold=args.high_conf_threshold,
    )


def filter_detections_by_effective_region(
    detections: sv.Detections,
    image_height: int,
    image_width: int,
    *,
    top_ratio: float,
    bottom_ratio: float,
    left_ratio: float,
    right_ratio: float,
) -> sv.Detections:
    """Keep boxes fully contained by the configured effective detection region.

    Top and bottom ratios contract the region into the image. Left and right
    ratios extend it beyond the image, using pre-clipping model boxes so an
    extension can admit a prediction that slightly crosses a side boundary.
    All-zero ratios disable this filter and preserve the original behavior.
    """
    if len(detections) == 0 or not any(
        (top_ratio, bottom_ratio, left_ratio, right_ratio)
    ):
        return detections

    boxes = np.asarray(
        detections.data.get(UNCLIPPED_XYXY_DATA_KEY, detections.xyxy),
        dtype=np.float32,
    )
    left = -float(left_ratio) * float(image_width)
    right = (1.0 + float(right_ratio)) * float(image_width)
    top = float(top_ratio) * float(image_height)
    bottom = (1.0 - float(bottom_ratio)) * float(image_height)
    keep = (
        (boxes[:, 0] >= left)
        & (boxes[:, 1] >= top)
        & (boxes[:, 2] <= right)
        & (boxes[:, 3] <= bottom)
    )
    return detections[keep]


def conveyor_body_progress(
    mask: Any,
    image_height: int,
    image_width: int,
    *,
    vertical_margin_ratio: float,
) -> float | None:
    """Return where the middle of a fish lies along the conveyor body.

    Adjusted frames contain a top and bottom safety margin, each measured as
    ``vertical_margin_ratio * belt_height``. Progress 0 is therefore the top of
    the conveyor itself and progress 1 is its bottom, rather than the edges of
    the full adjusted canvas.
    """
    mask_bool = ensure_2d_mask(mask, image_height, image_width)
    ys = np.flatnonzero(np.any(mask_bool, axis=1))
    if ys.size == 0:
        return None

    fish_middle_y = 0.5 * (float(ys[0]) + float(ys[-1]))
    belt_height = float(image_height) / (1.0 + 2.0 * vertical_margin_ratio)
    belt_top = vertical_margin_ratio * belt_height
    return (fish_middle_y - belt_top) / belt_height


def filter_detections_by_conveyor_progress(
    detections: sv.Detections,
    image_height: int,
    image_width: int,
    *,
    minimum_progress_ratio: float,
    vertical_margin_ratio: float,
) -> sv.Detections:
    """Keep fish whose body midpoint has passed the configured belt position."""
    if len(detections) == 0:
        return detections

    if detections.mask is None:
        raise RuntimeError(
            "Conveyor position filtering requires instance-segmentation masks."
        )

    keep = np.zeros(len(detections), dtype=bool)
    for index, mask in enumerate(detections.mask):
        progress = conveyor_body_progress(
            mask,
            image_height,
            image_width,
            vertical_margin_ratio=vertical_margin_ratio,
        )
        # "Passed" is deliberately strict: a midpoint exactly on the line has
        # not passed it yet.
        keep[index] = progress is not None and progress > minimum_progress_ratio
    return detections[keep]


def split_detections_for_tracking(
    detections: sv.Detections,
) -> tuple[sv.Detections, sv.Detections]:
    """Return ByteTrack candidates and segmentation-only detections."""
    if detections.confidence is None:
        tracking_mask = np.zeros(len(detections), dtype=bool)
    else:
        confidences = np.asarray(detections.confidence, dtype=np.float32)
        tracking_mask = confidences > TRACKING_INPUT_CONFIDENCE_THRESHOLD

    tracking_candidates = detections[tracking_mask]
    untracked_detections = detections[~tracking_mask]
    if len(untracked_detections) > 0:
        untracked_detections.tracker_id = np.full(
            len(untracked_detections),
            UNTRACKED_DETECTION_ID,
            dtype=np.int64,
        )
    return tracking_candidates, untracked_detections


def merge_tracked_and_untracked_detections(
    tracked_detections: sv.Detections,
    untracked_detections: sv.Detections,
) -> sv.Detections:
    """Keep all segmentation results while retaining IDs only for tracked ones."""
    return sv.Detections.merge([tracked_detections, untracked_detections])



CURRENT_IMG_WIDTH: int | None = None
CURRENT_IMG_DPI: float | None = None


def get_track_color(track_id: int | None, alpha: float = 0.6) -> np.ndarray:
    """Use the exact deterministic color rule from the Grounded-SAM script."""
    if track_id is None:
        return np.array([30 / 255, 144 / 255, 255 / 255, alpha])

    track_index = abs(int(track_id))
    rng = np.random.default_rng((track_index + 1) * 9973 + 17)
    color = rng.random(3) * 0.6 + 0.25
    return np.concatenate([color, np.array([alpha])], axis=0)


def show_mask(mask: np.ndarray, ax: Any, color: np.ndarray | None = None) -> None:
    """Draw a mask exactly like the Grounded-SAM visualization."""
    if color is None:
        color = np.array([30 / 255, 144 / 255, 255 / 255, 0.6])
    h, w = mask.shape[-2:]
    mask_image = mask.reshape(h, w, 1) * color.reshape(1, 1, -1)
    ax.imshow(mask_image)


def show_box(
    box: np.ndarray,
    ax: Any,
    label: str,
    color: np.ndarray | None = None,
) -> None:
    """Draw a box and label exactly like the Grounded-SAM script."""
    x0, y0 = box[0], box[1]
    width, height = box[2] - box[0], box[3] - box[1]
    if color is None:
        color = np.array([0.0, 0.5, 0.0, 1.0])

    ax.add_patch(
        plt.Rectangle(
            (x0, y0),
            width,
            height,
            edgecolor=color[:3],
            facecolor=(0, 0, 0, 0),
            lw=2,
        )
    )

    fontsize = 10
    dpi = CURRENT_IMG_DPI or plt.gcf().dpi
    image_width = CURRENT_IMG_WIDTH
    char_px = fontsize * (dpi / 72.0) * 0.6

    try:
        max_chars_right = (
            int((image_width - x0) / char_px)
            if image_width is not None
            else None
        )
    except Exception:
        max_chars_right = None

    if max_chars_right is None or max_chars_right <= 0:
        text_to_draw = label
        text_x = x0
        text_y = y0
    elif len(label) > max_chars_right:
        first = label[:max_chars_right].rstrip()
        rest = label[max_chars_right:].lstrip()
        text_to_draw = first + "\n" + rest
        text_x = x0
        line_height_px = fontsize * (dpi / 72.0) * 1.2
        text_y = y0 - line_height_px
    else:
        text_to_draw = label
        text_x = x0
        text_y = y0

    ax.text(
        text_x,
        text_y,
        text_to_draw,
        color=color[:3],
        fontsize=fontsize,
        bbox={"facecolor": "black", "alpha": 0.35, "pad": 1},
        va="top",
    )


def ensure_2d_mask(mask: Any, height: int, width: int) -> np.ndarray:
    array = np.asarray(mask)
    if array.ndim == 3 and array.shape[0] == 1:
        array = array[0]
    if array.ndim != 2:
        raise ValueError(f"Expected a 2D mask, got shape {array.shape}")
    if array.shape != (height, width):
        array = cv2.resize(
            array.astype(np.uint8),
            (width, height),
            interpolation=cv2.INTER_NEAREST,
        )
    return array.astype(bool)


@lru_cache(maxsize=None)
def track_color(track_id: int) -> tuple[int, int, int]:
    """Return a deterministic BGR color; the same confirmed ID keeps its color."""
    if track_id < 0:
        return 160, 160, 160

    hue = int((track_id * 47 + 13) % 180)
    hsv = np.uint8([[[hue, 210, 245]]])
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]
    return int(bgr[0]), int(bgr[1]), int(bgr[2])


def class_name_for(model: Any, class_id: int) -> str:
    names = getattr(model, "class_names", None)
    if names and 0 <= class_id < len(names):
        return str(names[class_id])
    return "fish" if class_id == 0 else f"class_{class_id}"


def annotate_frame(
    frame_bgr: np.ndarray,
    detections: sv.Detections,
    model: Any,
    mask_alpha: float,
) -> np.ndarray:
    """Draw full-resolution masks, contours, boxes, IDs, and labels in place."""
    # The submitted BGR frame is owned exclusively by this render worker.
    # Drawing in place avoids the original full-frame ``frame_bgr.copy()``.
    canvas = frame_bgr
    height, width = canvas.shape[:2]
    count = len(detections)
    if count == 0:
        return canvas

    boxes = np.asarray(detections.xyxy, dtype=np.float32)
    confidences = (
        np.asarray(detections.confidence, dtype=np.float32)
        if detections.confidence is not None
        else np.ones(count, dtype=np.float32)
    )
    class_ids = (
        np.asarray(detections.class_id, dtype=np.int64)
        if detections.class_id is not None
        else np.zeros(count, dtype=np.int64)
    )
    tracker_ids = (
        np.asarray(detections.tracker_id, dtype=np.int64)
        if detections.tracker_id is not None
        else np.full(count, -1, dtype=np.int64)
    )
    colors = [track_color(int(track_id)) for track_id in tracker_ids]

    # Keep the original visualization rule: each full-size mask is only blended
    # and scanned inside its detection box plus ROI_MARGIN_PIXELS.
    if detections.mask is not None:
        for raw_mask, box, color_tuple in zip(
            detections.mask,
            boxes,
            colors,
        ):
            mask = ensure_2d_mask(raw_mask, height, width)
            roi_x1 = max(0, int(np.floor(float(box[0]))) - ROI_MARGIN_PIXELS)
            roi_y1 = max(0, int(np.floor(float(box[1]))) - ROI_MARGIN_PIXELS)
            roi_x2 = min(width, int(np.ceil(float(box[2]))) + ROI_MARGIN_PIXELS)
            roi_y2 = min(height, int(np.ceil(float(box[3]))) + ROI_MARGIN_PIXELS)
            if roi_x2 <= roi_x1 or roi_y2 <= roi_y1:
                continue

            mask_roi = mask[roi_y1:roi_y2, roi_x1:roi_x2]
            if not np.any(mask_roi):
                continue

            canvas_roi = canvas[roi_y1:roi_y2, roi_x1:roi_x2]
            color = np.asarray(color_tuple, dtype=np.float32)
            pixels = canvas_roi[mask_roi].astype(np.float32)
            canvas_roi[mask_roi] = np.clip(
                pixels * (1.0 - mask_alpha) + color * mask_alpha,
                0,
                255,
            ).astype(np.uint8)

            contours, _ = cv2.findContours(
                mask_roi.astype(np.uint8),
                cv2.RETR_EXTERNAL,
                cv2.CHAIN_APPROX_SIMPLE,
            )
            cv2.drawContours(
                canvas_roi,
                contours,
                -1,
                color_tuple,
                2,
                cv2.LINE_AA,
            )

    for box, confidence, class_id, raw_track_id, color in zip(
        boxes,
        confidences,
        class_ids,
        tracker_ids,
        colors,
    ):
        track_id = int(raw_track_id)
        x1, y1, x2, y2 = np.rint(box).astype(int)
        x1 = int(np.clip(x1, 0, width - 1))
        y1 = int(np.clip(y1, 0, height - 1))
        x2 = int(np.clip(x2, 0, width - 1))
        y2 = int(np.clip(y2, 0, height - 1))

        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)

        class_name = class_name_for(model, int(class_id))
        if track_id == UNTRACKED_DETECTION_ID:
            label = f"{class_name}  {float(confidence):.2f}"
        else:
            id_text = str(track_id) if track_id >= 0 else "pending"
            label = f"{class_name}  ID:{id_text}  {float(confidence):.2f}"

        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.55
        thickness = 2
        (text_width, text_height), baseline = cv2.getTextSize(
            label,
            font,
            font_scale,
            thickness,
        )
        text_y = max(text_height + baseline + 4, y1)
        cv2.rectangle(
            canvas,
            (x1, text_y - text_height - baseline - 6),
            (min(width - 1, x1 + text_width + 6), text_y + 2),
            color,
            -1,
        )
        cv2.putText(
            canvas,
            label,
            (x1 + 3, text_y - baseline - 1),
            font,
            font_scale,
            (0, 0, 0),
            thickness,
            cv2.LINE_AA,
        )

    return canvas


def normalize_track_ids(track_ids: Any, count: int) -> list[int]:
    """Match the Grounded-SAM ID normalization behavior exactly."""
    if track_ids is None:
        return list(range(count))

    normalized: list[int] = []
    for index in range(count):
        track_id = track_ids[index] if index < len(track_ids) else None
        if track_id is None:
            normalized.append(index)
            continue

        track_value = int(track_id)
        normalized.append(index if track_value < 0 else track_value)
    return normalized


def save_first_seen_id_samples(
    output_root: Path,
    frame_index: int,
    image_rgb: np.ndarray,
    masks: list[np.ndarray],
    track_ids: list[int],
    seen_track_ids: set[int],
) -> None:
    """Save the same first-seen crop and mask files as Grounded-SAM."""
    output_root.mkdir(parents=True, exist_ok=True)

    for mask, track_id in zip(masks, track_ids):
        if track_id is None:
            continue

        track_id_int = int(track_id)
        if track_id_int in seen_track_ids:
            continue

        seen_track_ids.add(track_id_int)
        mask_bool = np.asarray(mask, dtype=bool)

        ys, xs = np.where(mask_bool)
        if ys.size == 0 or xs.size == 0:
            continue

        y_min, y_max = int(ys.min()), int(ys.max())
        x_min, x_max = int(xs.min()), int(xs.max())

        scale = 1.2
        height = y_max - y_min + 1
        width = x_max - x_min + 1
        center_y = y_min + height / 2.0
        center_x = x_min + width / 2.0

        new_height = max(1, int(round(height * scale)))
        new_width = max(1, int(round(width * scale)))

        new_y_min = int(round(center_y - new_height / 2.0))
        new_y_max = new_y_min + new_height - 1
        new_x_min = int(round(center_x - new_width / 2.0))
        new_x_max = new_x_min + new_width - 1

        image_height, image_width = image_rgb.shape[:2]
        new_y_min = max(0, new_y_min)
        new_x_min = max(0, new_x_min)
        new_y_max = min(image_height - 1, new_y_max)
        new_x_max = min(image_width - 1, new_x_max)

        crop_rgb = image_rgb[
            new_y_min : new_y_max + 1,
            new_x_min : new_x_max + 1,
        ]
        crop_path = output_root / make_fish_crop_name(track_id_int, frame_index)
        cv2.imwrite(str(crop_path), cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2BGR))

        crop_mask = (
            mask_bool[
                new_y_min : new_y_max + 1,
                new_x_min : new_x_max + 1,
            ].astype(np.uint8)
            * 255
        )
        crop_mask_path = output_root / make_fish_mask_name(track_id_int, frame_index)
        cv2.imwrite(str(crop_mask_path), crop_mask)


def save_first_seen_id_samples_bgr(
    output_root: Path,
    frame_index: int,
    image_bgr: np.ndarray,
    masks: list[np.ndarray],
    track_ids: list[int],
) -> None:
    """Save IDs already claimed as new, using the restored full-size masks."""
    if not track_ids:
        return
    output_root.mkdir(parents=True, exist_ok=True)

    image_height, image_width = image_bgr.shape[:2]
    for raw_mask, track_id in zip(masks, track_ids):
        mask_bool = ensure_2d_mask(raw_mask, image_height, image_width)
        ys, xs = np.where(mask_bool)
        if ys.size == 0 or xs.size == 0:
            continue

        y_min, y_max = int(ys.min()), int(ys.max())
        x_min, x_max = int(xs.min()), int(xs.max())

        scale = 1.2
        height = y_max - y_min + 1
        width = x_max - x_min + 1
        center_y = y_min + height / 2.0
        center_x = x_min + width / 2.0

        new_height = max(1, int(round(height * scale)))
        new_width = max(1, int(round(width * scale)))

        new_y_min = int(round(center_y - new_height / 2.0))
        new_y_max = new_y_min + new_height - 1
        new_x_min = int(round(center_x - new_width / 2.0))
        new_x_max = new_x_min + new_width - 1

        new_y_min = max(0, new_y_min)
        new_x_min = max(0, new_x_min)
        new_y_max = min(image_height - 1, new_y_max)
        new_x_max = min(image_width - 1, new_x_max)

        crop_bgr = image_bgr[
            new_y_min : new_y_max + 1,
            new_x_min : new_x_max + 1,
        ]
        crop_path = output_root / make_fish_crop_name(int(track_id), frame_index)
        cv2.imwrite(str(crop_path), crop_bgr)

        crop_mask = (
            mask_bool[
                new_y_min : new_y_max + 1,
                new_x_min : new_x_max + 1,
            ].astype(np.uint8)
            * 255
        )
        crop_mask_path = output_root / make_fish_mask_name(int(track_id), frame_index)
        cv2.imwrite(str(crop_mask_path), crop_mask)



class BestFishSampleSelector:
    """Save the first crop/mask after a tracked fish passes the save position.

    Updates run in the main sequential tracking thread before asynchronous
    annotation, so the saved pixels are from the untouched source frame and
    the first eligible frame is deterministic.
    """

    def __init__(
        self,
        output_root: Path | None,
        *,
        crop_scale: float = BEST_SAMPLE_CROP_SCALE,
        save_progress_ratio: float,
        vertical_margin_ratio: float,
    ) -> None:
        self.output_root = output_root
        self.crop_scale = float(crop_scale)
        self.save_progress_ratio = float(save_progress_ratio)
        self.vertical_margin_ratio = float(vertical_margin_ratio)

        self.best_scores: dict[int, float] = {}
        self.best_frame_indexes: dict[int, int] = {}
        self.best_paths: dict[int, tuple[Path, Path]] = {}
        self.observation_counts: dict[int, int] = {}
        self.confirmed_track_ids: set[int] = set()
        self.saved_track_ids: set[int] = set()

        if self.output_root is not None:
            self.output_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _mask_bounds(mask_bool: np.ndarray) -> tuple[int, int, int, int] | None:
        ys, xs = np.where(mask_bool)
        if ys.size == 0 or xs.size == 0:
            return None
        return (
            int(xs.min()),
            int(ys.min()),
            int(xs.max()),
            int(ys.max()),
        )

    def _expanded_crop_bounds(
        self,
        mask_bool: np.ndarray,
        image_height: int,
        image_width: int,
    ) -> tuple[int, int, int, int] | None:
        bounds = self._mask_bounds(mask_bool)
        if bounds is None:
            return None

        x_min, y_min, x_max, y_max = bounds
        fish_height = y_max - y_min + 1
        fish_width = x_max - x_min + 1
        center_y = y_min + fish_height / 2.0
        center_x = x_min + fish_width / 2.0

        crop_height = max(1, int(round(fish_height * self.crop_scale)))
        crop_width = max(1, int(round(fish_width * self.crop_scale)))

        crop_y_min = int(round(center_y - crop_height / 2.0))
        crop_x_min = int(round(center_x - crop_width / 2.0))
        crop_y_max = crop_y_min + crop_height - 1
        crop_x_max = crop_x_min + crop_width - 1

        crop_y_min = max(0, crop_y_min)
        crop_x_min = max(0, crop_x_min)
        crop_y_max = min(image_height - 1, crop_y_max)
        crop_x_max = min(image_width - 1, crop_x_max)

        if crop_x_max < crop_x_min or crop_y_max < crop_y_min:
            return None
        return crop_x_min, crop_y_min, crop_x_max, crop_y_max

    def _save_candidate(
        self,
        *,
        track_id: int,
        frame_index: int,
        image_bgr: np.ndarray,
        mask_bool: np.ndarray,
    ) -> tuple[Path, Path] | None:
        if self.output_root is None:
            return None

        image_height, image_width = image_bgr.shape[:2]
        crop_bounds = self._expanded_crop_bounds(
            mask_bool,
            image_height,
            image_width,
        )
        if crop_bounds is None:
            return None
        x1, y1, x2, y2 = crop_bounds

        crop_bgr = image_bgr[y1 : y2 + 1, x1 : x2 + 1]
        crop_mask = (
            mask_bool[y1 : y2 + 1, x1 : x2 + 1].astype(np.uint8) * 255
        )

        crop_path = self.output_root / make_fish_crop_name(
            int(track_id),
            int(frame_index),
        )
        mask_path = self.output_root / make_fish_mask_name(
            int(track_id),
            int(frame_index),
        )

        crop_ok = cv2.imwrite(
            str(crop_path),
            crop_bgr,
            [cv2.IMWRITE_JPEG_QUALITY, 100],
        )
        mask_ok = cv2.imwrite(str(mask_path), crop_mask)
        if not crop_ok or not mask_ok:
            try:
                crop_path.unlink(missing_ok=True)
                mask_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError(
                f"Could not save fish sample for track {track_id}"
            )

        return crop_path, mask_path

    def update(
        self,
        *,
        frame_index: int,
        image_bgr: np.ndarray,
        detections: sv.Detections,
    ) -> None:
        if detections.tracker_id is None or detections.mask is None:
            return

        tracker_ids = np.asarray(detections.tracker_id, dtype=np.int64)
        image_height, image_width = image_bgr.shape[:2]

        for index, raw_track_id in enumerate(tracker_ids):
            track_id = int(raw_track_id)
            if track_id < 0:
                continue

            self.confirmed_track_ids.add(track_id)
            self.observation_counts[track_id] = (
                self.observation_counts.get(track_id, 0) + 1
            )

            if track_id in self.saved_track_ids:
                continue

            mask_bool = ensure_2d_mask(
                detections.mask[index],
                image_height,
                image_width,
            )
            progress = conveyor_body_progress(
                mask_bool,
                image_height,
                image_width,
                vertical_margin_ratio=self.vertical_margin_ratio,
            )
            if progress is None or progress <= self.save_progress_ratio:
                continue

            saved_paths = self._save_candidate(
                track_id=track_id,
                frame_index=int(frame_index),
                image_bgr=image_bgr,
                mask_bool=mask_bool,
            )
            if saved_paths is None:
                continue

            self.best_scores[track_id] = float(progress)
            self.best_frame_indexes[track_id] = int(frame_index)
            self.best_paths[track_id] = saved_paths
            self.saved_track_ids.add(track_id)

            print(
                f"[SAMPLE] ID {track_id}: saved first frame past "
                f"{self.save_progress_ratio:.1%} of conveyor, "
                f"frame={frame_index}, progress={progress:.4f}"
            )


def save_grounded_sam_style_result(
    output_dir: Path,
    image_name: str,
    image_rgb: np.ndarray,
    detections: sv.Detections,
    mask_alpha: float,
    first_match_output_dir: Path,
    seen_track_ids: set[int],
) -> bool:
    """
    Save the same visual content and naming used by the supplied Grounded-SAM code.

    Returns True when a rendered image is written. As in that code, a frame with
    no detections is not written as ``*_grounded_sam.jpg``.
    """
    count = len(detections)
    if count == 0:
        return False

    image_height, image_width = image_rgb.shape[:2]
    boxes = np.asarray(detections.xyxy, dtype=np.float32)
    confidences = (
        np.asarray(detections.confidence, dtype=np.float32)
        if detections.confidence is not None
        else np.ones(count, dtype=np.float32)
    )
    class_ids = (
        np.asarray(detections.class_id, dtype=np.int64)
        if detections.class_id is not None
        else np.zeros(count, dtype=np.int64)
    )
    raw_track_ids = detections.tracker_id
    track_ids = normalize_track_ids(raw_track_ids, count)

    if detections.mask is None:
        masks = [np.zeros((image_height, image_width), dtype=bool) for _ in range(count)]
    else:
        masks = [
            ensure_2d_mask(mask, image_height, image_width)
            for mask in detections.mask
        ]

    save_first_seen_id_samples(
        output_root=first_match_output_dir,
        frame_index=(
            extract_frame_index_from_path(image_name)
            if extract_frame_index_from_path(image_name) is not None
            else 0
        ),
        image_rgb=image_rgb,
        masks=masks,
        track_ids=track_ids,
        seen_track_ids=seen_track_ids,
    )

    labels: list[str] = []
    for class_id, confidence in zip(class_ids, confidences):
        class_name = "fish" if int(class_id) == 0 else f"class_{int(class_id)}"
        labels.append(f"{class_name}({float(confidence):.3f})")

    global CURRENT_IMG_WIDTH, CURRENT_IMG_DPI
    CURRENT_IMG_WIDTH = image_width

    figure = plt.figure(figsize=(10, 10))
    CURRENT_IMG_DPI = figure.dpi
    axis = plt.gca()
    plt.imshow(image_rgb)

    for mask, track_id in zip(masks, track_ids):
        show_mask(
            mask,
            axis,
            color=get_track_color(track_id, alpha=mask_alpha),
        )

    for box, label, track_id in zip(boxes, labels, track_ids):
        show_box(
            box,
            axis,
            f"{label} | ID {track_id}",
            color=get_track_color(track_id, alpha=mask_alpha),
        )

    plt.axis("off")
    output_path = output_dir / f"{image_name}_grounded_sam.jpg"
    plt.savefig(
        output_path,
        bbox_inches="tight",
        dpi=300,
        pad_inches=0.0,
    )
    plt.close("all")
    return True



class FFmpegVideoWriter:
    """
    Stream OpenCV BGR frames to FFmpeg through stdin.

    The public ``write()``, ``release()``, and ``isOpened()`` methods intentionally
    mirror the subset of ``cv2.VideoWriter`` used by the existing pipeline, so the
    asynchronous visualization code does not need to change.
    """

    def __init__(
        self,
        output_path: Path,
        width: int,
        height: int,
        fps: float,
        ffmpeg_bin: str = "ffmpeg",
        requested_encoder: str = "auto",
    ) -> None:
        self.output_path = Path(output_path)
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps)
        self.ffmpeg_bin = self._resolve_ffmpeg_binary(ffmpeg_bin)
        self.encoder_name = self._resolve_encoder(requested_encoder)
        self.closed = False
        self.frame_count = 0
        self.process: subprocess.Popen[bytes] | None = None

        self.stderr_log_path = self.output_path.with_name(
            f"{self.output_path.stem}_ffmpeg.log"
        )
        self._stderr_log_file = self.stderr_log_path.open("wb")

        command = self._build_command()
        print(f"[INFO] FFmpeg executable:         {self.ffmpeg_bin}")
        print(f"[INFO] FFmpeg video encoder:      {self.encoder_name}")

        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=self._stderr_log_file,
                bufsize=0,
            )
        except BaseException:
            self._stderr_log_file.close()
            raise

        if self.process.stdin is None:
            self.process.terminate()
            self.process.wait()
            self._stderr_log_file.close()
            raise RuntimeError("Could not open the FFmpeg stdin pipe.")

    @staticmethod
    def _resolve_ffmpeg_binary(ffmpeg_bin: str) -> str:
        candidate = str(ffmpeg_bin).strip()
        if not candidate:
            raise ValueError("--ffmpeg-bin cannot be empty")

        if Path(candidate).is_file():
            return str(Path(candidate).resolve())

        resolved = shutil.which(candidate)
        if resolved is None:
            raise FileNotFoundError(
                "FFmpeg was not found. Install it in the current environment or "
                "pass its absolute path with --ffmpeg-bin. For example:\n"
                "    conda install -c conda-forge ffmpeg -y"
            )
        return resolved

    def _encoder_available(self, encoder_name: str) -> bool:
        result = subprocess.run(
            [self.ffmpeg_bin, "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Could not query FFmpeg encoders. FFmpeg output:\n"
                f"{result.stdout.strip()}"
            )

        # Encoder lines have the form `` V..... h264_nvenc ...``. Splitting the
        # output avoids substring matches against descriptions.
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[1] == encoder_name:
                return True
        return False

    def _test_encoder(self, encoder_name: str) -> tuple[bool, str]:
        """Test whether FFmpeg can actually initialize the requested encoder."""
        command = [
            self.ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=16x16:r=1",
            "-frames:v",
            "1",
        ]

        if encoder_name == "h264_nvenc":
            command.extend(
                [
                    "-c:v",
                    "h264_nvenc",
                    "-preset",
                    "p4",
                    "-rc:v",
                    "vbr",
                    "-cq:v",
                    "23",
                    "-b:v",
                    "0",
                ]
            )
        elif encoder_name == "libx264":
            command.extend(
                [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "23",
                ]
            )
        else:
            return False, f"Unsupported encoder test: {encoder_name}"

        command.extend(["-f", "null", "-"])
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        return result.returncode == 0, result.stderr.strip()

    def _resolve_encoder(self, requested_encoder: str) -> str:
        if requested_encoder == "auto":
            if self._encoder_available("h264_nvenc"):
                usable, error_text = self._test_encoder("h264_nvenc")
                if usable:
                    return "h264_nvenc"
                print(
                    "[WARN] h264_nvenc is listed by FFmpeg but could not be "
                    "initialized; falling back to CPU libx264. "
                    f"FFmpeg reported: {error_text or 'unknown error'}",
                    file=sys.stderr,
                )

            if self._encoder_available("libx264"):
                usable, error_text = self._test_encoder("libx264")
                if usable:
                    return "libx264"
                raise RuntimeError(
                    "FFmpeg lists libx264 but cannot initialize it. "
                    f"FFmpeg reported: {error_text or 'unknown error'}"
                )

            raise RuntimeError(
                "This FFmpeg build provides neither a usable h264_nvenc nor libx264 encoder."
            )

        if not self._encoder_available(requested_encoder):
            raise RuntimeError(
                f"The requested FFmpeg encoder is unavailable: {requested_encoder}"
            )

        usable, error_text = self._test_encoder(requested_encoder)
        if not usable:
            raise RuntimeError(
                f"The requested FFmpeg encoder cannot be initialized: {requested_encoder}. "
                f"FFmpeg reported: {error_text or 'unknown error'}"
            )
        return requested_encoder

    def _build_command(self) -> list[str]:
        command = [
            self.ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-video_size",
            f"{self.width}x{self.height}",
            "-framerate",
            f"{self.fps:.12g}",
            "-i",
            "pipe:0",
            "-an",
        ]

        if self.encoder_name == "h264_nvenc":
            command.extend(
                [
                    "-c:v",
                    "h264_nvenc",
                    "-preset",
                    "p4",
                    "-rc:v",
                    "vbr",
                    "-cq:v",
                    "23",
                    "-b:v",
                    "0",
                ]
            )
        elif self.encoder_name == "libx264":
            command.extend(
                [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "23",
                ]
            )
        else:
            raise RuntimeError(f"Unsupported resolved encoder: {self.encoder_name}")

        # yuv420p has broad player compatibility. Padding only changes odd source
        # dimensions by one pixel and is a no-op for normal even-sized frames.
        command.extend(
            [
                "-vf",
                "pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p",
                "-movflags",
                "+faststart",
                str(self.output_path),
            ]
        )
        return command

    def isOpened(self) -> bool:
        return (
            not self.closed
            and self.process is not None
            and self.process.poll() is None
            and self.process.stdin is not None
        )

    def _read_error_log(self) -> str:
        try:
            if not self._stderr_log_file.closed:
                self._stderr_log_file.flush()
            return self.stderr_log_path.read_text(
                encoding="utf-8",
                errors="replace",
            ).strip()
        except OSError:
            return ""

    def write(self, frame_bgr: np.ndarray) -> None:
        if not self.isOpened():
            error_text = self._read_error_log()
            suffix = f"\nFFmpeg output:\n{error_text}" if error_text else ""
            raise RuntimeError(f"FFmpeg video writer is not open.{suffix}")

        frame = np.asarray(frame_bgr)
        expected_shape = (self.height, self.width, 3)
        if frame.shape != expected_shape:
            raise ValueError(
                f"Expected video frame shape {expected_shape}, got {frame.shape}."
            )

        if frame.dtype != np.uint8:
            frame = np.clip(frame, 0, 255).astype(np.uint8)
        frame = np.ascontiguousarray(frame)

        assert self.process is not None
        assert self.process.stdin is not None
        try:
            self.process.stdin.write(frame.tobytes())
            self.frame_count += 1
        except (BrokenPipeError, OSError) as exc:
            error_text = self._read_error_log()
            suffix = f"\nFFmpeg output:\n{error_text}" if error_text else ""
            raise RuntimeError(
                f"FFmpeg stopped while writing frame {self.frame_count}.{suffix}"
            ) from exc

    def release(self) -> None:
        if self.closed:
            return
        self.closed = True

        process = self.process
        if process is None:
            if not self._stderr_log_file.closed:
                self._stderr_log_file.close()
            return

        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass

        return_code = process.wait()
        if not self._stderr_log_file.closed:
            self._stderr_log_file.flush()
            self._stderr_log_file.close()

        error_text = self._read_error_log()
        if return_code != 0:
            suffix = f"\nFFmpeg output:\n{error_text}" if error_text else ""
            raise RuntimeError(
                f"FFmpeg encoding failed with exit code {return_code}.{suffix}"
            )

        # Keep a non-empty log for diagnostics, but remove the normal empty file.
        try:
            if self.stderr_log_path.exists() and self.stderr_log_path.stat().st_size == 0:
                self.stderr_log_path.unlink()
        except OSError:
            pass


_VISUALIZATION_STOP = object()
_RENDER_RESULT_STOP = object()


class AsyncVisualizationWriter:
    """
    Render with two CPU workers and write frames through one ordered writer.

    ByteTrack and first-seen-ID claiming stay sequential in the main submission
    thread. Render workers mutate their owned BGR frames in place. Completed
    frames may arrive out of order, so the writer thread buffers them by
    ``frame_index`` and saves them strictly in source order.
    """

    def __init__(
        self,
        writer: FFmpegVideoWriter | None,
        model: Any,
        args: argparse.Namespace,
        timing_history: dict[str, list[float]],
        total_images: int,
        queue_size: int = VISUALIZATION_QUEUE_SIZE,
        worker_count: int = VISUALIZATION_WORKERS,
    ) -> None:
        self.writer = writer
        self.model = model
        self.args = args
        self.timing_history = timing_history
        self.total_images = int(total_images)
        self.worker_count = max(1, int(worker_count))
        self.render_queue: Queue[Any] = Queue(maxsize=max(1, int(queue_size)))
        self.result_queue: Queue[Any] = Queue(
            maxsize=max(1, int(RENDER_RESULT_QUEUE_SIZE))
        )
        self.render_threads = [
            threading.Thread(
                target=self._render_loop,
                name=f"rfdetr-render-{worker_index}",
                daemon=True,
            )
            for worker_index in range(self.worker_count)
        ]
        self.writer_thread = threading.Thread(
            target=self._writer_loop,
            name="rfdetr-ordered-ffmpeg-writer",
            daemon=True,
        )
        self.exception: BaseException | None = None
        self._exception_lock = threading.Lock()
        self.stop_requested = threading.Event()
        self.seen_track_ids: set[int] = set()
        self.frames_written = 0
        self.submitted_frames = 0

        for thread in self.render_threads:
            thread.start()
        self.writer_thread.start()

    def _set_exception(self, exc: BaseException) -> None:
        with self._exception_lock:
            if self.exception is None:
                self.exception = exc

    def _raise_if_failed(self) -> None:
        if self.exception is not None:
            raise RuntimeError(
                "The asynchronous visualization pipeline failed."
            ) from self.exception

    def _register_confirmed_track_ids(self, tracked: sv.Detections) -> None:
        """Keep the historical track-count statistic without saving first frame."""
        raw_tracker_ids = tracked.tracker_id
        if raw_tracker_ids is None:
            return
        for raw_track_id in np.asarray(raw_tracker_ids, dtype=np.int64):
            track_id = int(raw_track_id)
            if track_id >= 0:
                self.seen_track_ids.add(track_id)

    def submit(self, item: dict[str, Any]) -> None:
        self._raise_if_failed()
        self._register_confirmed_track_ids(item["tracked"])
        self.render_queue.put(item)
        self.submitted_frames += 1
        self._raise_if_failed()

    def close(self) -> None:
        for _ in range(self.worker_count):
            self.render_queue.put(_VISUALIZATION_STOP)
        self.render_queue.join()
        for thread in self.render_threads:
            thread.join()

        self.result_queue.put(_RENDER_RESULT_STOP)
        self.result_queue.join()
        self.writer_thread.join()
        self._raise_if_failed()

    def _render_loop(self) -> None:
        while True:
            item = self.render_queue.get()
            try:
                if item is _VISUALIZATION_STOP:
                    return
                if self.exception is None:
                    result = self._render_item(item)
                    self.result_queue.put(result)
            except BaseException as exc:
                self._set_exception(exc)
            finally:
                self.render_queue.task_done()

    def _render_item(self, item: dict[str, Any]) -> dict[str, Any]:
        frame_bgr: np.ndarray = item["frame_bgr"]
        tracked: sv.Detections = item["tracked"]

        # Position-based sample selection runs synchronously in the main
        # tracking thread before this frame is submitted. This render worker
        # must not write another first-seen crop.
        first_seen_start = time.perf_counter()
        first_seen_end = first_seen_start

        visualization_start = first_seen_end
        annotated = annotate_frame(
            frame_bgr=frame_bgr,
            detections=tracked,
            model=self.model,
            mask_alpha=self.args.mask_alpha,
        )
        visualization_end = time.perf_counter()

        raw_tracker_ids = tracked.tracker_id
        confirmed_tracks = (
            0
            if raw_tracker_ids is None
            else int(np.sum(np.asarray(raw_tracker_ids, dtype=np.int64) >= 0))
        )
        # Do not forward ``tracked`` to the ordered writer queue. With restored
        # full-resolution masks, retaining the Detections object after rendering
        # would keep large mask arrays alive until FFmpeg writes the frame.
        return {
            "frame_index": int(item["frame_index"]),
            "image_path": item["image_path"],
            "annotated": annotated,
            "image_read_ms": float(item["image_read_ms"]),
            "preprocess_ms": float(item["preprocess_ms"]),
            "inference_ms": float(item["inference_ms"]),
            "tracking_ms": float(item["tracking_ms"]),
            "visualization_ms": (visualization_end - visualization_start) * 1000.0,
            "first_seen_write_ms": (first_seen_end - first_seen_start) * 1000.0,
            "detections_count": len(tracked),
            "confirmed_tracks": confirmed_tracks,
        }

    def _writer_loop(self) -> None:
        pending: dict[int, dict[str, Any]] = {}
        next_frame_index = 0
        while True:
            result = self.result_queue.get()
            try:
                if result is _RENDER_RESULT_STOP:
                    if self.exception is None:
                        while next_frame_index in pending:
                            current = pending.pop(next_frame_index)
                            self._write_result(current)
                            next_frame_index += 1
                        if pending or next_frame_index != self.submitted_frames:
                            raise RuntimeError(
                                "Ordered writer closed with missing rendered frames: "
                                f"next={next_frame_index}, submitted={self.submitted_frames}, "
                                f"pending={sorted(pending)}"
                            )
                    return

                if self.exception is None:
                    frame_index = int(result["frame_index"])
                    pending[frame_index] = result
                    while next_frame_index in pending:
                        current = pending.pop(next_frame_index)
                        self._write_result(current)
                        next_frame_index += 1
            except BaseException as exc:
                self._set_exception(exc)
            finally:
                self.result_queue.task_done()

    def _write_result(self, item: dict[str, Any]) -> None:
        """Write one annotated frame and, when requested, the ordered video frame."""

        image_write_start = time.perf_counter()
        annotated_output_dir = self.args.annotated_output_dir
        frame_number = extract_frame_index_from_path(item["image_path"])
        if frame_number is None:
            raise RuntimeError(
                f"Could not parse frame index from: {item['image_path']}"
            )
        annotated_path = (
            annotated_output_dir / make_annotated_frame_name(frame_number)
        )
        if not cv2.imwrite(str(annotated_path), item["annotated"]):
            raise RuntimeError(
                f"Could not write annotated frame: {annotated_path}"
            )
        image_write_end = time.perf_counter()

        video_write_start = image_write_end
        if self.writer is not None:
            self.writer.write(item["annotated"])
        video_write_end = time.perf_counter()

        preview_start = video_write_end
        if self.args.preview:
            cv2.imshow(
                "RF-DETR Seg + ByteTrack image-sequence tracking",
                item["annotated"],
            )
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                self.stop_requested.set()
        preview_end = time.perf_counter()

        first_seen_write_ms = float(item["first_seen_write_ms"])
        annotated_write_ms = (
            image_write_end - image_write_start
        ) * 1000.0
        video_write_ms = (
            video_write_end - video_write_start
        ) * 1000.0

        timings = {
            "image_read_ms": float(item["image_read_ms"]),
            "preprocess_ms": float(item["preprocess_ms"]),
            "inference_ms": float(item["inference_ms"]),
            "tracking_ms": float(item["tracking_ms"]),
            "visualization_ms": float(item["visualization_ms"]),
            "first_seen_write_ms": first_seen_write_ms,
            "video_write_ms": video_write_ms,
            "image_write_ms": (
                first_seen_write_ms + annotated_write_ms + video_write_ms
            ),
            "preview_ms": (preview_end - preview_start) * 1000.0,
        }
        timings["total_ms"] = sum(timings.values())

        for key, value in timings.items():
            self.timing_history[key].append(value)

        self.frames_written += 1
        if self.frames_written % 50 == 0:
            recent_total_ms = self.timing_history["total_ms"][-50:]
            recent_stage_sum_fps = 1000.0 / max(
                float(np.mean(recent_total_ms)),
                1e-9,
            )
            print(
                f"[INFO] Written {self.frames_written}/{self.total_images} images | "
                f"recent stage-sum speed: {recent_stage_sum_fps:.2f} images/s | "
                f"latest amortized inference: {timings['inference_ms']:.2f} ms | "
                f"latest ROI visualization: {timings['visualization_ms']:.2f} ms"
            )



def summarize_values(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {
            "count": 0,
            "mean": 0.0,
            "median": 0.0,
            "p95": 0.0,
            "min": 0.0,
            "max": 0.0,
        }

    array = np.asarray(values, dtype=np.float64)
    return {
        "count": int(array.size),
        "mean": float(array.mean()),
        "median": float(np.median(array)),
        "p95": float(np.percentile(array, 95)),
        "min": float(array.min()),
        "max": float(array.max()),
    }


def load_image_batch(
    batch_paths: list[Path],
    expected_width: int,
    expected_height: int,
) -> list[dict[str, Any]]:
    """Read and RGB-convert one batch in the dedicated prefetch thread."""
    records: list[dict[str, Any]] = []
    for image_path in batch_paths:
        read_start = time.perf_counter()
        frame_bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        read_end = time.perf_counter()
        if frame_bgr is None:
            print(f"[WARN] Skipping unreadable image: {image_path}", file=sys.stderr)
            continue

        image_height, image_width = frame_bgr.shape[:2]
        if (image_width, image_height) != (expected_width, expected_height):
            raise RuntimeError(
                "All sequence images must have the same dimensions for video output. "
                f"First image: {expected_width}x{expected_height}; "
                f"current image {image_path.name}: {image_width}x{image_height}."
            )

        preprocess_start = read_end
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        preprocess_end = time.perf_counter()
        records.append(
            {
                "image_path": image_path,
                "frame_bgr": frame_bgr,
                "frame_rgb": frame_rgb,
                "image_height": image_height,
                "image_width": image_width,
                "image_read_ms": (read_end - read_start) * 1000.0,
                "preprocess_ms": (preprocess_end - preprocess_start) * 1000.0,
            }
        )
    return records


def main(argv: Iterable[str] | None = None) -> int:
    argv_list = list(argv) if argv is not None else None
    args = parse_args(argv_list)

    # Resolve outputs exactly like grounded_sam_mmdet_batch.py:
    #   annotated_frames/<12-digit>_grounded_sam.jpg
    #   first_match/id_<track>_<12-digit>_{crop|mask_crop_bw}.*
    #   videos/grounded_sam.mp4
    if args.video_id:
        if args.input_dir is None and args.input_image is None:
            args.input_dir = video_frames_adjusted_dir(args.video_id)
        args.annotated_output_dir = video_annotated_frames_dir(args.video_id)
        args.output_dir = args.annotated_output_dir
        args.first_match_output_dir = video_first_match_dir(args.video_id)
        args.video_output = video_grounded_sam_video(args.video_id)
    else:
        if args.input_dir is None and args.input_image is None:
            raise ValueError(
                "未指定 --input-dir / --input-image / --video-id；"
                "请显式传入输入目录或 --video-id。"
            )
        if args.output_dir is None and args.annotated_output_dir is None:
            raise ValueError(
                "未指定 --output-dir / --video-id；"
                "请显式传入标注帧输出目录或 --video-id。"
            )

        if args.input_dir is not None:
            args.input_dir = resolve_input_path(args.input_dir)
        if args.input_image is not None:
            args.input_image = resolve_input_path(args.input_image)
            args.input_dir = args.input_image.parent

        # Grounded-SAM uses --output-dir for annotated frames.
        if args.annotated_output_dir is None:
            args.annotated_output_dir = resolve_input_path(args.output_dir)
        else:
            args.annotated_output_dir = resolve_input_path(
                args.annotated_output_dir
            )
        args.output_dir = args.annotated_output_dir

        if args.first_match_output_dir is not None:
            args.first_match_output_dir = resolve_input_path(
                args.first_match_output_dir
            )
        if args.video_output is not None:
            args.video_output = resolve_input_path(args.video_output)

    args.checkpoint = resolve_input_path(args.checkpoint)
    position_config = load_conveyor_position_config()

    if args.input_image is not None and not args.input_image.is_file():
        raise FileNotFoundError(f"Input image not found: {args.input_image}")

    validate_args(args)
    cv2.setNumThreads(OPENCV_NUM_THREADS)

    image_paths = collect_image_paths(args.input_dir)
    if args.input_image is not None:
        image_paths = [args.input_image]

    # Grounded-SAM only accepts filenames from which a numeric frame index can
    # be parsed; this guarantees the same 12-digit output naming.
    filtered_image_paths: list[Path] = []
    for path in image_paths:
        frame_number = extract_frame_index_from_path(path)
        if frame_number is None:
            continue
        if args.frame_start <= frame_number <= args.frame_end:
            filtered_image_paths.append(path)
    image_paths = filtered_image_paths

    if not image_paths:
        raise RuntimeError(
            f"No supported, numerically named images were found in: "
            f"{args.input_dir}."
        )

    if args.max_images is not None:
        image_paths = image_paths[: args.max_images]

    total_images = len(image_paths)
    first_image = cv2.imread(str(image_paths[0]), cv2.IMREAD_COLOR)
    if first_image is None:
        raise RuntimeError(
            f"OpenCV could not read the first image: {image_paths[0]}"
        )
    first_height, first_width = first_image.shape[:2]

    # Do not append _runNNN: Grounded-SAM writes to these exact directories.
    ensure_dir(args.annotated_output_dir)
    if args.first_match_output_dir is not None:
        ensure_dir(args.first_match_output_dir)

    output_video: Path | None = args.video_output
    if output_video is not None:
        ensure_dir(output_video.parent)

    writer: FFmpegVideoWriter | None = None
    if output_video is not None:
        writer = FFmpegVideoWriter(
            output_path=output_video,
            width=first_width,
            height=first_height,
            fps=args.frame_rate,
            ffmpeg_bin=args.ffmpeg_bin,
            requested_encoder=args.video_encoder,
        )
        if not writer.isOpened():
            raise RuntimeError(
                f"FFmpeg could not create output video: {output_video}"
            )
        args.resolved_video_encoder = writer.encoder_name

    model_load_start = time.perf_counter()
    model = load_rfdetr_model(args.checkpoint, args.fp16)
    synchronize_cuda()
    model_load_seconds = time.perf_counter() - model_load_start

    tracker = build_tracker(args, args.frame_rate)

    # Timing remains in memory for console diagnostics only. Grounded-SAM does
    # not create processing_times.csv, timing_summary.json, or run_config.json.
    timing_history: dict[str, list[float]] = {
        "image_read_ms": [],
        "preprocess_ms": [],
        "inference_ms": [],
        "tracking_ms": [],
        "visualization_ms": [],
        "first_seen_write_ms": [],
        "video_write_ms": [],
        "image_write_ms": [],
        "preview_ms": [],
        "total_ms": [],
    }

    best_sample_selector = BestFishSampleSelector(
        output_root=args.first_match_output_dir,
        crop_scale=BEST_SAMPLE_CROP_SCALE,
        save_progress_ratio=position_config["crop_save_ratio"],
        vertical_margin_ratio=position_config["vertical_margin_ratio"],
    )

    visualization_pipeline = AsyncVisualizationWriter(
        writer=writer,
        model=model,
        args=args,
        timing_history=timing_history,
        total_images=total_images,
    )

    print(f"[INFO] Input directory:          {args.input_dir}")
    print(f"[INFO] TensorRT engine:          {args.checkpoint}")
    print(f"[INFO] Segmentation threshold:   > {args.model_threshold:.3f}")
    print(
        "[INFO] Effective edge ratios:    "
        f"top={args.top_edge_ratio:.3f}, "
        f"bottom={args.bottom_edge_ratio:.3f}, "
        f"left=+{args.left_edge_ratio:.3f}, "
        f"right=+{args.right_edge_ratio:.3f}"
    )
    print(
        "[INFO] Tracker input threshold: "
        f"> {TRACKING_INPUT_CONFIDENCE_THRESHOLD:.3f}"
    )
    print(f"[INFO] Annotated frames:         {args.annotated_output_dir}")
    print(f"[INFO] Fish sample output:        {args.first_match_output_dir}")
    print(
        "[INFO] Conveyor position config:  "
        f"{ADJUST_CALIBRATION_MARGIN_CONFIG_PATH}"
    )
    print(
        "[INFO] Tracking/segmentation gate: fish midpoint > "
        f"{position_config['tracking_start_ratio']:.1%} of conveyor"
    )
    print(
        "[INFO] Fish sample selection:     first fish midpoint > "
        f"{position_config['crop_save_ratio']:.1%} of conveyor "
        f"({POSITION_GATE_VERSION})"
    )
    print(f"[INFO] Output video:             {output_video}")
    if writer is not None:
        print(
            f"[INFO] Video writer backend:     FFmpeg "
            f"({writer.encoder_name})"
        )
    print("[INFO] Tracker:                  ByteTrackTracker")
    print(f"[INFO] TensorRT batch size:      {BATCH_SIZE}")
    print(
        "[INFO] GPU post-processing:      "
        "sigmoid/top-K/filter/boxes/full-size masks"
    )
    print(
        f"[INFO] GPU mask resize:          CUDA bilinear full-size, "
        f"chunk={GPU_MASK_RESIZE_CHUNK_SIZE}"
    )
    print(
        f"[INFO] Parallel visualization:   {VISUALIZATION_WORKERS} workers, "
        f"queue={VISUALIZATION_QUEUE_SIZE}"
    )
    print("[INFO] Image read prefetch:      enabled, one batch ahead")
    print(f"[INFO] Images found:             {total_images}")
    print(f"[INFO] Output video frame rate:  {args.frame_rate:.3f}")
    print(f"[INFO] Model load:               {model_load_seconds:.3f} s")

    submitted_frames = 0
    processing_start = time.perf_counter()
    main_error: BaseException | None = None

    try:
        stop_all = False
        batch_path_groups = [
            image_paths[start : start + BATCH_SIZE]
            for start in range(0, total_images, BATCH_SIZE)
        ]

        with ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="rfdetr-image-prefetch",
        ) as prefetch_executor:
            current_future: Future[list[dict[str, Any]]] | None = None
            if batch_path_groups:
                current_future = prefetch_executor.submit(
                    load_image_batch,
                    batch_path_groups[0],
                    first_width,
                    first_height,
                )

            for batch_index, _batch_paths in enumerate(batch_path_groups):
                if visualization_pipeline.stop_requested.is_set():
                    stop_all = True
                    break
                if current_future is None:
                    break

                batch_records = current_future.result()

                if batch_index + 1 < len(batch_path_groups):
                    next_future: Future[list[dict[str, Any]]] | None = (
                        prefetch_executor.submit(
                            load_image_batch,
                            batch_path_groups[batch_index + 1],
                            first_width,
                            first_height,
                        )
                    )
                else:
                    next_future = None

                if not batch_records:
                    current_future = next_future
                    continue

                inference_start = time.perf_counter()
                detections_batch = model.predict_batch(
                    [record["frame_rgb"] for record in batch_records],
                    threshold=args.model_threshold,
                )
                inference_end = time.perf_counter()
                batch_inference_ms = (
                    inference_end - inference_start
                ) * 1000.0
                inference_ms_per_image = (
                    batch_inference_ms / len(batch_records)
                )

                # ByteTrack remains strictly sequential.
                for record, detections in zip(
                    batch_records,
                    detections_batch,
                ):
                    if visualization_pipeline.stop_requested.is_set():
                        stop_all = True
                        break

                    detections = filter_detections_by_effective_region(
                        detections,
                        image_height=record["image_height"],
                        image_width=record["image_width"],
                        top_ratio=args.top_edge_ratio,
                        bottom_ratio=args.bottom_edge_ratio,
                        left_ratio=args.left_edge_ratio,
                        right_ratio=args.right_edge_ratio,
                    )
                    detections = filter_detections_by_conveyor_progress(
                        detections,
                        image_height=record["image_height"],
                        image_width=record["image_width"],
                        minimum_progress_ratio=(
                            position_config["tracking_start_ratio"]
                        ),
                        vertical_margin_ratio=(
                            position_config["vertical_margin_ratio"]
                        ),
                    )
                    tracking_candidates, untracked_detections = (
                        split_detections_for_tracking(detections)
                    )
                    tracking_start = time.perf_counter()
                    tracked = tracker.update(tracking_candidates)
                    tracking_end = time.perf_counter()
                    rendered_detections = merge_tracked_and_untracked_detections(
                        tracked,
                        untracked_detections,
                    )

                    # Save the first source crop past the configured conveyor
                    # position before a render worker annotates frame_bgr.
                    source_frame_number = extract_frame_index_from_path(
                        record["image_path"]
                    )
                    if source_frame_number is None:
                        source_frame_number = submitted_frames

                    best_sample_selector.update(
                        frame_index=int(source_frame_number),
                        image_bgr=record["frame_bgr"],
                        detections=rendered_detections,
                    )

                    visualization_pipeline.submit(
                        {
                            "frame_index": submitted_frames,
                            "image_path": record["image_path"],
                            "frame_bgr": record["frame_bgr"],
                            "image_height": record["image_height"],
                            "image_width": record["image_width"],
                            "image_read_ms": record["image_read_ms"],
                            "preprocess_ms": record["preprocess_ms"],
                            "inference_ms": inference_ms_per_image,
                            "tracking_ms": (
                                tracking_end - tracking_start
                            ) * 1000.0,
                            "tracked": rendered_detections,
                        }
                    )
                    submitted_frames += 1

                for record in batch_records:
                    record.pop("frame_rgb", None)
                current_future = next_future

                if stop_all:
                    print(
                        "[INFO] Preview stop requested; "
                        "no new frames will be queued."
                    )
                    break

    except BaseException as exc:
        main_error = exc
    finally:
        close_error: BaseException | None = None
        try:
            visualization_pipeline.close()
        except BaseException as exc:
            close_error = exc
        finally:
            if writer is not None:
                writer.release()
            if args.preview:
                cv2.destroyAllWindows()

        if main_error is not None:
            raise main_error
        if close_error is not None:
            raise close_error

    processing_wall_seconds = max(
        time.perf_counter() - processing_start,
        1e-9,
    )
    frame_count = visualization_pipeline.frames_written
    seen_track_ids = visualization_pipeline.seen_track_ids
    best_saved_track_ids = best_sample_selector.saved_track_ids
    warmup = min(args.timing_warmup_frames, frame_count)

    metrics_steady_state = {
        name: summarize_values(values[warmup:])
        for name, values in timing_history.items()
    }
    steady_total_mean = float(metrics_steady_state["total_ms"]["mean"])
    steady_fps = (
        1000.0 / steady_total_mean
        if steady_total_mean > 0
        else 0.0
    )

    print("==== RF-DETR Grounded-SAM-compatible pipeline 完成 ====")
    print(f"video_id         : {args.video_id or '-'}")
    print(f"annotated_dir    : {args.annotated_output_dir}")
    if args.first_match_output_dir is not None:
        print(f"first_match_dir  : {args.first_match_output_dir}")
    if output_video is not None:
        print(f"video_output     : {output_video}")
    print(f"frame_count      : {frame_count}")
    print(f"confirmed_ids    : {len(seen_track_ids)}")
    print(f"saved_samples    : {len(best_saved_track_ids)}")
    print(
        f"sample_position  : > "
        f"{position_config['crop_save_ratio']:.1%} of conveyor"
    )
    print(f"wall_time        : {processing_wall_seconds:.3f} s")
    print(
        f"wall_clock_speed : "
        f"{frame_count / processing_wall_seconds:.2f} images/s"
    )
    print(f"stage_sum        : {steady_total_mean:.3f} ms/image")
    print(f"stage_sum_speed  : {steady_fps:.2f} images/s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())