from __future__ import annotations

import time

import cv2
import requests


# ===== 配置 =====
SERVER_BASE = "http://localhost:8000/api"
# 改成你的 B 端 IP
TARGET_FPS = 1  # 先用低帧率
CAMERA_INDEX = 0


def start_camera():
    url = f"{SERVER_BASE}/camera/start"
    resp = requests.post(
        url,
        json={"camera_index": 0},
        timeout=5,
    )
    resp.raise_for_status()
    print("start:", resp.json())

def stop_camera():
    url = f"{SERVER_BASE}/camera/stop"
    try:
        resp = requests.post(url, timeout=5)
        resp.raise_for_status()
        print("stop:", resp.json())
    except Exception as e:
        print("stop failed:", e)


def send_frame(jpeg_bytes: bytes):
    url = f"{SERVER_BASE}/camera/frame"

    resp = requests.post(
        url,
        data=jpeg_bytes,
        headers={"Content-Type": "application/octet-stream"},
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    # 1. 启动 session
    start_camera()

    # 2. 打开摄像头
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("cannot open camera")
        stop_camera()
        return

    interval = 1.0 / TARGET_FPS
    frame_index = 0

    print("start sending frames...")

    try:
        while True:
            t0 = time.time()

            ret, frame = cap.read()
            if not ret:
                print("read frame failed")
                continue

            # 3. 编码为 JPEG
            ok, encoded = cv2.imencode(".jpg", frame)
            if not ok:
                print("encode failed")
                continue

            jpeg_bytes = encoded.tobytes()

            # 4. POST 到 B 端
            try:
                result = send_frame(jpeg_bytes)
                print(f"frame {frame_index}: ", result)
            except Exception as e:
                print(f"frame {frame_index} upload error:", e)

            frame_index += 1

            # 控制帧率
            elapsed = time.time() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("stopped by user")

    finally:
        cap.release()
        stop_camera()


if __name__ == "__main__":
    main()