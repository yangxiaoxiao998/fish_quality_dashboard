from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from app.services.update.upgrade_manager import storage


DEFAULT_RESTART_CMD_ENV = "FISH_UPDATE_RESTART_CMD"
DEFAULT_PRE_UPDATE_CMD_ENV = "FISH_UPDATE_PRE_UPDATE_CMD"


def _run_shell_command(command: str) -> dict[str, Any]:
    started = time.time()
    completed = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
    )
    return {
        "ok": completed.returncode == 0,
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout or "",
        "stderr": completed.stderr or "",
        "duration_seconds": round(time.time() - started, 2),
    }


def run_pre_update_hook(task: dict[str, Any]) -> dict[str, Any]:
    command = os.environ.get(DEFAULT_PRE_UPDATE_CMD_ENV, "").strip()
    if not command:
        return {"ok": True, "skipped": True, "hook": "pre_update", "task_id": task.get("task_id")}
    result = _run_shell_command(command)
    result.update({"hook": "pre_update", "task_id": task.get("task_id")})
    return result


def run_post_update_hook(task: dict[str, Any], *, success: bool) -> dict[str, Any]:
    command = os.environ.get(DEFAULT_RESTART_CMD_ENV, "").strip()
    if not command:
        return {
            "ok": True,
            "skipped": True,
            "hook": "post_update",
            "success": success,
            "task_id": task.get("task_id"),
        }
    result = _run_shell_command(command)
    result.update(
        {
            "hook": "post_update",
            "success": success,
            "task_id": task.get("task_id"),
        }
    )
    return result


def run_health_check_hook(task: dict[str, Any]) -> dict[str, Any]:
    script_path = storage.UPDATE_DIR / "health_check.sh"
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    precheck = metadata.get("precheck") if isinstance(metadata.get("precheck"), dict) else {}
    healthcheck_url = str(precheck.get("healthcheck") or "http://127.0.0.1:8000/api/health").strip()

    # The packaged backend bundles the shell/Python health script inside
    # PyInstaller's read-only _internal directory.  Use the HTTP endpoint for
    # frozen/artifact installations instead of trying to execute source-mode
    # ``python`` helper snippets or mutate bundled files.
    if bool(getattr(sys, "frozen", False)) or (
        (storage.INSTALL_ROOT / "fish-quality-backend").is_file()
        and (storage.INSTALL_ROOT / "config").is_dir()
    ):
        deadline = time.time() + 90
        last_error = ""
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(healthcheck_url, timeout=3) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if isinstance(payload, dict) and (
                    payload.get("status") == "ok" or payload.get("ok") is True
                ):
                    return {
                        "ok": True,
                        "hook": "health_check",
                        "task_id": task.get("task_id"),
                        "url": healthcheck_url,
                        "health": payload,
                        "fallback": "packaged_http",
                    }
                last_error = f"unexpected health payload: {payload!r}"
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                last_error = str(exc)
            time.sleep(1)
        return {
            "ok": False,
            "hook": "health_check",
            "task_id": task.get("task_id"),
            "url": healthcheck_url,
            "error": last_error or "packaged backend health check timed out",
            "fallback": "packaged_http",
        }

    if not script_path.exists():
        deadline = time.time() + 30
        last_error = ""
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(healthcheck_url, timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if isinstance(payload, dict) and (
                    payload.get("status") == "ok" or payload.get("ok") is True
                ):
                    return {
                        "ok": True,
                        "hook": "health_check",
                        "task_id": task.get("task_id"),
                        "url": healthcheck_url,
                        "health": payload,
                        "fallback": "http",
                    }
                last_error = f"unexpected health payload: {payload!r}"
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                last_error = str(exc)
            time.sleep(1)
        return {
            "ok": False,
            "hook": "health_check",
            "task_id": task.get("task_id"),
            "url": healthcheck_url,
            "error": last_error or f"health_check.sh not found: {script_path}",
            "fallback": "http",
        }

    env = os.environ.copy()
    env.setdefault("FISH_UPDATE_PYTHON_BIN", sys.executable)
    command = [
        "bash",
        str(script_path),
        "--install-root",
        str(storage.INSTALL_ROOT),
        "--python-bin",
        sys.executable,
    ]
    started = time.time()
    completed = subprocess.run(
        command,
        cwd=str(storage.PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "ok": completed.returncode == 0,
        "hook": "health_check",
        "task_id": task.get("task_id"),
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout or "",
        "stderr": completed.stderr or "",
        "duration_seconds": round(time.time() - started, 2),
    }
