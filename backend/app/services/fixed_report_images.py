"""大黄鱼形体指标测量示意图（图 6）固定资源。

源文件：backend/app/templates/大黄鱼形体指标测量示意图.tif
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.paths import PROJECT_ROOT, resource_path

logger = logging.getLogger("report_fixed_images")

FIXED_BODY_DEMO_TIF_REL = (
    Path("backend") / "app" / "templates" / "大黄鱼形体指标测量示意图.tif"
)


def get_fixed_body_demo_tif() -> Path:
    # The template image is bundled read-only by PyInstaller.  Prefer that
    # resource and retain the source-install path as a fallback.
    bundled = resource_path(*FIXED_BODY_DEMO_TIF_REL.parts)
    if bundled.exists():
        return bundled
    return PROJECT_ROOT / FIXED_BODY_DEMO_TIF_REL


def prepare_fixed_body_demo_image(
    output_dir: Path,
    *,
    prefer_rgb: bool = True,
) -> Path | None:
    """把 tif 固定示意图转换为 png，便于 python-docx / LibreOffice 稳定渲染。

    返回 PNG 路径。如果 Pillow 不可用且 tif 可直接被读取，仍尝试保存 PNG。
    任意失败回退到 tif。
    """
    src = get_fixed_body_demo_tif()
    if not src.exists():
        logger.warning("[fixed_images] 找不到源 tif: %s", src)
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    dst = output_dir / "large_yellow_croaker_body.png"

    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return dst

    try:
        from PIL import Image
    except Exception as exc:
        logger.warning("[fixed_images] Pillow 不可用，回退到 tif: %s", exc)
        return src

    try:
        with Image.open(str(src)) as img:
            if prefer_rgb and img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            img.save(str(dst))
        return dst
    except Exception as exc:
        logger.warning("[fixed_images] 转换失败，回退 tif: %s", exc)
        return src


__all__ = [
    "FIXED_BODY_DEMO_TIF_REL",
    "get_fixed_body_demo_tif",
    "prepare_fixed_body_demo_image",
]
