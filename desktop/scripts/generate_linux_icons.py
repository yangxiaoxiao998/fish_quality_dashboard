#!/usr/bin/env python3
"""Build transparent, standard-size Linux icons from the project fish logo."""

from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "frontend" / "src" / "assets" / "images" / "software_logo.png"
OUTPUT_DIR = ROOT / "desktop" / "build" / "icons"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)


def _is_background(pixel: tuple[int, int, int, int]) -> bool:
    red, green, blue, _alpha = pixel
    return min(red, green, blue) >= 242 and max(red, green, blue) - min(red, green, blue) <= 12


def _remove_edge_background(image: Image.Image) -> Image.Image:
    image = image.convert("RGBA")
    pixels = image.load()
    width, height = image.size
    queue: deque[tuple[int, int]] = deque()
    visited: set[tuple[int, int]] = set()

    for x in range(width):
        queue.append((x, 0))
        queue.append((x, height - 1))
    for y in range(height):
        queue.append((0, y))
        queue.append((width - 1, y))

    while queue:
        x, y = queue.popleft()
        if (x, y) in visited or not (0 <= x < width and 0 <= y < height):
            continue
        visited.add((x, y))
        pixel = pixels[x, y]
        if not _is_background(pixel):
            continue
        pixels[x, y] = (pixel[0], pixel[1], pixel[2], 0)
        queue.extend(((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))

    return image


def build_icons() -> None:
    image = _remove_edge_background(Image.open(SOURCE))
    # Keep the complete replacement logo.  The previous implementation
    # discarded the lower 14% to remove a watermark from an old asset, which
    # also clipped the microscope base in newer logos.
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        raise RuntimeError(f"No visible logo subject found in {SOURCE}")

    left, top, right, bottom = bbox
    subject = image.crop((left, top, right, bottom))
    side = max(subject.size)
    # GNOME/Ubuntu Dock applies its own icon mask.  Keep the source artwork
    # inside an 80% safe area so the panel does not visually crop the edges.
    padding = max(24, round(side * 0.125))
    canvas_side = side + padding * 2
    canvas = Image.new("RGBA", (canvas_side, canvas_side), (0, 0, 0, 0))
    canvas.alpha_composite(
        subject,
        ((canvas_side - subject.width) // 2, (canvas_side - subject.height) // 2),
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        icon = canvas.resize((size, size), Image.Resampling.LANCZOS)
        icon.save(OUTPUT_DIR / f"{size}x{size}.png", optimize=True)


if __name__ == "__main__":
    build_icons()
