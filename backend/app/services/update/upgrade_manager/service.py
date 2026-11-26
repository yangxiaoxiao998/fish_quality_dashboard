from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from app.services.update import updater
from app.services.update.upgrade_manager import hooks, storage
from app.services.update.upgrade_manager.models import FINAL_TASK_STATUSES


DEFAULT_AUTO_PRUNE_ENV = "FISH_UPDATE_AUTO_PRUNE"
DEFAULT_PRUNE_KEEP_UPLOADED_ENV = "FISH_UPDATE_PRUNE_KEEP_UPLOADED"
DEFAULT_PRUNE_KEEP_BACKUPS_ENV = "FISH_UPDATE_PRUNE_KEEP_BACKUPS"


def _latest_running_task() -> dict[str, Any] | None:
    for task in storage.list_tasks(limit=20):
        if task.get("status") not in FINAL_TASK_STATUSES:
            return task
    return None


def get_manager_health() -> dict[str, Any]:
    storage.ensure_manager_dirs()
    return {
        "ok": True,
        "service": "upgrade_manager",
        "project_root": str(storage.PROJECT_ROOT),
        "install_root": str(storage.INSTALL_ROOT),
        "config_root": str(storage.CONFIG_ROOT),
        "data_root": str(storage.DATA_ROOT),
        "state_root": str(storage.STATE_ROOT),
        "current_version": storage.read_current_version(),
        "current_config_version": storage.read_current_config_version(),
        "current_links": storage.current_link_targets(),
        "updater_script": str(storage.UPDATER_SCRIPT),
        "updater_script_exists": storage.UPDATER_SCRIPT.exists(),
    }


def get_manager_status() -> dict[str, Any]:
    tasks = storage.list_tasks(limit=20)
    return {
        "ok": True,
        "current_version": storage.read_current_version(),
        "version_file": str(storage.VERSION_FILE),
        "version_file_exists": storage.VERSION_FILE.exists(),
        "updater_script": str(storage.UPDATER_SCRIPT),
        "updater_script_exists": storage.UPDATER_SCRIPT.exists(),
        "uploaded_packages": storage.list_update_packages(),
        "active_task": _latest_running_task(),
        "latest_task": tasks[0] if tasks else None,
        "install_root": str(storage.INSTALL_ROOT),
        "config_root": str(storage.CONFIG_ROOT),
        "data_root": str(storage.DATA_ROOT),
        "state_root": str(storage.STATE_ROOT),
        "current_config_version": storage.read_current_config_version(),
        "current_links": storage.current_link_targets(),
    }


def list_packages() -> list[dict[str, Any]]:
    return storage.list_update_packages()


def upload_update_package(file_bytes: bytes, original_filename: str) -> dict[str, Any]:
    return storage.save_uploaded_package(file_bytes=file_bytes, original_filename=original_filename)


def upload_update_package_stream(file_stream, original_filename: str) -> dict[str, Any]:
    return storage.save_uploaded_package_stream(file_stream, original_filename)


def import_update_package_from_path(source_path: str) -> dict[str, Any]:
    return storage.import_update_package_from_path(source_path)


def browse_importable_package_files(path: str | None = None) -> dict[str, Any]:
    return storage.browse_importable_package_files(path)


def precheck_package(package_path: str) -> dict[str, Any]:
    target = storage.resolve_package_path(package_path)
    result = updater.precheck_update_package(
        target,
        install_root=storage.INSTALL_ROOT,
        config_root=storage.CONFIG_ROOT,
    )
    return {
        "ok": True,
        **result,
        "updater_script": str(storage.UPDATER_SCRIPT),
    }


def _write_task_log_header(task_id: str, title: str, payload: dict[str, Any]) -> None:
    storage.append_task_log(
        task_id,
        f"== {title} ==\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n",
    )


def _spawn_task_process(task_id: str) -> int:
    # PyInstaller's executable exposes subcommands rather than Python's
    # ``-m`` switch.  Keep source/Conda development behavior while launching
    # the dedicated frozen upgrade-runner entrypoint in a packaged release.
    if bool(getattr(sys, "frozen", False)):
        command = [sys.executable, "upgrade-runner", task_id]
    else:
        command = [sys.executable, "-m", "app.services.update.upgrade_manager.runner", task_id]
    process = subprocess.Popen(
        command,
        cwd=str(storage.PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return int(process.pid)


def create_update_task(package_path: str, *, launch_runner: bool = True) -> dict[str, Any]:
    precheck = precheck_package(package_path)
    task = {
        "task_id": storage.new_task_id("update"),
        "task_type": "update",
        "package_path": precheck["package_path"],
        "backup_dir": None,
        "current_version": precheck["current_version"],
        "target_version": precheck["new_version"],
        "status": "queued",
        "created_at": storage.now_iso(),
        "started_at": None,
        "finished_at": None,
        "updated_targets": precheck["updated_targets"],
        "error_message": None,
        "message": "升级任务已创建",
        "runner_pid": None,
        "metadata": {
            "precheck": precheck,
        },
    }
    storage.write_task_state(task)
    _write_task_log_header(task["task_id"], "create_update_task", task)
    if launch_runner:
        runner_pid = _spawn_task_process(task["task_id"])
        latest = storage.read_task_state(task["task_id"])
        latest["runner_pid"] = runner_pid
        storage.write_task_state(latest)
        task = latest
    return task


def create_rollback_task(backup_dir: str, *, launch_runner: bool = True) -> dict[str, Any]:
    target = storage.resolve_backup_dir(backup_dir)
    manifest = storage.read_backup_manifest(target)
    task = {
        "task_id": storage.new_task_id("rollback"),
        "task_type": "rollback",
        "package_path": None,
        "backup_dir": str(target),
        "current_version": storage.read_current_version(),
        "target_version": str(manifest.get("current_version") or "").strip() or storage.read_current_version(),
        "status": "queued",
        "created_at": storage.now_iso(),
        "started_at": None,
        "finished_at": None,
        "updated_targets": [
            target_info.get("name")
            for target_info in manifest.get("components", [])
            if isinstance(target_info, dict)
        ],
        "error_message": None,
        "message": "回滚任务已创建",
        "runner_pid": None,
        "metadata": {
            "backup_manifest": manifest,
            "precheck": {
                "healthcheck": manifest.get("healthcheck"),
            },
        },
    }
    storage.write_task_state(task)
    _write_task_log_header(task["task_id"], "create_rollback_task", task)
    if launch_runner:
        runner_pid = _spawn_task_process(task["task_id"])
        latest = storage.read_task_state(task["task_id"])
        latest["runner_pid"] = runner_pid
        storage.write_task_state(latest)
        task = latest
    return task


def get_task_status(task_id: str) -> dict[str, Any]:
    return storage.read_task_state(task_id)


def get_task_log(task_id: str, max_bytes: int = 200 * 1024) -> dict[str, Any]:
    return {
        "ok": True,
        "task_id": task_id,
        "log": storage.read_task_log(task_id, max_bytes=max_bytes),
    }


def list_backups() -> list[dict[str, Any]]:
    return storage.list_backups()


def _write_task_state(task: dict[str, Any], **changes: Any) -> dict[str, Any]:
    task.update(changes)
    storage.write_task_state(task)
    return task


def _read_positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(value, 0)


def _prune_items(items: list[dict[str, Any]], keep: int) -> list[str]:
    if keep < 0:
        keep = 0
    removed: list[str] = []
    for item in items[keep:]:
        path = Path(str(item["path"]))
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
            removed.append(str(path))
        except Exception:
            continue
    return removed


def _run_auto_prune(task: dict[str, Any]) -> dict[str, Any]:
    enabled = os.environ.get(DEFAULT_AUTO_PRUNE_ENV, "1").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return {
            "ok": True,
            "skipped": True,
            "hook": "auto_prune",
            "task_id": task.get("task_id"),
            "reason": f"{DEFAULT_AUTO_PRUNE_ENV}={enabled or '0'}",
        }

    keep_uploaded = _read_positive_int_env(DEFAULT_PRUNE_KEEP_UPLOADED_ENV, 2)
    keep_backups = _read_positive_int_env(DEFAULT_PRUNE_KEEP_BACKUPS_ENV, 5)
    removed_uploaded = _prune_items(storage.list_update_packages(), keep_uploaded)
    removed_backups = _prune_items(storage.list_backups(limit=200), keep_backups)
    return {
        "ok": True,
        "hook": "auto_prune",
        "task_id": task.get("task_id"),
        "keep_uploaded": keep_uploaded,
        "keep_backups": keep_backups,
        "removed_uploaded": removed_uploaded,
        "removed_backups": removed_backups,
    }


def run_task(task_id: str) -> dict[str, Any]:
    task = storage.read_task_state(task_id)
    task_type = str(task.get("task_type") or "update")
    log_path = storage.task_log_path(task_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    _write_task_state(task, status="running", started_at=storage.now_iso(), message="任务开始执行")
    with log_path.open("a", encoding="utf-8") as log_stream, redirect_stdout(log_stream), redirect_stderr(log_stream):
        print(f"[task] task_id={task_id}", flush=True)
        print(f"[task] task_type={task_type}", flush=True)

        try:
            if task_type == "update":
                _write_task_state(task, status="backing_up", message="正在准备升级")
                pre_result = hooks.run_pre_update_hook(task)
                print(json.dumps(pre_result, ensure_ascii=False), flush=True)
                if pre_result.get("ok") is False:
                    raise updater.UpdateError(f"pre_update failed: {pre_result}")

                _write_task_state(task, status="applying", message="正在应用更新")
                result = updater.run_update(
                    Path(str(task["package_path"])),
                    install_root=storage.INSTALL_ROOT,
                    config_root=storage.CONFIG_ROOT,
                    data_root=storage.DATA_ROOT,
                )
                _write_task_state(
                    task,
                    status="verifying",
                    message="正在执行升级后检查",
                    backup_dir=str(result.get("backup_dir") or ""),
                    updated_targets=list(result.get("updated_targets") or task.get("updated_targets") or []),
                    current_version=str(result.get("current_version") or task.get("current_version") or ""),
                    target_version=str(result.get("new_version") or task.get("target_version") or ""),
                )

                post_result = hooks.run_post_update_hook(task, success=True)
                print(json.dumps(post_result, ensure_ascii=False), flush=True)
                if post_result.get("ok") is False:
                    raise updater.UpdateError(f"post_update failed: {post_result}")

                health_result = hooks.run_health_check_hook(task)
                print(json.dumps(health_result, ensure_ascii=False), flush=True)
                if health_result.get("ok") is False:
                    raise updater.UpdateError(f"health_check failed: {health_result}")

                prune_result = _run_auto_prune(task)
                task_metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
                task_metadata["auto_prune"] = prune_result
                task["metadata"] = task_metadata
                print(json.dumps(prune_result, ensure_ascii=False), flush=True)

                _write_task_state(
                    task,
                    status="success",
                    finished_at=storage.now_iso(),
                    message="升级任务执行成功",
                )

            elif task_type == "rollback":
                _write_task_state(task, status="rollbacking", message="正在执行回滚")
                pre_result = hooks.run_pre_update_hook(task)
                print(json.dumps(pre_result, ensure_ascii=False), flush=True)
                if pre_result.get("ok") is False:
                    raise updater.UpdateError(f"pre_update failed: {pre_result}")

                result = updater.rollback_backup(
                    Path(str(task["backup_dir"])),
                    install_root=storage.INSTALL_ROOT,
                )
                post_result = hooks.run_post_update_hook(task, success=True)
                print(json.dumps(post_result, ensure_ascii=False), flush=True)
                if post_result.get("ok") is False:
                    raise updater.UpdateError(f"post_update failed: {post_result}")

                health_result = hooks.run_health_check_hook(task)
                print(json.dumps(health_result, ensure_ascii=False), flush=True)
                if health_result.get("ok") is False:
                    raise updater.UpdateError(f"health_check failed: {health_result}")

                _write_task_state(
                    task,
                    status="rolled_back",
                    finished_at=storage.now_iso(),
                    current_version=str(result.get("restored_version") or task.get("target_version") or ""),
                    target_version=str(result.get("restored_version") or task.get("target_version") or ""),
                    updated_targets=list(result.get("rolled_back_targets") or task.get("updated_targets") or []),
                    message="回滚任务执行成功",
                )
            else:
                raise RuntimeError(f"unsupported task type: {task_type}")

        except updater.UpdateError as exc:
            payload = exc.payload if isinstance(exc.payload, dict) else {}
            print(f"[task][error] {exc}", flush=True)
            if payload:
                print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
            print(traceback.format_exc(), flush=True)

            if task_type == "update" and payload.get("status") == "rolled_back":
                _write_task_state(
                    task,
                    status="rolled_back",
                    finished_at=storage.now_iso(),
                    backup_dir=str(payload.get("backup_dir") or task.get("backup_dir") or ""),
                    error_message=str(exc),
                    message="升级失败，已自动回滚",
                )
            else:
                _write_task_state(
                    task,
                    status="failed",
                    finished_at=storage.now_iso(),
                    backup_dir=str(payload.get("backup_dir") or task.get("backup_dir") or ""),
                    error_message=str(exc),
                    message="任务执行失败",
                )
        except Exception as exc:
            print(f"[task][error] unexpected failure: {exc}", flush=True)
            print(traceback.format_exc(), flush=True)
            _write_task_state(
                task,
                status="failed",
                finished_at=storage.now_iso(),
                error_message=str(exc),
                message="任务执行失败",
            )

    return storage.read_task_state(task_id)
