# path.py
from app.runtime_paths import (
    ALGORITHMS_ROOT,
    CONFIG_ROOT,
    DATA_ROOT,
    INSTALL_ROOT,
    RESOURCE_ROOT,
    resource_path,
)


# 源码模式下是仓库根；PyInstaller 模式下是后端可执行文件所在目录。
PROJECT_ROOT = INSTALL_ROOT

# data 目录
DATA_DIR = DATA_ROOT

# 运行期摄像头录制和媒体产物目录，video_task.source_path 引用其中的文件。
UPLOAD_DIR = DATA_DIR / "uploads"
VID_DIR = UPLOAD_DIR / "videos"
SRC_IMG_DIR = UPLOAD_DIR / "original_images"

# 标定目录
CALIBRATION_DIR = DATA_DIR / "calibration"
CALIBRATION_CURRENT_JSON = CALIBRATION_DIR / "current_calibration.json"

# 算法 jobs 中间目录
JOBS_DIR = DATA_DIR / "jobs"

# 算法长期 results 目录
RESULTS_DIR = DATA_DIR / "results"

# 日志目录
LOG_DIR = DATA_DIR / "logs"

# 旧 sqlite 兼容常量（保留但不再使用）
DB_FILE = RESULTS_DIR / "fish_detection.db"
DB_LOG_FILE = LOG_DIR / "database.log"


def ensure_directories() -> None:
    """启动时确保目录存在。"""
    for p in [
        DATA_DIR,
        UPLOAD_DIR,
        VID_DIR,
        CALIBRATION_DIR,
        JOBS_DIR,
        RESULTS_DIR,
        LOG_DIR,
    ]:
        p.mkdir(parents=True, exist_ok=True)


__all__ = [
    "ALGORITHMS_ROOT",
    "CALIBRATION_CURRENT_JSON",
    "CALIBRATION_DIR",
    "CONFIG_ROOT",
    "DATA_DIR",
    "DB_FILE",
    "DB_LOG_FILE",
    "JOBS_DIR",
    "LOG_DIR",
    "PROJECT_ROOT",
    "RESOURCE_ROOT",
    "SRC_IMG_DIR",
    "UPLOAD_DIR",
    "VID_DIR",
    "ensure_directories",
    "resource_path",
]
