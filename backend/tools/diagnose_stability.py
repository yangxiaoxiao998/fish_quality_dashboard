"""Read-only stability diagnostics for the development host.

Usage:
    PYTHONPATH=backend python backend/tools/diagnose_stability.py

This tool does not restart services or mutate the database.
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any


def _get_json(url: str) -> tuple[int | None, Any]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read().decode("utf-8", "replace")
            try:
                return response.status, json.loads(body)
            except json.JSONDecodeError:
                return response.status, body[:300]
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, {"error": str(exc)}


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def main() -> int:
    backend_port = int(os.getenv("BACKEND_PORT", "8000"))
    base = f"http://127.0.0.1:{backend_port}"
    checks: dict[str, Any] = {}
    failed = False

    for name, path in (
        ("ready", "/api/ready"),
        ("database", "/api/health/database"),
        ("camera", "/api/camera_yzt/status"),
        ("worker", "/api/worker/status"),
    ):
        status, payload = _get_json(base + path)
        checks[name] = {"http_status": status, "payload": payload}
        if status is None or status >= 500:
            failed = True

    for name, port in (
        ("mediamtx_rtmp", 1935),
        ("mediamtx_rtsp", 8554),
        ("mediamtx_hls", 8888),
        ("redis", 6379),
    ):
        checks[name] = {
            "host": "127.0.0.1",
            "port": port,
            "open": _port_open("127.0.0.1", port),
        }

    camera_payload = checks.get("camera", {}).get("payload")
    if isinstance(camera_payload, dict):
        required = (
            "state",
            "current_video_id",
            "reconnect_grace_seconds",
            "pi_resolution",
            "pi_arp",
        )
        missing = [key for key in required if key not in camera_payload]
        checks["camera_contract"] = {"ok": not missing, "missing": missing}
        failed = failed or bool(missing)

    print(json.dumps(checks, ensure_ascii=False, indent=2, default=str))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
