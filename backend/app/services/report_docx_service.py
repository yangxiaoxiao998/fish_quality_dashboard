"""鱼品质日报 - 5 张图生成 + docx 渲染 + libreoffice 转换 PDF。

整体流程：
    1. build_daily_report_context()         - report_stats_service 出 context
    2. generate_charts()                     - matplotlib Agg 后端生成 5 张 png
    3. render_report_docx()                  - python-docx 替换 {{xxx}} 文本 + 插图
    4. convert_docx_to_pdf()                 - libreoffice --headless --convert-to pdf
    5. 写 detection_report.file_name / file_path
"""
from __future__ import annotations

import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import date, datetime, time as _dtime
from pathlib import Path

# 必须在 import pyplot 前
import matplotlib
matplotlib.use("Agg")  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.font_manager as fm  # noqa: E402

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_ROW_HEIGHT_RULE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches
from sqlalchemy.orm import Session

from app.paths import DATA_DIR, PROJECT_ROOT  # noqa: E402
from app.services.path_service import to_data_relative
from app.services.report_stats_service import (
    build_daily_report_context,
    ensure_all_placeholders,
)

logger = logging.getLogger("report_docx")

# matplotlib 中文字体兜底（按优先级）
# .ttc 内多语种字体（如 Noto Sans CJK.ttc）通常 matplotlib 只识别第一个；
# 实际上 JP/KR/SC/TC 字体均带 CJK 全字符集，能渲染中文。
_CJK_FONT_CANDIDATES = [
    "Noto Sans CJK SC",
    "Noto Sans CJK TC",
    "Noto Sans CJK KR",
    "Noto Sans CJK JP",
    "Noto Sans CJK",
    "Source Han Sans SC",
    "WenQuanYi Zen Hei",
    "WenQuanYi Micro Hei",
    "SimHei",
    "Microsoft YaHei",
    "Arial Unicode MS",
    "DejaVu Sans",
]


def _configure_cjk_font() -> str | None:
    """配置 matplotlib 中文字体，返回实际选中的字体名（找不到时 None）。"""
    available = {f.name for f in fm.fontManager.ttflist}
    for name in _CJK_FONT_CANDIDATES:
        if name in available:
            matplotlib.rcParams["font.sans-serif"] = [name] + _CJK_FONT_CANDIDATES
            matplotlib.rcParams["axes.unicode_minus"] = False
            return name
    matplotlib.rcParams["axes.unicode_minus"] = False
    return None


_CONFIGURED_CJK_FONT = _configure_cjk_font()
logger.info(f"[docx] matplotlib CJK font: {_CONFIGURED_CJK_FONT}")

CHART_KEYS = [
    "chart_body_length_distribution",
    "chart_body_length_without_tail_distribution",
    "chart_body_weight_distribution",
    "chart_condition_factor_distribution",
    "chart_surface_damage_distribution",
]


# 报表统计图在 DOCX / PDF 单元格中的最大尺寸。
# 这些值与 video_report_charts.py 中 CHART_FIGSIZE=(7.2, 4.0) 共同决定
# 统计图在 PDF 中的最终呈现效果：
#  - 宽度从 5.8 缩到 5.30，避免贴近表格边界
#  - 高度从 3.2 缩到 2.90，避免撑爆单元格
#  - 安全边距 0.18 inch，确保图片四周留白
REPORT_CHART_MAX_WIDTH_INCH = 5.30
REPORT_CHART_MAX_HEIGHT_INCH = 2.90
REPORT_CHART_CELL_PADDING_INCH = 0.18


# ----------------- 模板路径 -----------------

def get_template_path() -> Path:
    """日报模板路径。优先 docs/data_docs/report_word_template.docx，兜底 detection_daily_report_template.docx。"""
    preferred = Path(__file__).resolve().parents[3] / "docs" / "data_docs" / "report_word_template.docx"
    if preferred.exists():
        return preferred
    p = DATA_DIR / "reports" / "templates" / "detection_daily_report_template.docx"
    if p.exists():
        return p
    alt = Path(__file__).resolve().parents[3] / "data" / "reports" / "templates" / "detection_daily_report_template.docx"
    if alt.exists():
        return alt
    return preferred


# ----------------- 图片尺寸工具 -----------------

def calc_fit_image_size(
    image_path: Path,
    max_width_inch: float,
    max_height_inch: float,
) -> tuple[float, float]:
    """根据图片原始像素比例，把图片限制在 max_width_inch x max_height_inch 内。

    返回 (width_inch, height_inch)。保持原图比例，不撑破上下限。
    """
    try:
        from PIL import Image
    except Exception:
        return (max_width_inch, max_height_inch)

    try:
        with Image.open(str(image_path)) as img:
            w_px, h_px = img.size
    except Exception:
        return (max_width_inch, max_height_inch)

    if not w_px or not h_px or max_width_inch <= 0 or max_height_inch <= 0:
        return (max_width_inch, max_height_inch)

    image_ratio = w_px / h_px
    box_ratio = max_width_inch / max_height_inch

    if image_ratio >= box_ratio:
        width = max_width_inch
        height = max_width_inch / image_ratio
    else:
        height = max_height_inch
        width = max_height_inch * image_ratio

    # 双层兜底：不让结果超过上下限，也不让它小到看不见
    width = max(0.4, min(width, max_width_inch))
    height = max(0.3, min(height, max_height_inch))
    return (round(width, 3), round(height, 3))


def resolve_report_image_path(value) -> Path | None:
    """把多种形态的图片路径解析成本地文件 Path。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.startswith("/static/"):
        return PROJECT_ROOT / "data" / s[len("/static/"):]
    if s.startswith("static/"):
        return PROJECT_ROOT / "data" / s[len("static/"):]
    if s.startswith("data/"):
        return PROJECT_ROOT / s
    if s.startswith("/data/"):
        return PROJECT_ROOT / s.lstrip("/")
    if s.startswith("results/"):
        return PROJECT_ROOT / "data" / s
    if s.startswith("/results/"):
        return PROJECT_ROOT / "data" / s.lstrip("/")
    p = Path(s)
    if p.is_absolute():
        return p
    return PROJECT_ROOT / s


def _safe_cell_size_inch(cell, attr: str) -> float | None:
    """安全读 cell.width / cell.height（EMU），返回 inch。失败返回 None。"""
    try:
        v = getattr(cell, attr)
        if v is None:
            return None
        # docx.shared.Length 类型
        if hasattr(v, "inches"):
            return float(v.inches)
        return float(v) / 914400.0
    except Exception:
        return None


def put_image_in_cell(
    cell,
    image_path,
    *,
    max_width_inch: float = 2.4,
    max_height_inch: float = 1.8,
    missing_text: str = "暂无图片",
) -> None:
    """清空 cell 后插入图片，自动按 cell 宽高比例缩放，水平左对齐 + 垂直居中。"""
    try:
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
    except Exception:
        WD_ALIGN_PARAGRAPH = None  # type: ignore
        WD_CELL_VERTICAL_ALIGNMENT = None  # type: ignore

    # 1) 清空 cell 内所有段落内容（保留至少 1 个 paragraph）
    paras = list(cell.paragraphs)
    if not paras:
        cell.add_paragraph()
        paras = list(cell.paragraphs)
    # 清掉第一段以外的所有段
    for extra in paras[1:]:
        try:
            extra._element.getparent().remove(extra._element)
        except Exception:
            pass
    p = cell.paragraphs[0]
    # 清空该段的 run
    for r in list(p.runs):
        try:
            r.text = ""
        except Exception:
            pass

    # 2) 垂直居中
    if WD_CELL_VERTICAL_ALIGNMENT is not None:
        try:
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        except Exception:
            pass

    # 3) 水平居中
    if WD_ALIGN_PARAGRAPH is not None:
        try:
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        except Exception:
            pass

    # 4) 解析路径
    resolved: Path | None = None
    if isinstance(image_path, Path):
        resolved = image_path
    else:
        resolved = resolve_report_image_path(image_path)

    if not resolved or not resolved.exists():
        p.add_run(missing_text)
        return

    # 5) 按 cell 内可用区域算缩放
    cw = _safe_cell_size_inch(cell, "width")
    if cw is not None and cw > 0:
        # 留 0.05in 内边距
        usable_w = max(0.3, cw - 0.1)
    else:
        usable_w = max_width_inch

    # 用段高限制图片，但 cell 高度不可靠，用 row 高度兜底
    ch = _safe_cell_size_inch(cell, "height")
    usable_h = max_height_inch
    if ch is not None and ch > 0:
        usable_h = max(0.3, ch - 0.1)
    else:
        try:
            rh = getattr(cell._parent, "height", None)
            if rh is not None and hasattr(rh, "inches") and rh.inches > 0:
                usable_h = max(0.3, rh.inches - 0.1)
        except Exception:
            pass

    try:
        w_in, h_in = calc_fit_image_size(resolved, usable_w, usable_h)
    except Exception:
        w_in, h_in = (min(usable_w, max_width_inch), min(usable_h, max_height_inch))

    try:
        run = p.add_run()
        run.add_picture(str(resolved), width=Inches(w_in), height=Inches(h_in))
    except Exception as exc:
        try:
            p.add_run(f"图片加载失败：{type(exc).__name__}")
        except Exception:
            pass


# ----------------- 图片占位符映射（未来模板扩展） -----------------

# key 是模板中可能出现的图片占位符
# 未来若模板加上 {{source_image}} / {{corrected_image}} / {{crop_image}} / {{fish_image}} 等，
# 在调用方构造 { 占位符 -> 本地路径 } 的字典传入 render_report_docx 即可生效。
IMAGE_PLACEHOLDER_DEFAULTS: dict[str, dict] = {
    "source_image": {"max_width_inch": 2.6, "max_height_inch": 1.8},
    "corrected_image": {"max_width_inch": 2.6, "max_height_inch": 1.8},
    "grounded_image": {"max_width_inch": 2.8, "max_height_inch": 2.0},
    "crop_image": {"max_width_inch": 1.2, "max_height_inch": 0.8},
    "fish_image": {"max_width_inch": 1.2, "max_height_inch": 0.8},
    "fish_thumb": {"max_width_inch": 1.2, "max_height_inch": 0.8},
    "report_chart": {
        "max_width_inch": REPORT_CHART_MAX_WIDTH_INCH,
        "max_height_inch": REPORT_CHART_MAX_HEIGHT_INCH,
    },
}


# ----------------- 图表生成 -----------------

# 旧条数图逻辑（_draw_bar_chart / generate_charts / _bucket_to_xy）已全部删除。
# 所有报表图 1-图 5 统一由 app.services.video_report_charts.generate_charts 生成。
# 保留 render_report_docx / put_image_in_cell / calc_fit_image_size 等图片工具。


# ----------------- 模板 placeholder 校验 -----------------

_PLACEHOLDER_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


def _iter_all_paragraphs(container) -> list:
    """递归 yield container.paragraphs + 所有嵌套 cell.paragraphs。"""
    result: list = []
    seen_paragraphs: set = set()

    def add_paragraph(p):
        if id(p) in seen_paragraphs:
            return
        seen_paragraphs.add(id(p))
        result.append(p)

    def walk_tables(tables):
        for t in tables:
            for row in t.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        add_paragraph(p)
                    walk_tables(cell.tables)

    for p in container.paragraphs:
        add_paragraph(p)
    walk_tables(container.tables)
    return result


def _strip_chart_caption_paragraphs(doc) -> int:
    """删除 docx 中 "图 N：xxx" 标题段落。

    模板里图片前后有形如 "图 1：鱼体长度分布图" 的标题段；
    这些文字挤占图片空间。这里遍历所有段落（含表格内 cell），
    匹配以 "图 N：xxx" 开头的整段（多 run 拼接判断），
    以及 "大黄鱼形体指标测量示意图" 之类的固定 caption，删除它们。
    """
    import re as _re
    # 严格匹配："图 N：xxx" 或 "图 N:xxx"，整段文本就是 caption
    # 既要包含数字编号（图 1 ~ 图 9），也兼容「图 1：体长（无尾鳍）」这种无尾鳍
    pat = _re.compile(r"^\s*图\s*\d+\s*[：:]\s*\S+\s*$")
    pat2 = _re.compile(r"^\s*大黄鱼形体指标测量示意图\s*$")
    removed = 0
    candidates = []
    for p in list(doc.paragraphs):
        try:
            text = "".join(r.text or "" for r in p.runs)
        except Exception:
            continue
        if not text:
            continue
        if pat.match(text) or pat2.match(text):
            candidates.append(p)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in list(cell.paragraphs):
                    try:
                        text = "".join(r.text or "" for r in p.runs)
                    except Exception:
                        continue
                    if not text:
                        continue
                    if pat.match(text) or pat2.match(text):
                        candidates.append(p)
    # 页眉 / 页脚 / 嵌套 cell 内的 caption 也都清理
    try:
        for section in doc.sections:
            for container in (section.header, section.footer):
                for p in list(container.paragraphs):
                    text = "".join(r.text or "" for r in p.runs).strip()
                    if pat.match(text) or pat2.match(text):
                        candidates.append(p)
                for table in container.tables:
                    for row in table.rows:
                        for cell in row.cells:
                            for p in list(cell.paragraphs):
                                text = "".join(r.text or "" for r in p.runs).strip()
                                if pat.match(text) or pat2.match(text):
                                    candidates.append(p)
    except Exception:
        pass

    for p in candidates:
        try:
            p._element.getparent().remove(p._element)
            removed += 1
        except Exception:
            pass
    return removed


    return result


def check_template_placeholders(template_path: Path) -> list[str]:
    """返回模板里出现的所有 {{xxx}} 占位符（去重），递归处理嵌套表。"""
    doc = Document(str(template_path))
    seen: set[str] = set()
    for p in _iter_all_paragraphs(doc):
        for m in _PLACEHOLDER_RE.findall(p.text):
            seen.add(m)
    return sorted(seen)


# ----------------- docx 渲染 -----------------

def render_report_docx(
    *,
    template_path: Path,
    context: dict,
    chart_paths: dict[str, Path],
    out_path: Path,
) -> None:
    """复制模板 → 替换 {{xxx}} → 替换 {{chart_xxx}} 为图片 → 保存。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(template_path), str(out_path))
    doc = Document(str(out_path))

    # 文字替换（递归所有段落）
    def replace_text_in_paragraph(paragraph) -> None:
        for run in paragraph.runs:
            for k, v in context.items():
                token = "{{" + k + "}}"
                if token in run.text:
                    run.text = run.text.replace(token, str(v))
        # 跨 run 残留处理：把整段 join 替换后再写回第一个 run
        full_text = "".join(r.text for r in paragraph.runs)
        for k, v in context.items():
            token = "{{" + k + "}}"
            if token in full_text:
                full_text = full_text.replace(token, str(v))
        if paragraph.runs:
            for r in paragraph.runs[1:]:
                r.text = ""
            paragraph.runs[0].text = full_text
        elif full_text:
            # 段落没有任何 run 但有内容（少见）— 加一个 run
            paragraph.add_run(full_text)

    # 1) 文字替换（用 ensure_all_placeholders 兜底过的 ctx）
    for p in _iter_all_paragraphs(doc):
        replace_text_in_paragraph(p)

    # 1.5) 删除模板中 "图 N：xxx" 标题段落（caption），避免挤占图片空间
    try:
        removed = _strip_chart_caption_paragraphs(doc)
        logger.info(f"[docx] 已删除 caption 段落 {removed} 个")
    except Exception as exc:
        logger.warning(f"[docx] 删除 caption 段落失败: {exc}")

    # 2) 把残留的 {{chart_xxx}} 段落清空后插图
    for key, chart_path in chart_paths.items():
        token = "{{" + key + "}}"
        replaced_count = _replace_chart_placeholder(doc, token, chart_path)
        if replaced_count == 0:
            logger.warning(f"[docx] 未找到图片占位符 {token}")

    # 3) 保存前再清理一次图注（chart 占位符 split 时可能产生新的 '图 N：xxx' 段落）
    try:
        removed2 = _strip_chart_caption_paragraphs(doc)
        if removed2:
            logger.info(f"[docx] 保存前再次删除 caption 段落 {removed2} 个")
    except Exception as exc:
        logger.warning(f"[docx] 保存前删除 caption 段落失败: {exc}")

    doc.save(str(out_path))


def _allow_chart_cell_row_to_expand(cell) -> None:
    """移除图表所在行的固定高度限制，允许行根据图片自动扩展。

    模板里部分表格行可能带有 ``w:trHeight`` 固定行高，导致 LibreOffice
    转 PDF 后只显示图片的一部分。这里只针对图表 cell 所在的行解除
    固定高度，不影响其他表格（如视频明细、异常鱼明细）。
    """
    try:
        from docx.oxml.ns import qn

        tr = cell._tc.getparent()
        if tr is None:
            return
        tr_pr = tr.get_or_add_trPr()
        for tr_height in list(tr_pr.findall(qn("w:trHeight"))):
            tr_pr.remove(tr_height)
    except Exception as exc:
        logger.debug(f"[docx] 无法移除图表行固定高度限制：{exc}")


def _replace_chart_placeholder(doc: Document, token: str, image_path: Path) -> int:
    """把所有包含 token 的段落 → 把 token 段拆出来塞图，按所在 cell 限制尺寸。

    模板里 token 通常与图标题在同一段：
        "图1：xxx\n{{chart_body_length_distribution}}"
    这里我们把段落拆成"前半段（标题）+ 图段（图片）+ 后半段"三段。
    表格内的 cell 优先走 put_image_in_cell，按 cell 宽高等比例缩放。
    """
    if not image_path or not Path(image_path).exists():
        logger.warning(f"[docx] 图片不存在，无法替换占位符 {token}: {image_path}")
        return 0

    count = 0

    def _cell_size_inch(paragraph) -> tuple[float | None, float | None]:
        """从段落推断所在 cell 的可用宽高（inch）。失败返回 (None, None)。"""
        try:
            cell = paragraph.part.element  # type: ignore[attr-defined]
        except Exception:
            cell = None
        try:
            # python-docx Paragraph 暴露 _parent 通常是 Cell
            parent = getattr(paragraph, "_parent", None)
            from docx.table import _Cell as _DocxCell  # type: ignore
            if isinstance(parent, _DocxCell):
                cw = _safe_cell_size_inch(parent, "width")
                ch = _safe_cell_size_inch(parent, "height")
                return (cw, ch)
        except Exception:
            pass
        return (None, None)

    def process_paragraph(p) -> bool:
        if token not in p.text:
            return False
        # 保留前半段文字
        before, sep, after = p.text.partition(token)
        # 清空所有 run
        for r in list(p.runs):
            r.text = ""
        # 把前半段写回第一个 run
        if p.runs:
            p.runs[0].text = before
        else:
            p.add_run(before)

        # 判断段落是否在 cell 里
        from docx.oxml.ns import qn
        from copy import deepcopy
        p_el = p._p
        parent = getattr(p, "_parent", None)
        from docx.table import _Cell as _DocxCell  # type: ignore
        in_cell = isinstance(parent, _DocxCell)

        if in_cell:
            # 把 token 文本从 cell 中清掉，再在 cell 内专门插一个段落放图
            # 现在的 p（前半段）已经只剩 "before"，cell.vertical_alignment 不变，
            # 我们直接 append 一个新的 paragraph 到 cell 末尾，水平居中插图。
            try:
                cell = parent
                # 解除模板中可能存在的 w:trHeight 固定行高，避免图片被裁切
                _allow_chart_cell_row_to_expand(cell)
                pic_p = cell.add_paragraph()
                # 图表段落水平居中，并清理多余段间距
                try:
                    pic_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    pic_p.paragraph_format.space_before = 0
                    pic_p.paragraph_format.space_after = 0
                except Exception:
                    pass

                cw, ch = _cell_size_inch(p)
                # 默认上限使用 REPORT_CHART_* 常量（已下调）
                max_w = REPORT_CHART_MAX_WIDTH_INCH
                max_h = REPORT_CHART_MAX_HEIGHT_INCH
                if cw is not None and cw > 0:
                    max_w = min(
                        REPORT_CHART_MAX_WIDTH_INCH,
                        max(0.4, cw - REPORT_CHART_CELL_PADDING_INCH),
                    )
                if ch is not None and ch > 0:
                    max_h = min(
                        REPORT_CHART_MAX_HEIGHT_INCH,
                        max(0.3, ch - REPORT_CHART_CELL_PADDING_INCH),
                    )

                w_in, h_in = calc_fit_image_size(Path(image_path), max_w, max_h)
                try:
                    pic_p.add_run().add_picture(
                        str(image_path),
                        width=Inches(w_in),
                        height=Inches(h_in),
                    )
                except Exception as exc:
                    pic_p.add_run(f"图片加载失败：{type(exc).__name__}")
            except Exception as exc:
                logger.warning(f"[docx] cell 插图失败回退段落插图: {exc}")
                pic_p = deepcopy(p_el)
                for r_el in pic_p.findall(qn("w:r")):
                    pic_p.remove(r_el)
                from docx.text.paragraph import Paragraph
                pic_para = Paragraph(pic_p, p._parent)
                fallback_w, fallback_h = calc_fit_image_size(
                    Path(image_path),
                    REPORT_CHART_MAX_WIDTH_INCH,
                    REPORT_CHART_MAX_HEIGHT_INCH,
                )
                pic_para.add_run().add_picture(
                    str(image_path),
                    width=Inches(fallback_w),
                    height=Inches(fallback_h),
                )
                p_el.addnext(pic_p)
        else:
            # 顶层段落：使用 REPORT_CHART_* 常量
            pic_p = deepcopy(p_el)
            for r_el in pic_p.findall(qn("w:r")):
                pic_p.remove(r_el)
            from docx.text.paragraph import Paragraph
            pic_para = Paragraph(pic_p, p._parent)
            try:
                pic_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                pic_para.paragraph_format.space_before = 0
                pic_para.paragraph_format.space_after = 0
            except Exception:
                pass
            w_in, h_in = calc_fit_image_size(
                Path(image_path),
                REPORT_CHART_MAX_WIDTH_INCH,
                REPORT_CHART_MAX_HEIGHT_INCH,
            )
            pic_para.add_run().add_picture(
                str(image_path),
                width=Inches(w_in),
                height=Inches(h_in),
            )
            p_el.addnext(pic_p)

        # 新段落：后半段（标题尾巴如果在同一段）
        if after:
            tail_p = deepcopy(p_el)
            for r_el in tail_p.findall(qn("w:r")):
                tail_p.remove(r_el)
            from docx.text.paragraph import Paragraph
            tail_para = Paragraph(tail_p, p._parent)
            tail_para.add_run(after)
            try:
                p_el.addnext(tail_p)
            except Exception:
                pass
        return True

    # 顶层段落
    for p in list(doc.paragraphs):
        if process_paragraph(p):
            count += 1
    # 表格内段落
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                for p in list(cell.paragraphs):
                    if process_paragraph(p):
                        count += 1
    return count


def replace_image_placeholder_in_cell(
    cell,
    placeholder: str,
    image_path,
    *,
    max_width_inch: float | None = None,
    max_height_inch: float | None = None,
    missing_text: str = "暂无图片",
) -> bool:
    """占位符在 cell 文本里时调用该函数，按 cell 比例缩放插图。

    返回 True 表示匹配到了 placeholder。
    """
    if placeholder not in cell.text:
        return False

    cfg = IMAGE_PLACEHOLDER_DEFAULTS.get(placeholder.strip("{}"), {})
    max_w = max_width_inch or cfg.get("max_width_inch") or 2.4
    max_h = max_height_inch or cfg.get("max_height_inch") or 1.8

    # 先清掉 placeholder 文字（包括"图片缺失"之类）
    for p in cell.paragraphs:
        for r in p.runs:
            if placeholder in r.text:
                r.text = r.text.replace(placeholder, "")

    put_image_in_cell(
        cell,
        image_path,
        max_width_inch=max_w,
        max_height_inch=max_h,
        missing_text=missing_text,
    )
    return True


# ----------------- docx → PDF（libreoffice） -----------------

def convert_docx_to_pdf(
    *,
    docx_path: Path,
    out_dir: Path,
    timeout_seconds: int = 120,
) -> Path | None:
    """用 libreoffice headless 转换 docx → pdf。"""
    if not docx_path.exists():
        return None
    if not shutil.which("libreoffice"):
        logger.error("[docx] libreoffice 不可用，无法生成 PDF")
        return None

    out_dir.mkdir(parents=True, exist_ok=True)

    # 配 user profile 避免并发冲突
    user_profile = out_dir / f".lo_user_{int(time.time() * 1000)}"
    user_profile.mkdir(parents=True, exist_ok=True)

    cmd = [
        "libreoffice",
        f"-env:UserInstallation=file://{user_profile}",
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(out_dir),
        str(docx_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.error(f"[docx] libreoffice 转换超时 ({timeout_seconds}s)")
        shutil.rmtree(user_profile, ignore_errors=True)
        return None
    finally:
        shutil.rmtree(user_profile, ignore_errors=True)

    if result.returncode != 0:
        logger.error(
            f"[docx] libreoffice 失败 rc={result.returncode} stderr={result.stderr[:300]}"
        )
        return None

    pdf_path = docx_path.with_suffix(".pdf")
    if not pdf_path.exists():
        # 兜底找
        candidates = list(out_dir.glob("*.pdf"))
        if candidates:
            pdf_path = candidates[0]
    return pdf_path if pdf_path.exists() else None


# ----------------- 一站式：docx + pdf -----------------

def _daily_fish_dicts(
    db: Session,
    report_date: date,
    *,
    start_dt: datetime | None = None,
    end_dt: datetime | None = None,
) -> list[dict]:
    """按日期范围查当天 fish。

    日报统计口径为视频拍摄/录制时间 (VideoTask.created_at)：
    2026-07-06 拍摄 + 2026-07-07 完成算法的视频也归入 2026-07-06 日报。

    如果传入 start_dt/end_dt 则按此区间；否则按 report_date 当天。
    """
    from sqlalchemy import select, and_
    from app.repositories.models import Fish, VideoTask
    from app.utils.timezone import CHINA_TZ

    if start_dt is None or end_dt is None:
        start_dt = datetime.combine(report_date, _dtime.min).replace(tzinfo=CHINA_TZ)
        end_dt = datetime.combine(report_date, _dtime.max).replace(tzinfo=CHINA_TZ)

    vt_subq = (
        select(VideoTask.video_id)
        .where(VideoTask.created_at >= start_dt)
        .where(VideoTask.created_at <= end_dt)
        .subquery()
    )
    rows = list(
        db.execute(select(Fish).where(Fish.video_id.in_(vt_subq))).scalars().all()
    )

    out: list[dict] = []
    for f in rows:
        out.append(
            {
                "body_length_cm": getattr(f, "body_length_cm", None),
                "body_length_no_tail_cm": getattr(f, "body_length_without_tail", None),
                "pred_weight_g": getattr(f, "pred_weight_g", None),
                "condition_factor": getattr(f, "condition_factor", None),
                "surface_damage_ratio": getattr(f, "surface_damage_ratio", None),
                "status": getattr(f, "status", None),
                "video_id": getattr(f, "video_id", None),
            }
        )
    return out


def _daily_chart_paths_with_fish(
    fish_dicts: list[dict],
    charts_dir: Path,
) -> dict:
    """统一入口：传入鱼 dict 列表，生成 5 张百分比图 + 1 张固定示意图。"""
    from app.services.video_report_charts import generate_charts
    return generate_charts(fish_dicts, charts_dir)


def generate_daily_fish_quality_docx_and_pdf(
    db: Session,
    *,
    report_date: date,
    output_dir: Path,
    file_stem: str,
    existing_context: dict | None = None,
) -> dict:
    """一键生成：构建 context → 5 图（统一 video_report_charts 入口）→ docx → pdf。"""
    template_path = get_template_path()
    if not template_path.exists():
        raise FileNotFoundError(
            f"日报模板不存在：{template_path}（请放入 data/reports/templates/detection_daily_report_template.docx）"
        )

    placeholders_in_template = check_template_placeholders(template_path)

    # 1) context
    ctx = existing_context or build_daily_report_context(
        db, report_date=report_date
    )

    # 2) 5 图（+ 图 6） — 统一调用 video_report_charts.generate_charts
    charts_dir = DATA_DIR / "tmp" / "report_charts"
    fish_dicts = _daily_fish_dicts(db, report_date)
    chart_paths = _daily_chart_paths_with_fish(fish_dicts, charts_dir)

    # 3) docx
    output_dir.mkdir(parents=True, exist_ok=True)
    docx_path = output_dir / f"{file_stem}.docx"
    render_report_docx(
        template_path=template_path,
        context=ctx,
        chart_paths=chart_paths,
        out_path=docx_path,
    )

    # 4) docx → pdf
    pdf_path = convert_docx_to_pdf(
        docx_path=docx_path,
        out_dir=output_dir,
    )

    # 5) 校验残留
    leftover = check_placeholder_leftover(docx_path)
    if leftover:
        logger.warning(
            f"[docx] 残留占位符（应该不会出现）：{leftover[:20]}"
        )

    return {
        "context": ctx,
        "docx_path": docx_path,
        "pdf_path": pdf_path,
        "placeholders_in_template": placeholders_in_template,
        "leftover_placeholders": leftover,
    }


def check_placeholder_leftover(docx_path: Path) -> list[str]:
    """生成后的 docx 文本中是否还残留 {{xxx}}（递归所有嵌套表）。"""
    if not docx_path.exists():
        return []
    doc = Document(str(docx_path))
    found: set[str] = set()
    for p in _iter_all_paragraphs(doc):
        for m in _PLACEHOLDER_RE.findall(p.text):
            found.add("{{" + m + "}}")
    return sorted(found)


__all__ = [
    "IMAGE_PLACEHOLDER_DEFAULTS",
    "calc_fit_image_size",
    "check_placeholder_leftover",
    "check_template_placeholders",
    "convert_docx_to_pdf",
    "generate_daily_fish_quality_docx_and_pdf",
    "get_template_path",
    "put_image_in_cell",
    "render_report_docx",
    "replace_image_placeholder_in_cell",
    "resolve_report_image_path",
]                       
