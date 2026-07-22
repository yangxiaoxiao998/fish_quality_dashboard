"""Offline ViT inference for fish-completeness classification.

The completeness classifier is deliberately independent from ``merge_funs`` so
that the old YOLO implementation can be removed without affecting the image
rotation and belly-orientation helpers that still live there.

All model assets are local to the project's centralized ``algorithms/pth``
directory.  In particular,
``ViTModel.from_pretrained`` is always called with ``local_files_only=True``;
the production pipeline must never attempt to download a Hugging Face model.
Heavy ML imports are delayed until a classifier is actually constructed.  This
keeps lightweight operations such as importing a CLI module or showing its
``--help`` output usable when the optional ViT dependencies are not installed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import sys
import threading
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
ALGORITHMS_ROOT = SCRIPT_DIR.parents[1]
if str(ALGORITHMS_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS_ROOT))

from config.paths import (  # noqa: E402
    COMPLETENESS_VIT_CACHE_DIR,
    COMPLETENESS_VIT_CHECKPOINT_PATH,
)


DEFAULT_CHECKPOINT_PATH = COMPLETENESS_VIT_CHECKPOINT_PATH
DEFAULT_MODEL_CACHE = COMPLETENESS_VIT_CACHE_DIR

# Keep aliases with the concise names used by the standalone prediction
# script.  The *_PATH form is preferred in new callers because it makes the
# value's purpose explicit.
DEFAULT_CHECKPOINT = DEFAULT_CHECKPOINT_PATH

EXPECTED_CLASS_TO_IDX = {"complete": 0, "incomplete": 1}
INDEX_TO_CLASS = ("complete", "incomplete")


class CompletenessModelError(RuntimeError):
    """Raised when the offline ViT completeness model cannot be used."""


@dataclass(frozen=True)
class CompletenessResult:
    """The classification result consumed by the completeness filter stage."""

    label: str
    code: int
    confidence: float
    scores: dict[str, float]
    is_complete: bool


@dataclass(frozen=True)
class _Runtime:
    """Runtime-only dependencies kept out of module import time."""

    torch: Any
    nn: Any
    image: Any
    transforms: Any
    vit_model: Any


def _load_runtime() -> _Runtime:
    """Import inference dependencies only when inference is requested."""

    try:
        import torch
        import torch.nn as nn
        from PIL import Image
        from torchvision import transforms
        from transformers import ViTModel
    except ImportError as error:
        missing_name = getattr(error, "name", None) or "a required package"
        raise CompletenessModelError(
            "ViT completeness inference requires local torch, torchvision, "
            f"Pillow, and transformers dependencies; missing or broken: {missing_name}."
        ) from error

    return _Runtime(
        torch=torch,
        nn=nn,
        image=Image,
        transforms=transforms,
        vit_model=ViTModel,
    )


def _resolve_path(path: str | Path) -> Path:
    """Normalize a user supplied asset path without changing process state."""

    return Path(path).expanduser().resolve()


def _validate_model_assets(checkpoint_path: Path, model_cache: Path) -> None:
    """Fail before expensive imports/loading when local assets are absent."""

    if not checkpoint_path.is_file():
        raise FileNotFoundError(
            "ViT completeness checkpoint not found: "
            f"{checkpoint_path}. Expected best_com_base.pth under algorithms/pth."
        )
    if not model_cache.is_dir():
        raise FileNotFoundError(
            "Local ViT model cache not found: "
            f"{model_cache}. Expected the offline vit-base cache under algorithms/pth/cache."
        )


def _select_device(torch_module: Any, device: str | Any | None) -> Any:
    """Choose CUDA by default and safely fall back to CPU when unavailable."""

    if device is None or str(device).lower() == "auto":
        return torch_module.device(
            "cuda" if torch_module.cuda.is_available() else "cpu"
        )

    selected = torch_module.device(device)
    if selected.type == "cuda" and not torch_module.cuda.is_available():
        raise CompletenessModelError(
            "CUDA was requested for ViT completeness inference, but CUDA is unavailable."
        )
    return selected


def select_device(device: str | Any | None = None) -> Any:
    """Public device selector, useful for diagnostics and explicit CPU use."""

    runtime = _load_runtime()
    return _select_device(runtime.torch, device)


def _build_eval_transform(runtime: _Runtime) -> Any:
    """Build the exact deterministic transform used by train.py validation."""

    return runtime.transforms.Compose(
        [
            runtime.transforms.Resize((224, 224)),
            runtime.transforms.ToTensor(),
            runtime.transforms.Normalize(
                mean=[0.5, 0.5, 0.5],
                std=[0.5, 0.5, 0.5],
            ),
        ]
    )


def build_eval_transform() -> Any:
    """Return the validation/test transform while retaining lazy imports."""

    return _build_eval_transform(_load_runtime())


def _create_model(
    runtime: _Runtime,
    model_cache: Path,
    num_labels: int,
) -> Any:
    """Create the same ViT + CLS-head architecture used during training."""

    class ViTForImageClassification(runtime.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.vit = runtime.vit_model.from_pretrained(
                str(model_cache),
                local_files_only=True,
            )
            self.dropout = runtime.nn.Dropout(0.1)
            self.classifier = runtime.nn.Linear(
                self.vit.config.hidden_size,
                num_labels,
            )

        def forward(self, inputs: Any) -> Any:
            outputs = self.vit(pixel_values=inputs)
            cls_token = outputs.last_hidden_state[:, 0]
            return self.classifier(self.dropout(cls_token))

    return ViTForImageClassification()


def _load_checkpoint(
    torch_module: Any,
    checkpoint_path: Path,
) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    """Load and validate the training checkpoint entirely on CPU.

    The checkpoint also stores optimizer state.  Loading it on CPU first avoids
    needlessly mapping that state into GPU memory before only the model state is
    copied into the ViT module.
    """

    try:
        checkpoint = torch_module.load(
            checkpoint_path,
            map_location="cpu",
            weights_only=True,
        )
    except Exception as error:
        raise CompletenessModelError(
            "Unable to load the ViT completeness checkpoint with "
            f"weights_only=True: {checkpoint_path}."
        ) from error

    if not isinstance(checkpoint, Mapping):
        raise CompletenessModelError(
            "Invalid ViT completeness checkpoint: expected a mapping with "
            "'model_state_dict' and 'class_to_idx'."
        )

    class_to_idx = checkpoint.get("class_to_idx")
    if not isinstance(class_to_idx, dict) or class_to_idx != EXPECTED_CLASS_TO_IDX:
        raise CompletenessModelError(
            "Unexpected ViT completeness class_to_idx mapping: "
            f"{class_to_idx!r}. Expected exactly {EXPECTED_CLASS_TO_IDX!r}."
        )

    model_state_dict = checkpoint.get("model_state_dict")
    if not isinstance(model_state_dict, Mapping):
        raise CompletenessModelError(
            "Invalid ViT completeness checkpoint: missing a mapping-valued "
            "'model_state_dict'."
        )

    # The labels are intentionally fixed, rather than inferred from an
    # arbitrary mapping, so an accidentally swapped training dataset fails
    # loudly instead of silently reversing pipeline semantics.
    return model_state_dict, INDEX_TO_CLASS


class ViTCompletenessClassifier:
    """One fully initialized, offline ViT completeness classifier.

    Args:
        checkpoint_path: Optional local ``best_com_base.pth`` override.
        model_cache: Optional local Hugging Face ViT cache override.
        device: ``None``/``"auto"`` selects CUDA when available, otherwise
            CPU.  ``"cpu"`` and an available ``"cuda"`` are also accepted.
    """

    def __init__(
        self,
        checkpoint_path: str | Path | None = None,
        model_cache: str | Path | None = None,
        device: str | Any | None = None,
    ) -> None:
        self.checkpoint_path = _resolve_path(
            DEFAULT_CHECKPOINT_PATH if checkpoint_path is None else checkpoint_path
        )
        self.model_cache = _resolve_path(
            DEFAULT_MODEL_CACHE if model_cache is None else model_cache
        )
        _validate_model_assets(self.checkpoint_path, self.model_cache)

        self._runtime = _load_runtime()
        self.device = _select_device(self._runtime.torch, device)
        self._transform = _build_eval_transform(self._runtime)

        model_state_dict, self.index_to_class = _load_checkpoint(
            self._runtime.torch,
            self.checkpoint_path,
        )

        try:
            # Keep construction and checkpoint loading on CPU.  Moving the
            # finished module is deliberately the final step, so the checkpoint
            # optimizer state can never be placed on the GPU.
            model = _create_model(
                self._runtime,
                self.model_cache,
                num_labels=len(self.index_to_class),
            )
            model.load_state_dict(model_state_dict, strict=True)
            model = model.to(self.device)
            model.eval()
        except Exception as error:
            raise CompletenessModelError(
                "Unable to construct or strictly load the local ViT "
                f"completeness model from {self.checkpoint_path}."
            ) from error
        finally:
            # ``load_state_dict`` copies tensors into the model, so retaining a
            # reference to this large mapping only wastes host memory.
            del model_state_dict

        self._model = model

    def predict_path(self, image_path: str | Path) -> CompletenessResult:
        """Classify one black-background fish image using softmax + argmax."""

        path = _resolve_path(image_path)
        if not path.is_file():
            raise FileNotFoundError(f"Completeness input image not found: {path}")

        try:
            with self._runtime.image.open(path) as source_image:
                image_tensor = self._transform(source_image.convert("RGB"))
        except OSError as error:
            raise CompletenessModelError(
                f"Cannot read completeness input image: {path}"
            ) from error

        torch_module = self._runtime.torch
        with torch_module.inference_mode():
            inputs = image_tensor.unsqueeze(0).to(self.device)
            logits = self._model(inputs)
            if getattr(logits, "ndim", None) != 2 or tuple(logits.shape) != (
                1,
                len(self.index_to_class),
            ):
                raise CompletenessModelError(
                    "ViT completeness model returned logits with unexpected "
                    f"shape {getattr(logits, 'shape', None)!r}; expected "
                    f"(1, {len(self.index_to_class)})."
                )
            probabilities = torch_module.softmax(logits, dim=1)[0].detach().cpu()
            confidence, prediction = probabilities.max(dim=0)

        code = int(prediction.item())
        if code < 0 or code >= len(self.index_to_class):
            raise CompletenessModelError(
                f"ViT completeness model returned an invalid class index: {code}."
            )

        values = probabilities.tolist()
        scores = {
            class_name: float(values[index])
            for index, class_name in enumerate(self.index_to_class)
        }
        label = self.index_to_class[code]
        is_complete = label == "complete"
        return CompletenessResult(
            label=label,
            code=code,
            confidence=float(confidence.item()),
            scores=scores,
            is_complete=is_complete,
        )


_DEFAULT_CLASSIFIER: ViTCompletenessClassifier | None = None
_DEFAULT_CLASSIFIER_LOCK = threading.Lock()


def get_default_classifier() -> ViTCompletenessClassifier:
    """Return the process-wide classifier, initializing it exactly once."""

    global _DEFAULT_CLASSIFIER
    if _DEFAULT_CLASSIFIER is not None:
        return _DEFAULT_CLASSIFIER

    with _DEFAULT_CLASSIFIER_LOCK:
        if _DEFAULT_CLASSIFIER is None:
            _DEFAULT_CLASSIFIER = ViTCompletenessClassifier()
        return _DEFAULT_CLASSIFIER


def judge_completeness(image_path: str | Path) -> CompletenessResult:
    """Classify an image with the process-wide default classifier."""

    return get_default_classifier().predict_path(image_path)


__all__ = [
    "CompletenessModelError",
    "CompletenessResult",
    "DEFAULT_CHECKPOINT",
    "DEFAULT_CHECKPOINT_PATH",
    "DEFAULT_MODEL_CACHE",
    "EXPECTED_CLASS_TO_IDX",
    "ViTCompletenessClassifier",
    "build_eval_transform",
    "get_default_classifier",
    "judge_completeness",
    "select_device",
]
