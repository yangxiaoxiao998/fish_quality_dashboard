from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from app.paths import DATA_DIR, PROJECT_ROOT


ALGORITHM_PYTHON = os.getenv("ALGORITHM_PYTHON") or sys.executable

CALIBRATION_DIR = DATA_DIR / "calibration"
CALIBRATION_JSON = CALIBRATION_DIR / "current_calibration.json"

RESULTS_DIR = DATA_DIR / "results"
ALGO_CLI_LOG_DIR = DATA_DIR / "logs" / "algorithm_cli"

def extract_real_error(
    stdout: str = "",
    stderr: str = "",
    returncode: int | None = None,
    *,
    max_lines: int = 60,
) -> dict[str, Any]:
    """从 subprocess 输出中过滤 warning，提取真实错误。

    返回：
        {
            "error_message": str,         # 用户可见的错误描述
            "warning_lines": int,          # 过滤掉的 warning 行数
            "has_real_error": bool,        # 是否抓到真实错误（Traceback/Exception 等）
        }
    """
    text = "\n".join([stdout or "", stderr or ""]).strip()
    if not text:
        return {
            "error_message": (
                f"视觉矫正算法进程异常退出，returncode={returncode}，但没有输出错误日志。"
                "请查看后端 logs/calibration/ 下的完整日志。"
            ),
            "warning_lines": 0,
            "has_real_error": False,
        }

    lines = text.splitlines()

    # 1) 优先抓 Traceback 段（最后一处 Traceback 起直到末尾）
    tb_start = -1
    for i, line in enumerate(lines):
        if "Traceback (most recent call last)" in line:
            tb_start = i
    if tb_start >= 0:
        tb_text = "\n".join(lines[tb_start:]).strip()
        if len(tb_text) > 4000:
            tb_text = tb_text[-4000:]
        return {
            "error_message": tb_text,
            "warning_lines": sum(1 for l in lines[:tb_start] if _is_warning_line(l)),
            "has_real_error": True,
        }

    # 2) 抓常见真正错误关键字
    error_keywords = (
        "Error:",
        "Exception:",
        "RuntimeError:",
        "FileNotFoundError:",
        "ModuleNotFoundError:",
        "ImportError:",
        "ValueError:",
        "TypeError:",
        "PermissionError:",
        "OSError:",
        "AssertionError:",
        "CUDA out of memory",
        "torch.cuda.OutOfMemoryError",
        "No such file or directory",
        "FileNotFoundError",
        "Traceback",
        "AssertionError",
        "failed",
        "FAILED",
    )
    error_lines = [
        line for line in lines
        if (not _is_warning_line(line)) and any(k in line for k in error_keywords)
    ]
    if error_lines:
        snippet = "\n".join(error_lines[-max_lines:]).strip()
        if len(snippet) > 4000:
            snippet = snippet[-4000:]
        return {
            "error_message": snippet,
            "warning_lines": sum(1 for l in lines if _is_warning_line(l)),
            "has_real_error": True,
        }

    # 3) 没有 warning 也没有 error 关键字 → 取最后一段非空内容
    non_warning_lines = [line for line in lines if not _is_warning_line(line)]
    non_warning_text = "\n".join(non_warning_lines).strip()
    warning_count = sum(1 for l in lines if _is_warning_line(l))

    if non_warning_text:
        snippet = non_warning_text[-4000:]
        return {
            "error_message": snippet,
            "warning_lines": warning_count,
            "has_real_error": True,
        }

    # 4) 全是 warning
    return {
        "error_message": (
            f"视觉矫正算法进程异常退出，returncode={returncode}，"
            f"日志中仅包含 {warning_count} 条依赖库 warning，没有明确 Traceback。"
            "请查看后端 logs/calibration/ 下的完整日志定位真实原因。"
        ),
        "warning_lines": warning_count,
        "has_real_error": False,
    }


_WARNING_PATTERNS = (
    "FutureWarning:",
    "UserWarning:",
    "DeprecationWarning:",
    "PendingDeprecationWarning:",
    "ResourceWarning:",
    "RuntimeWarning:",
    "warnings.warn(",
    "Importing from timm.models.layers is deprecated",
    "timm.models.layers is deprecated, please import via timm.layers",
    "torch.meshgrid",
    "UserWarning: torch.meshgrid",
    "torch._C._jit_set_bailout_depth",
    "huggingface_hub",
    "resume_download is deprecated",
    "TRANSFORMERS_CACHE",
    "HF_HUB_OFFLINE",
    "_warn",
)


def _is_warning_line(line: str) -> bool:
    s = str(line or "")
    if not s:
        return False
    if "Warning" not in s and "warnings.warn" not in s and "deprecated" not in s.lower():
        return False
    return any(p in s for p in _WARNING_PATTERNS) or (
        "Warning:" in s and "Traceback" not in s
    )


VIDEO_ID_RE = re.compile(r"video_\d{8}_\d{6}_[0-9a-zA-Z]{4,}")


def _ensure_dirs() -> None:
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ALGO_CLI_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _to_project_relative(path: str | Path | None) -> str | None:
    if not path:
        return None

    p = Path(path)
    if not p.is_absolute():
        return p.as_posix()

    try:
        return p.relative_to(PROJECT_ROOT).as_posix()
    except Exception:
        return p.as_posix()


def _json_read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _text_read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _build_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()

    backend_dir = PROJECT_ROOT / "backend"
    algo_dir = PROJECT_ROOT / "algorithms"

    pythonpath_items = [
        str(PROJECT_ROOT),
        str(backend_dir),
        str(algo_dir),
        str(algo_dir / "GroundingDINO"),
    ]

    old_pythonpath = env.get("PYTHONPATH")
    if old_pythonpath:
        pythonpath_items.append(old_pythonpath)

    env["PYTHONPATH"] = os.pathsep.join(pythonpath_items)

    env.setdefault("FISH_PROJECT_ROOT", str(PROJECT_ROOT))
    env.setdefault("FISH_ALGO_ROOT", str(algo_dir))
    env.setdefault("FISH_DATA_ROOT", str(DATA_DIR))

    # 统一权重路径：所有 SAM/SAM-HQ/GroundingDINO 权重都从 algorithms/pth 解析
    pth_dir = algo_dir / "pth"
    env.setdefault("FISH_SAM_CHECKPOINT", str(pth_dir / "sam_vit_h_4b8939.pth"))
    env.setdefault("FISH_SAM_HQ_CHECKPOINT", str(pth_dir / "sam_hq_vit_h.pth"))
    env.setdefault(
        "FISH_GROUNDING_DINO_CHECKPOINT", str(pth_dir / "groundingdino_swint_ogc.pth")
    )
    env.setdefault(
        "FISH_BERT_BASE_UNCASED_PATH", str(algo_dir / "bert-base-uncased")
    )

    env.setdefault("TRANSFORMERS_OFFLINE", "1")
    env.setdefault("HF_HUB_OFFLINE", "1")

    # 注意：不再设置 FISH_ALGO_MODE，不再支持 mock / real 切换。

    if extra:
        env.update(extra)

    return env


def _write_cli_log(
    *,
    name: str,
    command: list[str],
    returncode: int | None,
    stdout: str,
    stderr: str,
    extra: dict[str, Any] | None = None,
) -> str:
    """写入一条算法 CLI 调用日志（纯文本 + 元信息头），返回相对项目根的 POSIX 路径。"""
    _ensure_dirs()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_name = re.sub(r"[^0-9A-Za-z_.-]+", "_", name)[:80] or "algorithm_cli"
    log_path = ALGO_CLI_LOG_DIR / f"{ts}_{safe_name}.log"

    header_lines = [
        f"time: {ts}",
        f"cwd: {PROJECT_ROOT}",
        f"python: {ALGORITHM_PYTHON}",
        f"command: {' '.join(command)}",
    ]
    if extra:
        for k, v in extra.items():
            header_lines.append(f"{k}: {v}")
    header_lines.append(f"returncode: {returncode}")

    log_text = (
        "\n".join(header_lines)
        + "\n\n===== STDOUT =====\n"
        + (stdout or "")
        + "\n\n===== STDERR =====\n"
        + (stderr or "")
        + "\n"
    )

    log_path.write_text(log_text, encoding="utf-8")

    return _to_project_relative(log_path) or str(log_path)


def _run_cli(
    command: list[str],
    *,
    timeout: int = 7200,
    log_name: str,
    extra_log: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ensure_dirs()

    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            env=_build_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        returncode = int(completed.returncode)

        log_path = _write_cli_log(
            name=log_name,
            command=command,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            extra=extra_log,
        )

        return {
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "log_path": log_path,
        }

    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")

        log_path = _write_cli_log(
            name=log_name,
            command=command,
            returncode=None,
            stdout=stdout,
            stderr=stderr,
            extra={
                **(extra_log or {}),
                "timeout": timeout,
                "exception": "TimeoutExpired",
            },
        )

        return {
            "returncode": -1,
            "stdout": stdout,
            "stderr": stderr,
            "log_path": log_path,
            "error_message": f"算法 CLI 超时：{timeout} 秒",
        }

    except Exception as exc:
        log_path = _write_cli_log(
            name=log_name,
            command=command,
            returncode=None,
            stdout="",
            stderr=f"{type(exc).__name__}: {exc}",
            extra={
                **(extra_log or {}),
                "exception": type(exc).__name__,
            },
        )

        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "log_path": log_path,
            "error_message": f"{type(exc).__name__}: {exc}",
        }


def extract_video_id_from_stdout(stdout: str) -> str | None:
    if not stdout:
        return None

    match = VIDEO_ID_RE.search(stdout)
    if match:
        return match.group(0)

    patterns = [
        r"video_id\s*[:=]\s*([0-9A-Za-z_\-]+)",
        r"VIDEO_ID\s*[:=]\s*([0-9A-Za-z_\-]+)",
    ]

    for pattern in patterns:
        m = re.search(pattern, stdout)
        if m:
            value = m.group(1).strip()
            if value:
                return value

    return None


def find_latest_result_video_id() -> str | None:
    if not RESULTS_DIR.exists():
        return None

    candidates: list[Path] = []
    for p in RESULTS_DIR.iterdir():
        if not p.is_dir():
            continue
        if p.name.endswith(".partial"):
            continue
        if (p / "reports" / "pipeline_result.json").exists():
            candidates.append(p)

    if not candidates:
        return None

    latest = max(candidates, key=lambda x: x.stat().st_mtime)
    return latest.name


def pipeline_result_path(video_id: str) -> Path:
    return RESULTS_DIR / video_id / "reports" / "pipeline_result.json"


def error_conclusion_path(video_id: str) -> Path:
    return RESULTS_DIR / video_id / "reports" / "error_conclusion.txt"


def read_pipeline_result(video_id: str) -> dict[str, Any]:
    path = pipeline_result_path(video_id)
    if not path.exists():
        raise FileNotFoundError(f"pipeline_result.json 不存在：{path}")
    return _json_read(path)


def read_error_conclusion(video_id: str) -> str:
    path = error_conclusion_path(video_id)
    if not path.exists():
        return ""
    return _text_read(path)


def run_calibration_cli(
    *,
    source_image: str,
    camera_id: str = "default",
    device: str = "cuda",
    overwrite: bool = True,
    timeout: int = 1800,
) -> dict[str, Any]:
    _ensure_dirs()

    # 统一路径解析：支持 data/uploads/... / uploads/... / /static/... / 绝对路径 / 旧 PROJECT_ROOT/uploads/...
    from app.services.path_service import resolve_data_path

    src = resolve_data_path(source_image)
    if not src:
        return {
            "ok": False,
            "returncode": -1,
            "error_message": (
                f"标定图片路径解析失败：{source_image}。"
                "请确认路径形如 data/uploads/calibration/YYYY/MM/DD/xxx.jpg，"
                "或重新点击「摄像头拍摄」生成图片。"
            ),
            "calibration_json": _to_project_relative(CALIBRATION_JSON),
        }

    if not src.exists():
        return {
            "ok": False,
            "returncode": -1,
            "error_message": (
                f"标定图片不存在：{src}。"
                f"输入路径：{source_image}。"
                "请重新点击「摄像头拍摄」确认预览图出现后再执行视觉矫正。"
            ),
            "calibration_json": _to_project_relative(CALIBRATION_JSON),
        }

    command = [
        ALGORITHM_PYTHON,
        "-m",
        "algorithms.core.adjust_calibration",
        "--source-image",
        str(src),
        "--camera-id",
        str(camera_id),
        "--device",
        str(device),
    ]

    if overwrite:
        command.append("--overwrite")

    result = _run_cli(
        command,
        timeout=timeout,
        log_name="adjust_calibration",
        extra_log={"source_image": str(src), "camera_id": camera_id},
    )

    ok = result["returncode"] == 0 and CALIBRATION_JSON.exists()

    payload: dict[str, Any] = {
        "ok": ok,
        "returncode": result["returncode"],
        "calibration_json": _to_project_relative(CALIBRATION_JSON),
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "log_path": result["log_path"],
    }

    if not ok:
        stdout_text = result.get("stdout") or ""
        stderr_text = result.get("stderr") or ""
        if result.get("returncode") == 0 and not CALIBRATION_JSON.exists():
            extracted = extract_real_error(
                stdout_text,
                stderr_text,
                result.get("returncode"),
            )
            payload["error_message"] = (
                extracted["error_message"]
                + "\n（算法进程返回 0，但未生成 calibration JSON 文件。）"
            )
        else:
            extracted = extract_real_error(
                stdout_text,
                stderr_text,
                result.get("returncode"),
            )
            payload["error_message"] = extracted["error_message"]
        payload["warning_lines"] = extracted.get("warning_lines", 0)
        if not payload.get("warning_lines"):
            payload["warning_lines"] = 0
        if result.get("error_message") and not extracted["has_real_error"]:
            payload["runner_error"] = result["error_message"]

    return payload


def _resolve_video_id_after_pipeline(stdout: str, stderr: str) -> str | None:
    video_id = extract_video_id_from_stdout(stdout)
    if video_id:
        return video_id

    video_id = extract_video_id_from_stdout(stderr)
    if video_id:
        return video_id

    return find_latest_result_video_id()


def run_video_pipeline_cli(
    *,
    input_video: str,
    camera_id: str = "default",
    device: str = "cuda",
    video_fps: int = 25,
    overwrite: bool = True,
    timeout: int = 7200,
    video_id: str | None = None,
) -> dict[str, Any]:
    _ensure_dirs()

    if str(device).lower() == "cpu":
        return {
            "ok": False,
            "returncode": -1,
            "error_message": "正式流程禁止使用 --device cpu，请使用 cuda。",
        }

    video = Path(input_video).expanduser()
    if not video.is_absolute():
        video = PROJECT_ROOT / video

    if not video.exists():
        return {
            "ok": False,
            "returncode": -1,
            "error_message": f"输入视频不存在：{video}",
        }

    if not CALIBRATION_JSON.exists():
        return {
            "ok": False,
            "returncode": -1,
            "error_message": (
                "缺少透视矩阵：data/calibration/current_calibration.json。"
                "请先调用标定接口 python -m algorithms.core.adjust_calibration。"
            ),
            "calibration_json": _to_project_relative(CALIBRATION_JSON),
        }

    before_video_ids = {
        p.name
        for p in RESULTS_DIR.iterdir()
        if p.is_dir() and not p.name.endswith(".partial")
    } if RESULTS_DIR.exists() else set()

    command = [
        ALGORITHM_PYTHON,
        "-m",
        "algorithms.tools.run_video_pipeline",
        "--input-video",
        str(video),
        "--camera-id",
        str(camera_id),
        "--device",
        str(device),
        "--video-fps",
        str(video_fps),
    ]

    if overwrite:
        command.append("--overwrite")

    if video_id:
        command.extend(["--video-id", str(video_id)])

    result = _run_cli(
        command,
        timeout=timeout,
        log_name="run_video_pipeline",
        extra_log={
            "input_video": str(video),
            "video_id": str(video_id) if video_id else None,
            "camera_id": camera_id,
            "video_fps": video_fps,
        },
    )

    video_id = _resolve_video_id_after_pipeline(result["stdout"], result["stderr"])

    # 如果 stdout 没解析到，就从新增 results 目录兜底找。
    if not video_id and RESULTS_DIR.exists():
        after_dirs = [
            p for p in RESULTS_DIR.iterdir()
            if p.is_dir()
            and not p.name.endswith(".partial")
            and p.name not in before_video_ids
            and (p / "reports" / "pipeline_result.json").exists()
        ]
        if after_dirs:
            latest = max(after_dirs, key=lambda x: x.stat().st_mtime)
            video_id = latest.name

    pipeline_payload: dict[str, Any] | None = None
    error_text = ""

    if video_id:
        try:
            pipeline_payload = read_pipeline_result(video_id)
        except Exception:
            pipeline_payload = None

        try:
            error_text = read_error_conclusion(video_id)
        except Exception:
            error_text = ""

    status = pipeline_payload.get("status") if pipeline_payload else None
    ok = result["returncode"] == 0 and status == "done"

    response: dict[str, Any] = {
        "ok": ok,
        "returncode": result["returncode"],
        "video_id": video_id,
        "status": status,
        "result_dir": f"data/results/{video_id}" if video_id else None,
        "pipeline_result_path": (
            f"data/results/{video_id}/reports/pipeline_result.json"
            if video_id
            else None
        ),
        "error_conclusion_path": (
            f"data/results/{video_id}/reports/error_conclusion.txt"
            if video_id
            else None
        ),
        "pipeline_result": pipeline_payload,
        "error_conclusion_text": error_text,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "log_path": result["log_path"],
    }

    if not ok:
        response["error_message"] = (
            (pipeline_payload or {}).get("error_message")
            or error_text
            or result.get("error_message")
            or result["stderr"]
            or result["stdout"]
            or "算法视频处理失败"
        )

    return response


def algorithm_path_to_static_url(path: str | None) -> str | None:
    if not path:
        return None

    p = str(path).replace("\\", "/")

    if p.startswith("data/"):
        p = p[len("data/"):]

    if p.startswith("/"):
        p = p.lstrip("/")

    return "/static/" + p


__all__ = [
    "ALGORITHM_PYTHON",
    "CALIBRATION_JSON",
    "RESULTS_DIR",
    "algorithm_path_to_static_url",
    "extract_video_id_from_stdout",
    "find_latest_result_video_id",
    "pipeline_result_path",
    "error_conclusion_path",
    "read_pipeline_result",
    "read_error_conclusion",
    "run_calibration_cli",
    "run_video_pipeline_cli",
]
