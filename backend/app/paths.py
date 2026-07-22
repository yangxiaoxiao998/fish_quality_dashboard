# path.py
from pathlib import Path


# 项目根目录（fish_quality_dashboard）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# data 目录
DATA_DIR = PROJECT_ROOT / "data"

# 上传目录（前端仍上传到 data/uploads/videos，video_task.source_path 引用）
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
