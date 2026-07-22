"""data 目录清理工具。

只允许保留 ``data/`` 下的四个一级目录：

- ``data/calibration/``
- ``data/jobs/``
- ``data/results/``
- ``data/logs/``

其他一级目录（历史测试目录如 ``data/input_videos/`` / ``data/output_*_images/`` /
``data/tmp_test/`` / ``data/debug_outputs/`` / ``data/test_outputs/`` /
``data/output_test/`` / ``data/pipeline_test/`` / ``data/sandbox_output/`` /
``data/temp_results/`` 等）默认会被列出；只有 ``--apply`` 才会真正删除。

安全保证：

1. **必须**先 ``--dry-run``（默认）确认目标；``--apply`` 才执行删除。
2. 删除对象必须**在 ``DATA_ROOT`` 之下**；``DATA_ROOT`` 本身永不删除。
3. 不删除 ``data/calibration`` / ``data/jobs`` / ``data/results`` / ``data/logs``。
4. 不删除项目根目录下的 ``output_25fps.mp4`` 与 ``input_adjust.png``。
5. 不级联 ``data/jobs`` / ``data/results`` 内部 video_id 子目录（那是
   ``run_video_pipeline`` 配合 ``--clean / --overwrite`` 的责任）。
6. 删除前打印每条目标路径。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
ALGORITHMS_ROOT = THIS_DIR.parent
if str(ALGORITHMS_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS_ROOT))

from config.cleanup import remove_dir  # noqa: E402
from config.paths import DATA_ROOT, resolve_under_data  # noqa: E402


# 长期保留的四个一级目录
PROTECTED_TOP_LEVEL: tuple[str, ...] = (
    "calibration",
    "jobs",
    "results",
    "logs",
)

# 历史约定上的"测试/旧输出"目录匹配模式，便于提示 + 清理
LEGACY_TEST_DIRS: tuple[str, ...] = (
    "input_videos",
    "output_crop_images_gaoyuxi",
    "output_long_height_images",
    "output_long_height_results",
    "output_test",
    "tmp_test",
    "temp_results",
    "debug_outputs",
    "test_outputs",
    "pipeline_test",
    "sandbox_output",
)


def find_unauthorized_top_level_dirs() -> list[Path]:
    """列出 ``data/`` 下除四个保留目录以外的所有一级目录。"""

    if not DATA_ROOT.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(DATA_ROOT.iterdir()):
        if not child.is_dir():
            continue
        if child.name in PROTECTED_TOP_LEVEL:
            continue
        # 任何其他一级目录都视为需要清理
        out.append(child)
    return out


def classify(unauthorized: list[Path]) -> dict[str, list[Path]]:
    """把 unauthorized 列表按"是否在历史 legacy 名单中"分两类。"""

    known_legacy: list[Path] = []
    other: list[Path] = []
    for p in unauthorized:
        if p.name in LEGACY_TEST_DIRS:
            known_legacy.append(p)
        else:
            other.append(p)
    return {"legacy": known_legacy, "other": other}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m algorithms.tools.clean_data",
        description=(
            "清理 data/ 下除 calibration/jobs/results/logs 之外的一级目录。"
            "默认 --dry-run，只列出来不删除；必须加 --apply 才执行。"
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="只列出将被删除的一级目录，不执行任何删除（默认行为）",
    )
    group.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="真正执行删除",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="与 --apply 一起使用，跳过交互确认（脚本里目前是直接执行）",
    )
    args = parser.parse_args(argv)

    print(f"data 根目录: {DATA_ROOT}")
    print(f"长期保留:   {', '.join(PROTECTED_TOP_LEVEL)}")
    print(f"工作模式:   {'DRY-RUN' if args.dry_run else 'APPLY'}")
    print()

    unauthorized = find_unauthorized_top_level_dirs()
    bucket = classify(unauthorized)

    if not unauthorized:
        print("[OK] data/ 下没有未授权的一级目录，无需清理。")
        return 0

    if bucket["legacy"]:
        print("[legacy 目录] 与历史测试脚本命名匹配，建议清理：")
        for p in bucket["legacy"]:
            print(f"  - {p}")
    if bucket["other"]:
        print("[other 目录] 不在保留白名单内，但也不在 legacy 名单，列出供人工确认：")
        for p in bucket["other"]:
            print(f"  - {p}")
    print()

    if args.dry_run:
        print("[dry-run] 未做任何修改。如需清理请加 --apply。")
        return 0

    # apply 模式
    removed = 0
    failed = 0
    for target in unauthorized:
        try:
            # resolve 并校验 target 位于 DATA_ROOT 内（也排除 DATA_ROOT 自身）
            abs_target = resolve_under_data(target)
            if abs_target == DATA_ROOT.resolve():
                print(f"[SKIP] {target} 等于 DATA_ROOT，跳过")
                continue
            if abs_target == resolve_under_data(target).parent:
                # 直接是 DATA_ROOT 自身的兄弟目录，不会发生但保留保险
                print(f"[SKIP] {target} 位于 DATA_ROOT 之外，跳过")
                continue
            print(f"[remove] {target}")
            shutil.rmtree(target)
            removed += 1
        except Exception as exc:
            print(f"[ERROR] 删除失败：{target} -> {exc}")
            failed += 1

    print()
    print(f"=== 清理完成：成功 {removed}，失败 {failed} ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())