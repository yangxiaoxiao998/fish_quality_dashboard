"""单视频鱼体检测报表服务。

主入口：
    generate_video_pdf_report(video_id) -> dict

行为：
    1. 读取 data/results/<video_id>/reports/pipeline_result.json（含 fish 列表、summary）。
    2. 加载 backend/app/templates/report_word_template.docx（兜底 docs/data_docs）。
    3. 生成 5 张统计图 PNG（matplotlib Agg）到 data/results/<video_id>/reports/charts/。
    4. 模板字符占位符按稳健方式替换（容忍 run 拆行 / 空格 / 中英文标点）。
    5. 模板图表占位符在表格 cell 中识别（{{chart_xxx}} + 各种被换行/空格拆分的形态），
       走 put_image_in_cell，按 cell 宽高 5.8×3.2 in 等比例缩放，不撑破表格。
    6. 不在 DOCX/PDF 里追加"鱼体检测明细"表格；鱼表是前端"四、鱼体检测明细"的专属。
    7. docx 输出：data/results/<video_id>/reports/<video_id>_report.docx
    8. libreoffice 转 PDF（不可用则跳过），写到同目录 <video_id>_report.pdf。
    9. 写回 pipeline_result.json：report_docx / report_pdf / report_docx_url / report_pdf_url / report_charts。
    10. 失败不抛：返回 {ok: False, error: ...}，让上层可降级。

不在本服务做的：
    - 调用真实算法。
    - 调用真实标定。
    - 跑任何 pipeline 主流程。
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
from datetime import datetime

from app.utils.timezone import format_china_datetime, now_china
from pathlib import Path
from statistics import mean
from typing import Any

from app.paths import DATA_DIR, PROJECT_ROOT
from app.services.report_docx_service import (
    calc_fit_image_size,
    put_image_in_cell,
    resolve_report_image_path,
)
from app.services.video_report_charts import (
    ALL_CHART_KEYS,
    CHART_KEYS,
    FIXED_BODY_DEMO_CHART_KEY,
    charts_rel_paths,
    generate_charts,
    generate_fixed_body_demo,
)
from app.services.fixed_report_images import prepare_fixed_body_demo_image

from docx.shared import Inches  # noqa: E402

logger = logging.getLogger("video_report_pdf")


# ---------------------------------------------------------------------------
# 模板路径
# ---------------------------------------------------------------------------


def get_video_report_template_path() -> Path:
    """单视频检测报告模板，优先 backend/app/templates，回退 docs/data_docs。"""
    candidates = [
        PROJECT_ROOT / "backend" / "app" / "templates" / "report_word_template.docx",
        PROJECT_ROOT / "docs" / "data_docs" / "report_word_template.docx",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        "未找到 report_word_template.docx，请检查 backend/app/templates 或 docs/data_docs"
    )


# ---------------------------------------------------------------------------
# 路径辅助
# ---------------------------------------------------------------------------


def _video_reports_dir(video_id: str) -> Path:
    d = DATA_DIR / "results" / video_id / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _charts_dir(video_id: str) -> Path:
    d = DATA_DIR / "results" / video_id / "reports" / "charts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _docx_path_for(video_id: str) -> Path:
    return _video_reports_dir(video_id) / f"{video_id}_report.docx"


def _pdf_path_for(video_id: str) -> Path:
    return _video_reports_dir(video_id) / f"{video_id}_report.pdf"


def _pipeline_result_path(video_id: str) -> Path:
    return _video_reports_dir(video_id) / "pipeline_result.json"


def _data_rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _static_url(path: Path) -> str:
    rel = _data_rel(path)
    if rel.startswith("data/"):
        rel = rel[len("data/"):]
    if rel.startswith("/"):
        rel = rel.lstrip("/")
    return "/static/" + rel


# ---------------------------------------------------------------------------
# 通用格式化
# ---------------------------------------------------------------------------


def _ensure_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except Exception:
        return None


def _fmt_int(v: Any) -> str:
    if v is None or v == "":
        return "0"
    try:
        return str(int(float(v)))
    except Exception:
        return "0"


def _fmt_float(v: Any, decimals: int = 2) -> str:
    f = _to_float(v)
    if f is None:
        return "--"
    return f"{f:.{decimals}f}"


def _fmt_percent(v: Any) -> str:
    f = _to_float(v)
    if f is None:
        return "--"
    return f"{f * 100:.2f}%"


def _to_float_list(fish: list[dict], key: str) -> list[float]:
    out: list[float] = []
    for f in fish:
        if not isinstance(f, dict):
            continue
        v = _to_float(f.get(key))
        if v is not None:
            out.append(v)
    return out


def _avg_or_none(values: list[float]) -> float | None:
    return mean(values) if values else None


def _min_or_none(values: list[float]) -> float | None:
    return min(values) if values else None


def _max_or_none(values: list[float]) -> float | None:
    return max(values) if values else None


# ---------------------------------------------------------------------------
# LibreOffice 转 PDF（隔离 user-profile，避免并发）
# ---------------------------------------------------------------------------


def _convert_docx_to_pdf(docx_path: Path, output_dir: Path) -> Path | None:
    exe = shutil.which("libreoffice") or shutil.which("soffice")
    if not exe:
        logger.info("[video_report] libreoffice/soffice 不可用，跳过 PDF 转换")
        return None
    if not docx_path.exists():
        return None
    output_dir.mkdir(parents=True, exist_ok=True)

    user_profile = output_dir / f".lo_user_{int(time.time() * 1000)}"
    user_profile.mkdir(parents=True, exist_ok=True)

    cmd = [
        exe,
        f"-env:UserInstallation=file://{user_profile}",
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(output_dir),
        str(docx_path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=240, check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("[video_report] libreoffice 转 PDF 超时")
        shutil.rmtree(user_profile, ignore_errors=True)
        return None
    finally:
        shutil.rmtree(user_profile, ignore_errors=True)

    if result.returncode != 0:
        logger.warning(
            f"[video_report] libreoffice 失败 rc={result.returncode} stderr={result.stderr[:200]}"
        )
        return None

    pdf_path = docx_path.with_suffix(".pdf")
    if pdf_path.exists():
        return pdf_path
    candidates = sorted(output_dir.glob("*.pdf"))
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# pipeline 读取与 summary
# ---------------------------------------------------------------------------


def _read_pipeline_result(video_id: str) -> dict[str, Any]:
    p = _pipeline_result_path(video_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"[video_report] pipeline_result.json 解析失败: {exc}")
    return {"video_id": video_id, "status": "", "fish": [], "summary": {}}


def _summary_get(payload: dict[str, Any]) -> dict[str, Any]:
    s = payload.get("summary")
    if not isinstance(s, dict):
        s = {}
    out = dict(s)
    for k in (
        "total_fish", "complete_fish", "incomplete_fish", "failed_fish",
        "damaged_fish", "avg_surface_damage_ratio", "max_surface_damage_ratio",
    ):
        if out.get(k) is None:
            v = payload.get(k)
            if v is not None:
                out[k] = v
    return out


# ---------------------------------------------------------------------------
# 文字 context（模板字段 → 系统数据）
# ---------------------------------------------------------------------------


def _context_for_template(
    video_id: str,
    payload: dict[str, Any],
    fish: list[dict],
    summary: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, str]:
    """构造模板要用的字符串上下文。

    extra 用于补充 average/min/max 之类可能模板用到的字段。
    """
    extra = extra or {}

    status = (payload.get("status") or "").strip()
    status_text = {
        "done": "已完成",
        "running": "进行中",
        "failed": "失败",
        "pending": "待处理",
    }.get(status.lower(), status or "已生成")

    complete = sum(
        1 for f in fish
        if isinstance(f, dict) and str(f.get("status") or "").lower() == "done"
    )

    base = {
        # === 基础文字占位符（模板顶部） ===
        "video_id": video_id,
        "report_no": extra.get("report_no") or _safe_report_no(video_id),
        "generated_at": format_china_datetime(),
        "report_date_cn": now_china().strftime("%Y 年 %m 月 %d 日"),
        "system_name": "鱼质检测分析系统",
        "report_type_name": "单视频鱼体检测报告",
        "report_status_name": status_text,
        "video_status": status_text,
        "report_title": f"鱼体检测视频报告 · {video_id}",
        "status": status_text,

        # === 鱼数 / 状态统计 ===
        "total_fish": _fmt_int(summary.get("total_fish") or len(fish)),
        "fish_count": _fmt_int(len(fish)),
        "total_count": _fmt_int(len(fish)),
        "complete_fish": _fmt_int(summary.get("complete_fish")),
        "incomplete_fish": _fmt_int(summary.get("incomplete_fish")),
        "failed_fish": _fmt_int(summary.get("failed_fish")),
        "damaged_fish": _fmt_int(summary.get("damaged_fish")),
        "complete_count": _fmt_int(summary.get("complete_fish")),
        "incomplete_count": _fmt_int(summary.get("incomplete_fish")),
        "failed_count": _fmt_int(summary.get("failed_fish")),
        "damaged_count": _fmt_int(summary.get("damaged_fish")),

        # === 破损率 ===
        "avg_surface_damage_ratio": _fmt_percent(summary.get("avg_surface_damage_ratio")),
        "max_surface_damage_ratio": _fmt_percent(summary.get("max_surface_damage_ratio")),
        "avg_damage_ratio": _fmt_percent(summary.get("avg_surface_damage_ratio")),
        "max_damage_ratio": _fmt_percent(summary.get("max_surface_damage_ratio")),

        # === 平均指标 ===
        "avg_body_length_cm": _fmt_float(extra.get("avg_body_length_cm")),
        "avg_body_length_without_tail_cm": _fmt_float(
            extra.get("avg_body_length_without_tail_cm")
        ),
        "avg_body_height_cm": _fmt_float(extra.get("avg_body_height_cm")),
        "avg_body_weight_g": _fmt_float(extra.get("avg_body_weight_g")),
        "avg_condition_factor": _fmt_float(extra.get("avg_condition_factor"), 3),

        # === min/max 指标 ===
        "min_body_length_cm": _fmt_float(extra.get("min_body_length_cm")),
        "max_body_length_cm": _fmt_float(extra.get("max_body_length_cm")),
        "min_body_length_without_tail_cm": _fmt_float(
            extra.get("min_body_length_without_tail_cm")
        ),
        "max_body_length_without_tail_cm": _fmt_float(
            extra.get("max_body_length_without_tail_cm")
        ),
        "min_body_height_cm": _fmt_float(extra.get("min_body_height_cm")),
        "max_body_height_cm": _fmt_float(extra.get("max_body_height_cm")),
        "min_body_weight_g": _fmt_float(extra.get("min_body_weight_g")),
        "max_body_weight_g": _fmt_float(extra.get("max_body_weight_g")),

        # === 结尾结论 / 摘要 ===
        "daily_conclusion": extra.get("daily_conclusion") or _build_conclusion(
            video_id, status, summary, fish,
        ),

        # === 单视频场景下覆盖的"日报级"字段（避免占位符残留） ===
        # 所有多视频汇总的占位符在单视频场景无意义，但模板里都有。
        # 这里统一兜底为 -- 或 0，让占位符被替换干净。
        "upload_video_total": "0",
        "rtsp_video_total": "0",
        "no_fish_video_total": "0",
        "done_video_total": _fmt_int(1 if status.lower() == "done" else 0),
        "failed_video_total": _fmt_int(1 if status.lower() == "failed" else 0),
        "total_video_duration": "--",
        "task_success_rate": _fmt_float(summary.get("total_fish") and complete / len(fish) if fish else None, 2),
        "metric_complete_rate": "--",

        # 同义 avg / max / min（不带后缀 cm）—— 部分模板字段
        "avg_body_length": _fmt_float(extra.get("avg_body_length_cm")),
        "avg_body_length_without_tail": _fmt_float(extra.get("avg_body_length_without_tail_cm")),
        "avg_body_height": _fmt_float(extra.get("avg_body_height_cm")),
        "avg_body_weight": _fmt_float(extra.get("avg_body_weight_g")),
        "max_body_length": _fmt_float(extra.get("max_body_length_cm")),
        "max_body_length_without_tail": _fmt_float(extra.get("max_body_length_without_tail_cm")),
        "max_body_height": _fmt_float(extra.get("max_body_height_cm")),
        "max_body_weight": _fmt_float(extra.get("max_body_weight_g")),
        "min_body_length": _fmt_float(extra.get("min_body_length_cm")),
        "min_body_length_without_tail": _fmt_float(extra.get("min_body_length_without_tail_cm")),
        "min_body_height": _fmt_float(extra.get("min_body_height_cm")),
        "min_body_weight": _fmt_float(extra.get("min_body_weight_g")),
        "min_condition_factor": _fmt_float(extra.get("min_condition_factor"), 3),
        "max_condition_factor": _fmt_float(extra.get("max_condition_factor"), 3),

        # 业务字段
        "valid_fish_total": _fmt_int(summary.get("complete_fish")),
        "partial_fish_total": _fmt_int(summary.get("incomplete_fish")),
        "fish_total": _fmt_int(len(fish)),
        "surface_damage_fish_total": _fmt_int(summary.get("damaged_fish")),
        "surface_damage_rate": _fmt_percent(summary.get("avg_surface_damage_ratio")),
        "abnormal_fish_total": _fmt_int(summary.get("incomplete_fish")),
        "abnormal_fish_rate": _fmt_float(
            (summary.get("incomplete_fish") or 0) / len(fish) if fish else None,
            2,
        ),
        "avg_fps": "--",
        "avg_processing_seconds": "--",
        "local_save_status": "--",
        "usb_backup_status": "--",
        "report_file_name": f"{video_id}_report.docx",
        "report_file_path": _data_rel(DATA_DIR / "results" / video_id / "reports" / f"{video_id}_report.docx"),

        # 列表型占位符（top 5 异常鱼等），单视频下都用 -- 兜底
        "abnormal_index_1": "--",
        "abnormal_index_2": "--",
        "abnormal_index_3": "--",
        "abnormal_index_4": "--",
        "abnormal_video_id_1": "--",
        "abnormal_video_id_2": "--",
        "abnormal_video_id_3": "--",
        "abnormal_video_id_4": "--",
        "abnormal_fish_no_1": "--",
        "abnormal_fish_no_2": "--",
        "abnormal_fish_no_3": "--",
        "abnormal_fish_no_4": "--",
        "abnormal_reason_1": "--",
        "abnormal_reason_2": "--",
        "abnormal_reason_3": "--",
        "abnormal_reason_4": "--",
        "abnormal_body_length_1": "--",
        "abnormal_body_length_2": "--",
        "abnormal_body_length_3": "--",
        "abnormal_body_length_4": "--",
        "abnormal_body_length_without_tail_1": "--",
        "abnormal_body_length_without_tail_2": "--",
        "abnormal_body_length_without_tail_3": "--",
        "abnormal_body_length_without_tail_4": "--",
        "abnormal_body_height_1": "--",
        "abnormal_body_height_2": "--",
        "abnormal_body_height_3": "--",
        "abnormal_body_height_4": "--",
        "abnormal_body_weight_1": "--",
        "abnormal_body_weight_2": "--",
        "abnormal_body_weight_3": "--",
        "abnormal_body_weight_4": "--",
        "abnormal_condition_factor_1": "--",
        "abnormal_condition_factor_2": "--",
        "abnormal_condition_factor_3": "--",
        "abnormal_condition_factor_4": "--",
        "abnormal_surface_damage_1": "--",
        "abnormal_surface_damage_2": "--",
        "abnormal_surface_damage_3": "--",
        "abnormal_surface_damage_4": "--",

        # 视频列表（按当前 video 单值）
        "video_total": _fmt_int(1),
        "video_index_1": "1",
        "video_index_2": "--",
        "video_index_3": "--",
        "video_index_4": "--",
        "video_index_5": "--",
        "video_id_1": video_id,
        "video_id_2": "--",
        "video_id_3": "--",
        "video_id_4": "--",
        "video_id_5": "--",
        "video_status_1": status_text,
        "video_status_2": "--",
        "video_status_3": "--",
        "video_status_4": "--",
        "video_status_5": "--",
        "video_total_fish_1": _fmt_int(len(fish)),
        "video_total_fish_2": "--",
        "video_total_fish_3": "--",
        "video_total_fish_4": "--",
        "video_total_fish_5": "--",
        "video_processed_fish_1": _fmt_int(summary.get("complete_fish")),
        "video_processed_fish_2": "--",
        "video_processed_fish_3": "--",
        "video_processed_fish_4": "--",
        "video_processed_fish_5": "--",
        "video_source_type_1": "--",
        "video_source_type_2": "--",
        "video_source_type_3": "--",
        "video_source_type_4": "--",
        "video_source_type_5": "--",
        "video_created_at_1": "--",
        "video_created_at_2": "--",
        "video_created_at_3": "--",
        "video_created_at_4": "--",
        "video_created_at_5": "--",
        "video_finished_at_1": "--",
        "video_finished_at_2": "--",
        "video_finished_at_3": "--",
        "video_finished_at_4": "--",
        "video_finished_at_5": "--",
    }
    return base


def _safe_report_no(video_id: str) -> str:
    s = video_id.replace(" ", "")
    cleaned = "".join(ch for ch in s if ch.isalnum() or ch in "-_")[-14:]
    return (cleaned or "VID").upper()


def _build_conclusion(
    video_id: str,
    status: str,
    summary: dict[str, Any],
    fish: list[dict],
) -> str:
    """daily_conclusion 兜底文本。"""
    total = len(fish)
    if total == 0:
        return f"视频 {video_id} 当前没有可分析的鱼体数据。"
    complete = sum(
        1 for f in fish
        if isinstance(f, dict) and str(f.get("status") or "").lower() == "done"
    )
    rate = complete / total if total else 0.0
    avg_ratio = summary.get("avg_surface_damage_ratio")
    avg_pct = f"{float(avg_ratio) * 100:.2f}%" if avg_ratio not in (None, "") else "--"
    return (
        f"视频 {video_id} 共检测到 {total} 条鱼，"
        f"其中完整鱼 {complete} 条，完整率 {rate * 100:.2f}%；"
        f"平均破损率 {avg_pct}。"
    )


# ---------------------------------------------------------------------------
# 占位符替换（含 run 拆行 / 空格 / 中英文符号 兼容）
# ---------------------------------------------------------------------------


# 用于把模板里被 Word 拆开的占位符归一化：
#   "{{chart_body_length_distributi on}}" 或带 \n/空格/全角空格 → "{{chart_body_length_distribution}}"
_PLACEHOLDER_BOUNDARY = re.compile(r"\{\s*\{\s*([a-zA-Z0-9_]+)\s*\}\s*\}")
_PLACEHOLDER_FRAGMENTED = re.compile(
    r"\{\s*\{[^{}\n]*?(?:chart_|total_fish|complete_fish|incomplete_fish|failed_fish|damaged_fish|avg_|max_|min_|video_id|report_no|generated_at|report_date_cn|system_name|report_type_name|report_status_name|video_status|report_title|fish_count|total_count|complete_count|incomplete_count|failed_count|damaged_count|avg_damage_ratio|max_damage_ratio|status|daily_conclusion)[^{}]*?\}\s*\}"
)
_ANY_BRACES = re.compile(r"\{\s*\{\s*([a-zA-Z0-9_]+)\s*\}\s*\}")


def _normalize_placeholder(text: str) -> str:
    """把模板被换行/空格拆散的占位符文本归一化，便于检测。

    例： '{{chart_body_length_distributi on}}' -> '{{chart_body_length_distribution}}'
    """
    s = text.replace("\r", "").replace("\t", "").replace("\n", "")
    s = s.replace(" ", "").replace("\u3000", "")
    return s


def _replace_text_placeholders(doc, context: dict[str, str]) -> list[str]:
    """对 doc 中所有 paragraph + 表格 cell paragraph 做文字占位符替换。

    处理：
        * 容忍 Word 把 {{key}} 拆成多 run（甚至拆成多行）。
        * 用归一化文本识别 key，再用容忍换行的正则回写。
        * 跳过只含 chart_xxx 占位符的段落（交给图片替换）。
    返回残留文字占位符。
    """
    chart_keys = set(CHART_KEYS)
    synonyms = dict(context)

    def fix_paragraph(p) -> None:
        raw_text = p.text
        if "{{" not in raw_text or "}}" not in raw_text:
            return
        norm = _normalize_placeholder(raw_text)
        # 用归一化文本匹配 key
        names_norm = {
            m.group(1) for m in re.finditer(r"\{\{([a-zA-Z0-9_]+)\}\}", norm)
        }
        if not names_norm:
            return
        # 跳过纯 chart 占位符（图片会处理）
        if names_norm and names_norm.issubset(chart_keys):
            return

        # 拼回整段文本（多 run join）
        full_text = "".join(r.text or "" for r in p.runs)
        new_text = full_text
        # 先按 {{key}} 精确替换
        for k, v in synonyms.items():
            token = "{{" + k + "}}"
            if token in new_text:
                new_text = new_text.replace(token, str(v))
        # 再按 容忍空白/换行 的形式替换
        for k, v in synonyms.items():
            pat = re.compile(
                r"\{\s*\{\s*" + re.escape(k) + r"\s*\}\s*\}"
            )
            new_text = pat.sub(str(v), new_text)

        if p.runs:
            for r in p.runs[1:]:
                r.text = ""
            p.runs[0].text = new_text
        else:
            p.add_run(new_text)

    def walk(container) -> None:
        for p in list(container.paragraphs):
            fix_paragraph(p)
        for t in list(container.tables):
            for row in t.rows:
                for cell in row.cells:
                    walk(cell)

    walk(doc)
    return _collect_residual_placeholders(doc)


def _collect_residual_placeholders(doc) -> list[str]:
    """扫描 doc 中残留的文字占位符（chart_xxx 占位符由图片替换处理，这里跳过）。"""
    chart_keys = set(CHART_KEYS)
    found: list[str] = []

    def walk(container) -> None:
        for p in container.paragraphs:
            t = p.text
            if t:
                _collect_in_text(t, found, chart_keys)
        for tab in container.tables:
            for row in tab.rows:
                for cell in row.cells:
                    walk(cell)

    walk(doc)
    return sorted(set(found))


def _collect_in_text(text: str, out: list[str], skip_keys: set[str]) -> None:
    norm = _normalize_placeholder(text)
    for m in re.finditer(r"\{\{([a-zA-Z0-9_]+)\}\}", norm):
        key = m.group(1)
        if key in skip_keys:
            continue
        out.append("{{" + key + "}}")
    # 也尝试解析碎片形（例如 "{{chart_body_length_distributi on}}"）
    # 上一步正则已经会在 _normalize_placeholder 后匹配到


# ---------------------------------------------------------------------------
# 图表插入：识别模板图表占位符所在 cell
# ---------------------------------------------------------------------------


def _insert_chart_images(doc, chart_paths: dict[str, Path]) -> dict[str, str]:
    """扫描所有表格 cell，找到包含 chart 占位符的 cell：

        1. 先把 cell 内的所有 chart token（含拆散形态）从 run 文本中抹掉，
           保留其它文字（例如"图1：鱼体长度分布图"）。
        2. 在 cell 末尾追加一段专门放图（put_image_in_cell）。

    支持被换行 / 空格拆散的占位符形态。
    """
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT

    residual: list[str] = []
    inserted: dict[str, str] = {}

    def _strip_token_from_cell(cell, key: str) -> None:
        """从 cell 中所有 run 文本里抹掉 {{ key }} 的所有拆散形态。"""
        # 抹掉规范的 {{key}}、容忍空白/换行的形态。
        # 用"无捕获组"替换更安全。
        pat = re.compile(
            r"\{\s*\{\s*" + re.escape(key) + r"\s*\}\s*\}"
        )
        # 同时也抹掉 fragments：把同一 key 的 normalized 形式再对应回去也不够稳
        # 直接遍历 run 修改。
        for p in cell.paragraphs:
            for r in p.runs:
                if not r.text:
                    continue
                if pat.search(r.text):
                    r.text = pat.sub("", r.text)
            # 段落级也尝试一次（多 run 拼接后第一次出现的位置）
            full = "".join(rr.text or "" for rr in p.runs)
            if pat.search(full):
                full = pat.sub("", full)
                # 写回第一个 run 清空其它
                if p.runs:
                    for rr in p.runs[1:]:
                        rr.text = ""
                    p.runs[0].text = full
                else:
                    p.add_run(full)

    def _append_chart_to_cell(cell, key: str) -> None:
        png = chart_paths.get(key)
        # 创建新段落承载图片
        try:
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        except Exception:
            pass

        pic_p = cell.add_paragraph()
        try:
            pic_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        except Exception:
            pass

        if not png or not png.exists():
            pic_p.add_run(f"暂无{key}图表")
            return

        try:
            w_in, h_in = calc_fit_image_size_safe(png, cell, max_w=5.8, max_h=3.2)
            pic_p.add_run().add_picture(
                str(png),
                width=Inches(w_in),
                height=Inches(h_in),
            )
            inserted[key] = _data_rel(png)
        except Exception as exc:
            logger.warning(f"[video_report] 插入 {key} 失败：{exc}")
            pic_p.add_run(f"图表加载失败：{type(exc).__name__}")

    def walk(container) -> None:
        for tab in container.tables:
            for row in tab.rows:
                for cell in row.cells:
                    handle_cell(cell)

    def handle_cell(cell) -> None:
        all_text = "\n".join(p.text for p in cell.paragraphs)
        norm = _normalize_placeholder(all_text)
        chart_hits = [
            k for k in ALL_CHART_KEYS
            if _normalize_placeholder("{{" + k + "}}") in norm
        ]
        # 兼容"图 6：大黄鱼形体指标测量示意图"标题行——下一行或同段插图
        if FIXED_BODY_DEMO_CHART_KEY not in chart_hits and (
            "图 6" in all_text or "图6" in all_text or "大黄鱼形体指标测量示意图" in all_text
        ):
            chart_hits.append(FIXED_BODY_DEMO_CHART_KEY)
        if not chart_hits:
            return

        # 1) 抹掉 token（保留其它文字）
        for k in chart_hits:
            _strip_token_from_cell(cell, k)

        # 2) 同一 cell 中追加图片段
        for k in chart_hits:
            _append_chart_to_cell(cell, k)

    # 先把所有 chart token 抹掉，然后追加图片
    walk(doc)

    # 兜底：顶层 paragraph（非 cell）里若有 chart 占位符也抹掉
    for p in doc.paragraphs:
        norm = _normalize_placeholder(p.text)
        for k in ALL_CHART_KEYS:
            tok = _normalize_placeholder("{{" + k + "}}")
            if tok in norm:
                # 用容忍换行的正则替换
                pat = re.compile(
                    r"\{\s*\{\s*" + re.escape(k) + r"\s*\}\s*\}"
                )
                full = p.text
                full = pat.sub("", full)
                if p.runs:
                    for r in p.runs[1:]:
                        r.text = ""
                    p.runs[0].text = full
                else:
                    p.add_run(full)

    # 再次扫描确认没有残留
    for tab in doc.tables:
        for row in tab.rows:
            for cell in row.cells:
                t = "\n".join(p.text for p in cell.paragraphs)
                norm = _normalize_placeholder(t)
                for k in ALL_CHART_KEYS:
                    tok = _normalize_placeholder("{{" + k + "}}")
                    if tok in norm:
                        residual.append("{{" + k + "}}")

    return {
        "inserted": inserted,
        "residual": sorted(set(residual)),
    }


def calc_fit_image_size_safe(
    image_path: Path,
    cell,
    *,
    max_w: float,
    max_h: float,
) -> tuple[float, float]:
    """按 cell 内可用区域选合适的图片尺寸。

    复用 report_docx_service 的 calc_fit_image_size。
    """
    from app.services.report_docx_service import calc_fit_image_size, _safe_cell_size_inch

    cw = _safe_cell_size_inch(cell, "width")
    ch = _safe_cell_size_inch(cell, "height")

    usable_w = max_w
    if cw is not None and cw > 0:
        usable_w = max(0.4, cw - 0.1)
    usable_h = max_h
    if ch is not None and ch > 0:
        usable_h = max(0.3, ch - 0.1)
    return calc_fit_image_size(image_path, usable_w, usable_h)


# ---------------------------------------------------------------------------
# 写回 pipeline_result.json
# ---------------------------------------------------------------------------


def _write_report_urls_back_to_pipeline(
    video_id: str,
    docx: Path,
    pdf: Path | None,
    chart_paths: dict[str, str],
) -> None:
    p = _pipeline_result_path(video_id)
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return
    if docx.exists():
        data["report_docx"] = _data_rel(docx)
        data["report_docx_url"] = _static_url(docx)
    if pdf and pdf.exists():
        data["report_pdf"] = _data_rel(pdf)
        data["report_pdf_url"] = _static_url(pdf)
    if chart_paths:
        data["report_charts"] = chart_paths
    try:
        p.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning(f"[video_report] 写回 pipeline_result.json 失败: {exc}")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def generate_video_pdf_report(video_id: str) -> dict[str, Any]:
    """为单一 video_id 生成 DOCX + PDF 报表。

    返回 dict：
        {
            "ok": True/False,
            "video_id": str,
            "report_docx": "data/results/<vid>/reports/<vid>_report.docx",
            "report_docx_url": "/static/results/<vid>/reports/<vid>_report.docx",
            "report_pdf":     ...,
            "report_pdf_url": ...,
            "chart_paths":    {chart_key: data/...png},
            "chart_urls":     {chart_key: /static/...png},
            "residual_text_placeholders": [...],
            "residual_chart_placeholders": [...],
        }
    """
    video_id = _ensure_str(video_id).strip()
    if not video_id:
        return {"ok": False, "error": "video_id is empty"}

    try:
        template_path = get_video_report_template_path()
    except FileNotFoundError as exc:
        return {"ok": False, "video_id": video_id, "error": str(exc)}

    payload = _read_pipeline_result(video_id)
    fish_list = payload.get("fish") or []
    if not isinstance(fish_list, list):
        fish_list = []
    fish_list = [f for f in fish_list if isinstance(f, dict)]
    summary = _summary_get(payload)

    # 1. 5 张统计图
    charts_dir = _charts_dir(video_id)
    try:
        chart_files = generate_charts(fish_list, charts_dir)
    except Exception as exc:
        logger.exception(f"[video_report] 生成 charts 失败: {exc}")
        chart_files = {}

    # 若 generate_charts 已生成 fixed 路径，则不再重复
    fixed_demo_path = charts_dir / f"{FIXED_BODY_DEMO_CHART_KEY}.png"
    if not fixed_demo_path.exists():
        try:
            png = prepare_fixed_body_demo_image(charts_dir)
            if png and Path(png).exists():
                fixed_demo_path = charts_dir / f"{FIXED_BODY_DEMO_CHART_KEY}.png"
                if png != fixed_demo_path:
                    try:
                        shutil.copy2(str(png), str(fixed_demo_path))
                    except Exception:
                        fixed_demo_path = Path(png)
        except Exception as exc:
            logger.warning(f"[video_report] 生成图6固定图失败：{exc}")

    chart_paths: dict[str, Path] = chart_files
    chart_paths[FIXED_BODY_DEMO_CHART_KEY] = fixed_demo_path

    # 2. 文字 context
    body_vals = _to_float_list(fish_list, "body_length_cm")
    no_tail_vals = _to_float_list(fish_list, "body_length_no_tail_cm")
    height_vals = _to_float_list(fish_list, "body_height_cm")
    weight_vals = _to_float_list(fish_list, "pred_weight_g")
    cf_vals = _to_float_list(fish_list, "condition_factor")

    extra = {
        "avg_body_length_cm": _avg_or_none(body_vals),
        "avg_body_length_without_tail_cm": _avg_or_none(no_tail_vals),
        "avg_body_height_cm": _avg_or_none(height_vals),
        "avg_body_weight_g": _avg_or_none(weight_vals),
        "avg_condition_factor": _avg_or_none(cf_vals),
        "min_body_length_cm": _min_or_none(body_vals),
        "max_body_length_cm": _max_or_none(body_vals),
        "min_body_length_without_tail_cm": _min_or_none(no_tail_vals),
        "max_body_length_without_tail_cm": _max_or_none(no_tail_vals),
        "min_body_height_cm": _min_or_none(height_vals),
        "max_body_height_cm": _max_or_none(height_vals),
        "min_body_weight_g": _min_or_none(weight_vals),
        "max_body_weight_g": _max_or_none(weight_vals),
        "min_condition_factor": _min_or_none(cf_vals),
        "max_condition_factor": _max_or_none(cf_vals),
    }

    context = _context_for_template(video_id, payload, fish_list, summary, extra)

    reports_dir = _video_reports_dir(video_id)
    docx_path = _docx_path_for(video_id)
    pdf_path = _pdf_path_for(video_id)

    # 3. 复制模板 → 文字替换 → 图表替换 → 保存
    residual_text: list[str] = []
    residual_chart: list[str] = []
    try:
        from docx import Document

        shutil.copy2(str(template_path), str(docx_path))
        doc = Document(str(docx_path))

        residual_text = _replace_text_placeholders(doc, context)

        ins = _insert_chart_images(doc, chart_paths)
        residual_chart = ins.get("residual", [])

        doc.save(str(docx_path))
    except Exception as exc:
        logger.exception(f"[video_report] docx 生成失败 {video_id}: {exc}")
        return {
            "ok": False,
            "video_id": video_id,
            "error": f"docx 生成失败：{type(exc).__name__}: {exc}",
        }

    # 4. LibreOffice 转 PDF
    pdf_out: Path | None = None
    try:
        pdf_out = _convert_docx_to_pdf(docx_path, reports_dir)
    except Exception as exc:
        logger.warning(f"[video_report] PDF 转换异常 {video_id}: {exc}")

    # 5. 写回 pipeline_result.json
    rel_charts = charts_rel_paths(charts_dir, PROJECT_ROOT)
    _write_report_urls_back_to_pipeline(video_id, docx_path, pdf_out, rel_charts)

    # 6. chart_urls 构建
    chart_urls: dict[str, str] = {}
    for k, rel in rel_charts.items():
        # data/results/.../charts/x.png -> /static/results/.../charts/x.png
        if rel.startswith("data/"):
            chart_urls[k] = "/static/" + rel[len("data/"):]
        elif rel.startswith("/"):
            chart_urls[k] = "/static/" + rel.lstrip("/")
        else:
            chart_urls[k] = "/static/" + rel

    result = {
        "ok": True,
        "video_id": video_id,
        "report_docx": _data_rel(docx_path) if docx_path.exists() else "",
        "report_docx_url": _static_url(docx_path) if docx_path.exists() else "",
        "report_pdf": _data_rel(pdf_out) if pdf_out and pdf_out.exists() else "",
        "report_pdf_url": _static_url(pdf_out) if pdf_out and pdf_out.exists() else "",
        "chart_paths": rel_charts,
        "chart_urls": chart_urls,
        "fixed_body_demo_url": chart_urls.get(FIXED_BODY_DEMO_CHART_KEY, ""),
        "fixed_body_demo_path": rel_charts.get(FIXED_BODY_DEMO_CHART_KEY, ""),
        "residual_text_placeholders": residual_text,
        "residual_chart_placeholders": residual_chart,
    }
    logger.info(
        f"[video_report] 生成完成 {video_id}: docx={result['report_docx']} "
        f"pdf={result['report_pdf']} fish={len(fish_list)} "
        f"residual_text={len(residual_text)} residual_chart={len(residual_chart)}"
    )
    return result


__all__ = [
    "CHART_KEYS",
    "generate_video_pdf_report",
    "get_video_report_template_path",
]
