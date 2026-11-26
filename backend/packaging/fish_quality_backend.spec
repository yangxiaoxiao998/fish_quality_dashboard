# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules, copy_metadata


PROJECT_ROOT = Path(SPECPATH).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
ALGORITHMS_ROOT = PROJECT_ROOT / "algorithms"
GROUNDING_DINO_ROOT = ALGORITHMS_ROOT / "vendor" / "GroundingDINO"
SEGMENT_ANYTHING_ROOT = (
    ALGORITHMS_ROOT / "vendor" / "Grounded-Segment-Anything" / "segment_anything"
)

for search_path in (
    PROJECT_ROOT,
    BACKEND_ROOT,
    ALGORITHMS_ROOT,
    GROUNDING_DINO_ROOT,
    SEGMENT_ANYTHING_ROOT,
):
    text = str(search_path)
    if text not in sys.path:
        sys.path.insert(0, text)


def safe_collect(package_name):
    try:
        return collect_submodules(package_name)
    except Exception:
        return []


def optional_metadata(*distribution_names):
    collected = []
    for distribution_name in distribution_names:
        try:
            collected += copy_metadata(distribution_name, recursive=True)
        except Exception:
            pass
    return collected


hiddenimports = safe_collect("app")
hiddenimports += safe_collect("groundingdino")
hiddenimports += safe_collect("segment_anything")
hiddenimports += safe_collect("yapf")
hiddenimports += safe_collect("yapf_third_party")
hiddenimports += [
    "addict",
    "platformdirs",
    "timm.models.layers",
    "algorithms.config.cleanup",
    "algorithms.config.naming",
    "algorithms.config.paths",
    "algorithms.config.state",
    "algorithms.core.adjust",
    "algorithms.core.adjust_calibration",
    "algorithms.core.all_model_catboost",
    "algorithms.core.belly_color",
    "algorithms.core.calibration_config",
    "algorithms.core.filter_complete_fish",
    "algorithms.core.filter_complete_pairs",
    "algorithms.core.fish_condition",
    "algorithms.core.grounded_sam_mmdet_batch",
    # test3 的正式视频流程使用 batch_fix；必须显式加入冻结包的 PYZ。
    "algorithms.core.grounded_sam_mmdet_batch_fix",
    "algorithms.core.merge_funs",
    "algorithms.core.seg_track",
    "algorithms.core.surface_damage",
    "algorithms.core.tail_cut_annotator",
    "algorithms.core.vit_completeness",
    "algorithms.tools.clean_data",
    "algorithms.tools.run_video_pipeline",
    "algorithms.tools.video_to_frames",
]

# XGBoost 3.x locates its native library relative to the Python package at
# runtime.  PyInstaller does not currently collect this library in our build
# environment, so keep the original xgboost/lib layout explicitly.
xgboost_binaries = collect_dynamic_libs("xgboost")
if not xgboost_binaries:
    raise RuntimeError("xgboost/lib/libxgboost.so was not found in the build environment")

datas = [
    # XGBoost reads this file with open(os.path.join(dirname(__file__), ...))
    # during import; it is not a Python module and must be copied beside the
    # frozen xgboost package.
    (str(Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages" / "xgboost" / "VERSION"), "xgboost"),
    (str(BACKEND_ROOT / "app" / "templates"), "backend/app/templates"),
    (str(BACKEND_ROOT / "app" / "services" / "update"), "backend/app/services/update"),
    # run_video_pipeline 使用 runpy.run_path 加载带连字符的脚本；
    # 只把这个动态脚本作为只读资源放进 PyInstaller 内部资源，
    # 不把整个 algorithms/core 源码目录复制到交付目录。
    (str(ALGORITHMS_ROOT / "core" / "arched-back.py"), "algorithms/core"),
    (str(ALGORITHMS_ROOT / "config" / "adjust_calibration_margin.json"), "algorithms/config"),
    (str(PROJECT_ROOT / "version.json"), "."),
]
datas += optional_metadata(
    "catboost",
    "fastapi",
    "matplotlib",
    "mmdet",
    "mmengine",
    "pydantic",
    "pydantic-settings",
    "redis",
    "rq",
    "sqlalchemy",
    "timm",
    "torch",
    "torchvision",
    "transformers",
    "uvicorn",
    "xgboost",
)

a = Analysis(
    [str(BACKEND_ROOT / "packaging" / "fish_quality_backend.py")],
    pathex=[
        str(PROJECT_ROOT),
        str(BACKEND_ROOT),
        str(ALGORITHMS_ROOT),
        str(GROUNDING_DINO_ROOT),
        str(SEGMENT_ANYTHING_ROOT),
    ],
    binaries=xgboost_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["IPython", "jupyter", "notebook", "pytest", "tkinter"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="fish-quality-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="fish-quality-backend",
)
