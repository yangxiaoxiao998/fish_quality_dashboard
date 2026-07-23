#!/usr/bin/env python3
"""Build a fixed-batch-64 FP16 TensorRT engine from RF-DETR ONNX.

Designed for:
  - TensorRT 10.8.x
  - NVIDIA RTX 5060 Ti
  - ONNX input: (-1, 3, 432, 432)
  - Runtime input: (64, 3, 432, 432)

The previous engine is backed up only after the new engine has been built and
successfully deserialized.
"""

from __future__ import annotations

import argparse
import gc
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

try:
    import tensorrt as trt
except ImportError as exc:
    raise SystemExit(
        "TensorRT is not installed in the current Python environment.\n"
        "Activate the full environment first: conda activate full"
    ) from exc


EXPECTED_TRT_MAJOR_MINOR = (10, 8)
EXPECTED_ONNX_SHAPE = (-1, 3, 432, 432)
FIXED_BATCH_SIZE = 64
FIXED_INPUT_SHAPE = (FIXED_BATCH_SIZE, 3, 432, 432)
GIB = 1024**3
MIB = 1024**2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert RF-DETR ONNX to a TensorRT 10.8 FP16 engine with "
            "fixed batch size 64."
        )
    )
    parser.add_argument(
        "--onnx",
        type=Path,
        default=Path(
            "algorithms/pth/output_onnx_dynamic/rfdetr-seg-medium.onnx"
        ),
        help="input ONNX path",
    )
    parser.add_argument(
        "--engine",
        type=Path,
        default=Path(
            "algorithms/pth/output_onnx_dynamic/"
            "rfdetr-seg-medium-b64-fp16.engine"
        ),
        help="output TensorRT engine path",
    )
    parser.add_argument(
        "--workspace-gib",
        type=float,
        default=8.0,
        help="TensorRT workspace upper limit in GiB (default: 8)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="enable verbose TensorRT logs",
    )
    return parser.parse_args()


def as_shape(values: Iterable[int]) -> tuple[int, ...]:
    return tuple(int(value) for value in values)


def parse_version(version: str) -> tuple[int, int]:
    try:
        parts = version.split(".")
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError) as exc:
        raise RuntimeError(f"Cannot parse TensorRT version: {version}") from exc


def print_rule(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def print_network_outputs(network: trt.INetworkDefinition) -> None:
    print("ONNX outputs:")
    for index in range(network.num_outputs):
        tensor = network.get_output(index)
        print(
            f"  [{index}] name={tensor.name}, "
            f"shape={as_shape(tensor.shape)}, dtype={tensor.dtype}"
        )


def validate_engine(
    engine_file: Path,
    logger: trt.ILogger,
    input_name: str,
) -> None:
    print("\n[4/5] Deserializing and validating the new engine...")

    runtime = trt.Runtime(logger)
    engine_bytes = engine_file.read_bytes()
    engine = runtime.deserialize_cuda_engine(engine_bytes)
    if engine is None:
        raise RuntimeError("The newly built engine could not be deserialized")

    profile = tuple(
        as_shape(shape)
        for shape in engine.get_tensor_profile_shape(input_name, 0)
    )
    expected_profile = (
        FIXED_INPUT_SHAPE,
        FIXED_INPUT_SHAPE,
        FIXED_INPUT_SHAPE,
    )

    print("Engine profile:")
    print("  min:", profile[0])
    print("  opt:", profile[1])
    print("  max:", profile[2])

    if profile != expected_profile:
        raise RuntimeError(
            "The engine profile is not fixed batch 64: "
            f"expected={expected_profile}, actual={profile}"
        )

    context = engine.create_execution_context()
    if context is None:
        raise RuntimeError("Could not create a TensorRT execution context")

    declared_shape = as_shape(engine.get_tensor_shape(input_name))
    if -1 in declared_shape:
        if not context.set_input_shape(input_name, FIXED_INPUT_SHAPE):
            raise RuntimeError(
                f"Could not set runtime input shape to {FIXED_INPUT_SHAPE}"
            )

    runtime_shape = as_shape(context.get_tensor_shape(input_name))
    print("Declared input shape:", declared_shape)
    print("Runtime input shape: ", runtime_shape)
    if runtime_shape != FIXED_INPUT_SHAPE:
        raise RuntimeError(
            "Unexpected runtime input shape: "
            f"expected={FIXED_INPUT_SHAPE}, actual={runtime_shape}"
        )

    print("Engine I/O tensors:")
    for index in range(engine.num_io_tensors):
        name = engine.get_tensor_name(index)
        print(
            f"  [{index}] name={name}, "
            f"shape={as_shape(context.get_tensor_shape(name))}, "
            f"dtype={engine.get_tensor_dtype(name)}, "
            f"mode={engine.get_tensor_mode(name)}"
        )

    del context
    del engine
    del runtime


def build_engine(args: argparse.Namespace) -> Path:
    onnx_path = args.onnx.expanduser().resolve()
    engine_path = args.engine.expanduser().resolve()
    temporary_path = engine_path.with_name(engine_path.name + ".building")

    if args.workspace_gib <= 0:
        raise ValueError("--workspace-gib must be greater than zero")
    if parse_version(trt.__version__) != EXPECTED_TRT_MAJOR_MINOR:
        raise RuntimeError(
            "This script requires TensorRT 10.8.x; "
            f"the current version is {trt.__version__}"
        )
    if not onnx_path.is_file():
        raise FileNotFoundError(f"ONNX file not found: {onnx_path}")

    engine_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path.unlink(missing_ok=True)

    severity = trt.Logger.VERBOSE if args.verbose else trt.Logger.INFO
    logger = trt.Logger(severity)
    trt.init_libnvinfer_plugins(logger, "")

    print_rule("TensorRT engine build configuration")
    print("TensorRT version: ", trt.__version__)
    print("ONNX:             ", onnx_path)
    print("Engine:           ", engine_path)
    print("Input shape:      ", FIXED_INPUT_SHAPE)
    print("Precision:         FP16 mixed precision")
    print("Workspace limit:  ", f"{args.workspace_gib:g} GiB")

    builder = trt.Builder(logger)
    if builder is None:
        raise RuntimeError("Could not create a TensorRT builder")
    if not builder.platform_has_fast_fp16:
        raise RuntimeError("The current GPU does not report fast FP16 support")

    network_flags = 1 << int(
        trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH
    )
    network = builder.create_network(network_flags)
    parser = trt.OnnxParser(network, logger)

    print("\n[1/5] Parsing ONNX...")
    if not parser.parse_from_file(str(onnx_path)):
        errors = "\n".join(
            str(parser.get_error(index))
            for index in range(parser.num_errors)
        )
        raise RuntimeError(f"ONNX parsing failed:\n{errors}")

    if network.num_inputs != 1:
        names = [
            network.get_input(index).name
            for index in range(network.num_inputs)
        ]
        raise RuntimeError(
            f"Expected exactly one ONNX input, found {network.num_inputs}: {names}"
        )

    input_tensor = network.get_input(0)
    input_name = input_tensor.name
    onnx_shape = as_shape(input_tensor.shape)
    print("ONNX input name: ", input_name)
    print("ONNX input shape:", onnx_shape)
    print("ONNX input dtype:", input_tensor.dtype)
    print_network_outputs(network)

    if onnx_shape != EXPECTED_ONNX_SHAPE:
        raise RuntimeError(
            "Unexpected ONNX input shape: "
            f"expected={EXPECTED_ONNX_SHAPE}, actual={onnx_shape}"
        )

    print("\n[2/5] Configuring fixed batch-64 FP16 profile...")
    config = builder.create_builder_config()
    workspace_bytes = int(args.workspace_gib * GIB)
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, workspace_bytes)
    config.set_flag(trt.BuilderFlag.FP16)

    profile = builder.create_optimization_profile()
    # TensorRT 10.8 Python API returns None from set_shape() on success.
    # Do not use its return value as a Boolean; validate the stored shapes.
    profile.set_shape(
        input_name,
        FIXED_INPUT_SHAPE,
        FIXED_INPUT_SHAPE,
        FIXED_INPUT_SHAPE,
    )

    actual_profile = tuple(
        as_shape(shape) for shape in profile.get_shape(input_name)
    )
    expected_profile = (
        FIXED_INPUT_SHAPE,
        FIXED_INPUT_SHAPE,
        FIXED_INPUT_SHAPE,
    )
    if actual_profile != expected_profile or not profile:
        raise RuntimeError(
            "Invalid TensorRT optimization profile: "
            f"expected={expected_profile}, actual={actual_profile}"
        )

    if config.add_optimization_profile(profile) < 0:
        raise RuntimeError("Could not add the TensorRT optimization profile")

    print("Profile min:", actual_profile[0])
    print("Profile opt:", actual_profile[1])
    print("Profile max:", actual_profile[2])

    print("\n[3/5] Building the engine; this can take several minutes...")
    started_at = time.monotonic()
    serialized_engine = builder.build_serialized_network(network, config)
    elapsed = time.monotonic() - started_at
    if serialized_engine is None:
        raise RuntimeError(
            "TensorRT engine build failed; inspect the TensorRT log above"
        )

    temporary_path.write_bytes(bytes(serialized_engine))
    print(f"Build time: {elapsed:.2f} seconds")
    print(f"Temporary engine size: {temporary_path.stat().st_size / MIB:.2f} MiB")

    del serialized_engine
    del profile
    del config
    del parser
    del network
    del builder
    gc.collect()

    validate_engine(temporary_path, logger, input_name)

    print("\n[5/5] Publishing validated engine...")
    if engine_path.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = engine_path.with_name(
            f"{engine_path.name}.previous_{timestamp}.bak"
        )
        shutil.copy2(engine_path, backup_path)
        print("Previous engine backup:", backup_path)

    temporary_path.replace(engine_path)
    print_rule("TensorRT engine build succeeded")
    print("Output:       ", engine_path)
    print("Size:         ", f"{engine_path.stat().st_size / MIB:.2f} MiB")
    print("Input shape:  ", FIXED_INPUT_SHAPE)
    print("Precision:     FP16 mixed precision")
    return engine_path


def main() -> int:
    args = parse_args()
    temporary_path = args.engine.expanduser().resolve().with_name(
        args.engine.expanduser().resolve().name + ".building"
    )
    try:
        build_engine(args)
    except KeyboardInterrupt:
        temporary_path.unlink(missing_ok=True)
        print("\nBuild interrupted; temporary engine removed.", file=sys.stderr)
        return 130
    except Exception as exc:
        temporary_path.unlink(missing_ok=True)
        print(f"\n[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())