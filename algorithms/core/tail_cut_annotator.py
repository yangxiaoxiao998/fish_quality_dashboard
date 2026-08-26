"""在浏览器或 OpenCV 窗口中标注鱼尾截断点，并保存为 JSON。

默认标注目录：
``data/results/complete_match``

默认输出：
``data/results/complete_match/tail_cut_annotations.json``

默认启动不依赖 Qt/X11 的浏览器标注页面，适合 SSH、服务器和容器环境。
启动后，在浏览器打开终端中显示的网址即可。

浏览器操作：

- 鼠标左键：设置或修改尾部截断点，并立即自动保存；
- 左右方向键或页面按钮：切换图片；
- R 或“清除当前标注”：清除当前图片标注。

OpenCV 窗口操作（使用 ``--mode gui`` 启用）：

- 鼠标左键：设置或修改尾部截断点，并立即自动保存；
- 鼠标右键或 R：清除当前图片标注；
- Enter / 空格 / N / 右方向键：下一张；
- P / 左方向键：上一张；
- S：手动保存；
- Q / Esc：保存并退出。

窗口中的图像可以放大或缩小显示，JSON 始终保存原始图片的
``x, y`` 像素坐标。程序每次启动都会读取已有 JSON，默认从第一张
未标注图片继续。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Sequence
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np


THIS_DIR = Path(__file__).resolve().parent
ALGORITHMS_ROOT = THIS_DIR.parent
if str(ALGORITHMS_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS_ROOT))

from config.naming import FISH_CROP_SUFFIX, parse_fish_file_name  # noqa: E402
from config.paths import PROJECT_ROOT, ensure_dir, to_project_relative  # noqa: E402


DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "results" / "complete_match"
DEFAULT_JSON_NAME = "tail_cut_annotations.json"
SCHEMA_VERSION = 1
ANNOTATION_TYPE = "fish_tail_cut_point"
WINDOW_NAME = "Tail cut point annotator"
HEADER_HEIGHT = 92

RIGHT_KEYS = {65363, 2555904}
LEFT_KEYS = {65361, 2424832}
DELETE_KEYS = {8, 127, 3014656}


WEB_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>鱼尾截断点标注</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; background: #17191d; color: #eee; font-family: sans-serif; }
  main { max-width: 1500px; margin: auto; padding: 14px; }
  #top { display: flex; flex-wrap: wrap; gap: 9px; align-items: center; }
  button { border: 1px solid #666; border-radius: 5px; padding: 7px 13px;
           background: #30343b; color: #fff; cursor: pointer; }
  button:hover { background: #414751; }
  button:disabled { opacity: .4; cursor: default; }
  #status { margin-left: 4px; }
  #message { min-height: 1.5em; color: #7ee787; }
  #canvas-wrap { overflow: auto; text-align: center; border: 1px solid #444;
                 background: #222; padding: 8px; }
  canvas { max-width: 100%; max-height: calc(100vh - 155px); width: auto; height: auto;
           image-rendering: auto; cursor: crosshair; }
  .help { color: #bbb; font-size: 14px; }
</style>
</head>
<body><main>
  <div id="top">
    <button id="prev">上一张（←）</button>
    <button id="next">下一张（→）</button>
    <button id="clear">清除当前标注（R）</button>
    <span id="status">正在加载……</span>
  </div>
  <div id="message"></div>
  <div id="canvas-wrap"><canvas id="canvas"></canvas></div>
  <p class="help">在鱼尾截断点上单击。红点位置会立即保存；JSON 中保存的是原图像素坐标。</p>
</main>
<script>
const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const statusEl = document.getElementById('status');
const messageEl = document.getElementById('message');
let state = null;
let image = new Image();

function message(text, error=false) {
  messageEl.textContent = text;
  messageEl.style.color = error ? '#ff7b72' : '#7ee787';
}
function draw() {
  if (!state || !image.complete || !image.naturalWidth) return;
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  ctx.drawImage(image, 0, 0);
  if (state.point) {
    const radius = Math.max(2, Math.min(canvas.width, canvas.height) * 0.018);
    ctx.beginPath(); ctx.arc(state.point.x, state.point.y, radius + 1.5, 0, Math.PI * 2);
    ctx.fillStyle = '#000'; ctx.fill();
    ctx.beginPath(); ctx.arc(state.point.x, state.point.y, radius, 0, Math.PI * 2);
    ctx.fillStyle = '#ff2020'; ctx.fill();
  }
}
async function load(index) {
  try {
    if (state) index = Math.max(0, Math.min(index, state.total - 1));
    const response = await fetch('/api/state?index=' + index, {cache: 'no-store'});
    if (!response.ok) throw new Error(await response.text());
    state = await response.json();
    statusEl.textContent = `[${state.index + 1}/${state.total}] ${state.filename}　` +
      (state.point ? `已标注 (${state.point.x}, ${state.point.y})` : '未标注') +
      `　总进度 ${state.annotated_count}/${state.total}`;
    document.getElementById('prev').disabled = state.index === 0;
    document.getElementById('next').disabled = state.index === state.total - 1;
    image = new Image();
    image.onload = draw;
    image.onerror = () => message('图片加载失败', true);
    image.src = '/image?index=' + state.index + '&v=' + Date.now();
    message('');
  } catch (error) { message(String(error), true); }
}
async function update(action, x=null, y=null) {
  if (!state) return;
  try {
    const response = await fetch('/api/annotation', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action, index: state.index, x, y})
    });
    if (!response.ok) throw new Error(await response.text());
    state = await response.json();
    draw();
    statusEl.textContent = `[${state.index + 1}/${state.total}] ${state.filename}　` +
      (state.point ? `已标注 (${state.point.x}, ${state.point.y})` : '未标注') +
      `　总进度 ${state.annotated_count}/${state.total}`;
    message(action === 'clear' ? '已清除并保存' : `已保存坐标 (${x}, ${y})`);
  } catch (error) { message(String(error), true); }
}
canvas.addEventListener('click', event => {
  if (!state) return;
  const rect = canvas.getBoundingClientRect();
  const x = Math.max(0, Math.min(canvas.width - 1,
    Math.round((event.clientX - rect.left) * canvas.width / rect.width)));
  const y = Math.max(0, Math.min(canvas.height - 1,
    Math.round((event.clientY - rect.top) * canvas.height / rect.height)));
  update('set', x, y);
});
document.getElementById('prev').onclick = () => state && load(state.index - 1);
document.getElementById('next').onclick = () => state && load(state.index + 1);
document.getElementById('clear').onclick = () => update('clear');
document.addEventListener('keydown', event => {
  if (!state) return;
  if (event.key === 'ArrowLeft') { event.preventDefault(); load(state.index - 1); }
  if (event.key === 'ArrowRight' || event.key === 'Enter' || event.key === ' ') {
    event.preventDefault(); load(state.index + 1);
  }
  if (event.key.toLowerCase() === 'r') update('clear');
});
load(__START_INDEX__);
</script></body></html>
"""


def _resolve_path(path: str | Path) -> Path:
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = PROJECT_ROOT / resolved
    return resolved.resolve()


def discover_crop_images(input_dir: Path) -> list[Path]:
    """按 ``track_id, frame_index, filename`` 排序发现 crop 图片。"""

    images = []
    if not input_dir.is_dir():
        return images
    for path in input_dir.iterdir():
        if not path.is_file() or not path.name.endswith(FISH_CROP_SUFFIX):
            continue
        info = parse_fish_file_name(path.name)
        if info is None or info.kind != "crop":
            continue
        images.append((info.track_id, info.frame_index, path.name, path))
    images.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in images]


def _empty_payload(input_dir: Path) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "annotation_type": ANNOTATION_TYPE,
        "input_dir": to_project_relative(input_dir),
        "total_images": 0,
        "annotated_count": 0,
        "updated_at": None,
        "annotations": {},
    }


def load_annotations(json_path: Path, input_dir: Path) -> dict:
    """读取已有标注；文件不存在时返回空结构。"""

    if not json_path.is_file():
        return _empty_payload(input_dir)
    with json_path.open("r", encoding="utf-8") as json_file:
        payload = json.load(json_file)
    if not isinstance(payload, dict):
        raise ValueError(f"annotation JSON root must be an object: {json_path}")
    annotations = payload.get("annotations", {})
    if not isinstance(annotations, dict):
        raise ValueError(f"annotations must be an object: {json_path}")
    payload["annotations"] = annotations
    payload["schema_version"] = SCHEMA_VERSION
    payload["annotation_type"] = ANNOTATION_TYPE
    payload["input_dir"] = to_project_relative(input_dir)
    return payload


def save_annotations_atomic(
    json_path: Path,
    payload: dict,
    *,
    input_dir: Path,
    total_images: int,
) -> None:
    """原子保存 JSON，防止中途退出造成文件损坏。"""

    ensure_dir(json_path.parent)
    annotations = payload.setdefault("annotations", {})
    payload.update(
        {
            "schema_version": SCHEMA_VERSION,
            "annotation_type": ANNOTATION_TYPE,
            "input_dir": to_project_relative(input_dir),
            "total_images": int(total_images),
            "annotated_count": int(len(annotations)),
            "updated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        }
    )
    # 保持 annotations 对象本身不变。TailCutAnnotator 持有这个字典的引用；
    # 如果在这里直接替换为新字典，后续标注会写入旧对象，JSON 便只保留
    # 第一次保存时的内容。
    ordered_annotations = [
        (key, annotations[key])
        for key in sorted(annotations)
    ]
    annotations.clear()
    annotations.update(ordered_annotations)
    payload["annotations"] = annotations
    temp_path = json_path.with_suffix(json_path.suffix + ".tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as json_file:
            json.dump(payload, json_file, ensure_ascii=False, indent=2)
            json_file.write("\n")
        os.replace(temp_path, json_path)
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


class TailCutAnnotator:
    def __init__(
        self,
        *,
        input_dir: Path,
        output_json: Path,
        display_scale: float,
        max_window_width: int,
        max_window_height: int,
        start_index: int | None,
    ) -> None:
        self.input_dir = input_dir
        self.output_json = output_json
        self.display_scale = float(display_scale)
        self.max_window_width = int(max_window_width)
        self.max_window_height = int(max_window_height)
        self.images = discover_crop_images(input_dir)
        if not self.images:
            raise ValueError(f"未找到 {FISH_CROP_SUFFIX} 图片：{input_dir}")
        self.payload = load_annotations(output_json, input_dir)
        self.annotations: dict[str, dict] = self.payload["annotations"]
        self.index = self._initial_index(start_index)
        self.original_image: np.ndarray | None = None
        self.scale_x = 1.0
        self.scale_y = 1.0
        self.display_width = 0
        self.display_height = 0

    def _initial_index(self, start_index: int | None) -> int:
        if start_index is not None:
            return max(0, min(int(start_index), len(self.images) - 1))
        for index, path in enumerate(self.images):
            if path.name not in self.annotations:
                return index
        return 0
    @property
    def current_path(self) -> Path:
        return self.images[self.index]

    def _save(self) -> None:
        save_annotations_atomic(
            self.output_json,
            self.payload,
            input_dir=self.input_dir,
            total_images=len(self.images),
        )

    def _load_current_image(self) -> None:
        image = cv2.imread(str(self.current_path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"无法读取图片：{self.current_path}")
        self.original_image = image
        image_height, image_width = image.shape[:2]
        scale = min(
            self.display_scale,
            self.max_window_width / max(1, image_width),
            (self.max_window_height - HEADER_HEIGHT) / max(1, image_height),
        )
        scale = max(0.1, float(scale))
        self.display_width = max(1, int(round(image_width * scale)))
        self.display_height = max(1, int(round(image_height * scale)))
        self.scale_x = self.display_width / float(image_width)
        self.scale_y = self.display_height / float(image_height)

    def _current_annotation(self) -> dict | None:
        annotation = self.annotations.get(self.current_path.name)
        return annotation if isinstance(annotation, dict) else None

    def _set_point(self, x: int, y: int) -> None:
        if self.original_image is None:
            return
        image_height, image_width = self.original_image.shape[:2]
        x = max(0, min(int(x), image_width - 1))
        y = max(0, min(int(y), image_height - 1))
        info = parse_fish_file_name(self.current_path.name)
        self.annotations[self.current_path.name] = {
            "image_path": to_project_relative(self.current_path),
            "track_id": None if info is None else int(info.track_id),
            "frame_index": None if info is None else int(info.frame_index),
            "image_width": int(image_width),
            "image_height": int(image_height),
            "tail_cut_point": {"x": int(x), "y": int(y)},
            "updated_at": dt.datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
        }
        self._save()
        print(
            f"[已保存] {self.current_path.name}: "
            f"tail_cut_point=({x}, {y})"
        )

    def _clear_current(self) -> None:
        if self.annotations.pop(self.current_path.name, None) is not None:
            self._save()
            print(f"[已清除] {self.current_path.name}")

    def _mouse_callback(self, event, x, y, _flags, _parameter) -> None:
        if event not in (cv2.EVENT_LBUTTONDOWN, cv2.EVENT_RBUTTONDOWN):
            return
        if event == cv2.EVENT_RBUTTONDOWN:
            self._clear_current()
            return
        image_y = int(y) - HEADER_HEIGHT
        if x < 0 or x >= self.display_width or image_y < 0 or image_y >= self.display_height:
            return
        original_x = int(round(float(x) / self.scale_x))
        original_y = int(round(float(image_y) / self.scale_y))
        self._set_point(original_x, original_y)

    def _render(self) -> np.ndarray:
        if self.original_image is None:
            raise RuntimeError("current image has not been loaded")
        resized = cv2.resize(
            self.original_image,
            (self.display_width, self.display_height),
            interpolation=(
                cv2.INTER_CUBIC
                if self.scale_x >= 1.0 and self.scale_y >= 1.0
                else cv2.INTER_AREA
            ),
        )
        canvas = np.full(
            (HEADER_HEIGHT + self.display_height, self.display_width, 3),
            28,
            dtype=np.uint8,
        )
        canvas[HEADER_HEIGHT:, :] = resized
        annotated_count = len(self.annotations)
        status = "ANNOTATED" if self._current_annotation() else "UNANNOTATED"
        cv2.putText(
            canvas,
            (
                f"[{self.index + 1}/{len(self.images)}] {self.current_path.name}  "
                f"{status}  ({annotated_count}/{len(self.images)})"
            ),
            (12, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            canvas,
            "Left click: set/update    Right click/R: clear    Enter/Space/N: next",
            (12, 56),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.50,
            (210, 210, 210),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            canvas,
            "P/Left: previous    S: save    Q/Esc: save and quit",
            (12, 80),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.50,
            (210, 210, 210),
            1,
            cv2.LINE_AA,
        )

        annotation = self._current_annotation()
        if annotation:
            point = annotation.get("tail_cut_point", {})
            try:
                original_x = int(point["x"])
                original_y = int(point["y"])
            except (KeyError, TypeError, ValueError):
                return canvas
            display_x = int(round(original_x * self.scale_x))
            display_y = HEADER_HEIGHT + int(round(original_y * self.scale_y))
            radius = max(5, int(round(3 * min(self.scale_x, self.scale_y))))
            cv2.circle(
                canvas,
                (display_x, display_y),
                radius + 3,
                (0, 0, 0),
                -1,
                cv2.LINE_AA,
            )
            cv2.circle(
                canvas,
                (display_x, display_y),
                radius,
                (0, 0, 255),
                -1,
                cv2.LINE_AA,
            )
            cv2.putText(
                canvas,
                f"({original_x}, {original_y})",
                (min(display_x + radius + 5, max(0, self.display_width - 120)),
                 max(HEADER_HEIGHT + 18, display_y - radius - 5)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                (0, 0, 0),
                3,
                cv2.LINE_AA,
            )
            cv2.putText(
                canvas,
                f"({original_x}, {original_y})",
                (min(display_x + radius + 5, max(0, self.display_width - 120)),
                 max(HEADER_HEIGHT + 18, display_y - radius - 5)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
        return canvas

    def _move(self, offset: int) -> None:
        new_index = max(0, min(self.index + int(offset), len(self.images) - 1))
        if new_index == self.index:
            print("[提示] 已到图片列表边界。")
            return
        self.index = new_index
        self._load_current_image()
        cv2.resizeWindow(
            WINDOW_NAME,
            self.display_width,
            HEADER_HEIGHT + self.display_height,
        )

    def run(self) -> int:
        self._load_current_image()
        print(f"输入目录：{self.input_dir}")
        print(f"图片数量：{len(self.images)}")
        print(f"标注 JSON：{self.output_json}")
        print(f"已有标注：{len(self.annotations)}")
        print("鼠标左键点击尾部截断点；Q 或 Esc 保存退出。")
        try:
            cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(
                WINDOW_NAME,
                self.display_width,
                HEADER_HEIGHT + self.display_height,
            )
            cv2.setMouseCallback(WINDOW_NAME, self._mouse_callback)
        except cv2.error as exc:
            raise RuntimeError(
                "无法创建 OpenCV 标注窗口，请在有图形桌面的环境中运行。"
            ) from exc

        try:
            while True:
                cv2.imshow(WINDOW_NAME, self._render())
                key = cv2.waitKeyEx(30)
                if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                    break
                if key < 0:
                    continue
                low_key = key & 0xFF
                if low_key in (27, ord("q"), ord("Q")):
                    break
                if low_key in (13, 10, 32, ord("n"), ord("N")) or key in RIGHT_KEYS:
                    self._move(1)
                    continue
                if low_key in (ord("p"), ord("P")) or key in LEFT_KEYS:
                    self._move(-1)
                    continue
                if low_key in (ord("r"), ord("R")) or key in DELETE_KEYS:
                    self._clear_current()
                    continue
                if low_key in (ord("s"), ord("S")):
                    self._save()
                    print(f"[已保存] {self.output_json}")
        finally:
            self._save()
            cv2.destroyAllWindows()

        print(
            f"标注已保存：{self.output_json} "
            f"({len(self.annotations)}/{len(self.images)})"
        )
        return 0


def _web_state(annotator: TailCutAnnotator, index: int) -> dict:
    """返回浏览器需要的当前图片状态。"""

    annotator.index = max(0, min(int(index), len(annotator.images) - 1))
    annotator._load_current_image()
    annotation = annotator._current_annotation()
    point = None
    if annotation:
        raw_point = annotation.get("tail_cut_point")
        if isinstance(raw_point, dict) and "x" in raw_point and "y" in raw_point:
            point = {"x": int(raw_point["x"]), "y": int(raw_point["y"])}
    if annotator.original_image is None:
        raise RuntimeError("图片尚未加载")
    height, width = annotator.original_image.shape[:2]
    return {
        "index": annotator.index,
        "total": len(annotator.images),
        "filename": annotator.current_path.name,
        "image_width": int(width),
        "image_height": int(height),
        "point": point,
        "annotated_count": len(annotator.annotations),
    }


def run_web_server(
    annotator: TailCutAnnotator,
    *,
    host: str,
    port: int,
) -> int:
    """运行不依赖桌面环境的浏览器标注服务。"""

    lock = threading.RLock()
    page = WEB_PAGE.replace(
        "__START_INDEX__", str(annotator.index)
    ).encode("utf-8")

    class AnnotationHandler(BaseHTTPRequestHandler):
        server_version = "TailCutAnnotator/1.0"

        def log_message(self, format_string: str, *args) -> None:
            return

        def _send_bytes(
            self, status: int, content: bytes, content_type: str
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def _send_json(self, status: int, value: dict) -> None:
            content = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self._send_bytes(status, content, "application/json; charset=utf-8")

        def _send_error_json(self, status: int, message: str) -> None:
            self._send_json(status, {"error": message})

        @staticmethod
        def _query_index(query: str) -> int:
            values = parse_qs(query).get("index", [])
            if not values:
                raise ValueError("缺少 index 参数")
            index = int(values[0])
            if not 0 <= index < len(annotator.images):
                raise ValueError("index 超出图片范围")
            return index

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/":
                    self._send_bytes(200, page, "text/html; charset=utf-8")
                    return
                if parsed.path == "/api/state":
                    index = self._query_index(parsed.query)
                    with lock:
                        state = _web_state(annotator, index)
                    self._send_json(200, state)
                    return
                if parsed.path == "/image":
                    index = self._query_index(parsed.query)
                    image_path = annotator.images[index]
                    content_type = (
                        "image/png"
                        if image_path.suffix.lower() == ".png"
                        else "image/jpeg"
                    )
                    self._send_bytes(200, image_path.read_bytes(), content_type)
                    return
                self._send_error_json(404, "页面不存在")
            except (OSError, RuntimeError, ValueError) as exc:
                self._send_error_json(400, str(exc))

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/api/annotation":
                self._send_error_json(404, "接口不存在")
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length <= 0 or content_length > 65536:
                    raise ValueError("请求内容长度无效")
                request = json.loads(self.rfile.read(content_length))
                index = int(request["index"])
                if not 0 <= index < len(annotator.images):
                    raise ValueError("index 超出图片范围")
                with lock:
                    annotator.index = index
                    annotator._load_current_image()
                    if request.get("action") == "set":
                        annotator._set_point(
                            int(request["x"]), int(request["y"])
                        )
                    elif request.get("action") == "clear":
                        annotator._clear_current()
                    else:
                        raise ValueError("未知标注操作")
                    state = _web_state(annotator, index)
                self._send_json(200, state)
            except (
                KeyError,
                TypeError,
                OSError,
                RuntimeError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                self._send_error_json(400, str(exc))

    try:
        server = ThreadingHTTPServer((host, int(port)), AnnotationHandler)
    except OSError as exc:
        raise RuntimeError(f"无法启动标注服务 {host}:{port}：{exc}") from exc

    actual_port = int(server.server_address[1])
    browser_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print(f"输入目录：{annotator.input_dir}")
    print(f"图片数量：{len(annotator.images)}")
    print(f"已有标注：{len(annotator.annotations)}")
    print(f"标注 JSON：{annotator.output_json}")
    print(f"请在浏览器打开：http://{browser_host}:{actual_port}")
    print("按 Ctrl+C 停止服务；已经点击的标注会立即保存。")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\n正在停止标注服务……")
    finally:
        server.server_close()
        annotator._save()
    print(
        f"标注已保存：{annotator.output_json} "
        f"({len(annotator.annotations)}/{len(annotator.images)})"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="在浏览器中点击标注鱼尾截断点，并保存原图像素坐标 JSON。"
    )
    parser.add_argument(
        "--mode",
        choices=("web", "gui"),
        default="web",
        help="标注界面：web 不需要图形桌面，gui 使用 OpenCV（默认：web）",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="web 模式监听地址（默认：127.0.0.1）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="web 模式监听端口；设为 0 自动选择空闲端口（默认：8765）",
    )
    parser.add_argument(
        "--input-dir",
        default=str(DEFAULT_INPUT_DIR),
        help=f"crop 图片目录（默认：{DEFAULT_INPUT_DIR}）",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help=(
            "JSON 输出路径（默认："
            "<input-dir>/tail_cut_annotations.json）"
        ),
    )
    parser.add_argument(
        "--display-scale",
        type=float,
        default=4.0,
        help="图片显示倍率，原图坐标不受影响（默认：4.0）",
    )
    parser.add_argument(
        "--max-window-width",
        type=int,
        default=1400,
        help="窗口最大宽度（默认：1400）",
    )
    parser.add_argument(
        "--max-window-height",
        type=int,
        default=900,
        help="窗口最大高度（默认：900）",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=None,
        help="从指定的从 1 开始的图片序号进入；默认从首张未标注图片继续",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    input_dir = _resolve_path(args.input_dir)
    output_json = (
        _resolve_path(args.output_json)
        if args.output_json
        else input_dir / DEFAULT_JSON_NAME
    )
    if not input_dir.is_dir():
        print(f"[ERROR] 输入目录不存在：{input_dir}", file=sys.stderr)
        return 2
    if args.display_scale <= 0:
        print("[ERROR] --display-scale 必须大于 0。", file=sys.stderr)
        return 2
    if not 0 <= args.port <= 65535:
        print("[ERROR] --port 必须在 0 到 65535 之间。", file=sys.stderr)
        return 2
    if args.max_window_width <= 0 or args.max_window_height <= HEADER_HEIGHT:
        print("[ERROR] 窗口尺寸参数无效。", file=sys.stderr)
        return 2
    start_index = None
    if args.start_index is not None:
        if args.start_index <= 0:
            print("[ERROR] --start-index 必须是大于等于 1 的整数。", file=sys.stderr)
            return 2
        start_index = int(args.start_index) - 1
    try:
        annotator = TailCutAnnotator(
            input_dir=input_dir,
            output_json=output_json,
            display_scale=float(args.display_scale),
            max_window_width=int(args.max_window_width),
            max_window_height=int(args.max_window_height),
            start_index=start_index,
        )
        if args.mode == "gui":
            return annotator.run()
        return run_web_server(annotator, host=args.host, port=args.port)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())