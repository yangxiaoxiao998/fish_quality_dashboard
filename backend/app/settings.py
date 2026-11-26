# 后端配置 settings（统一从环境变量读 Postgres/Redis）
from __future__ import annotations

from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    系统配置统一入口：
    - 全部来自环境变量（docker / k8r / 本地都一致）
    - 由 main.py 在启动时加载并注入
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    app_env: str = "dev"

    database_url: str
    redis_url: str
    fish_api_key: str = ""
    cors_allowed_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    # Runtime processing parameters are environment-backed until a typed
    # configuration service is introduced.
    sample_fps: int = 25
    snapshot_strategy: str = "first_seen"  # first_seen / best_sharpness / etc.

    # 抽帧 / crop 帧数上限
    # 0 或 -1 表示不限制
    # 默认 500，避免突然处理很慢
    max_extract_frames: int = 500
    max_crop_frames: int = 500

    # 卡住任务超时：超过该分钟数还在 processing 的任务会被 cleanup_stuck_tasks.py 清理
    stuck_task_timeout_minutes: int = 30

    # 批量输入（batch pipeline）相关配置
    # pipeline_input_source: 选择批量输入的图像来源
    #   - jobs_raw   : data/jobs/<video_id>/frames/raw/
    #   - perspective : data/results/perspective_images/...
    #   - corrected   : data/results/corrected_images/...
    pipeline_input_source: Literal["original", "perspective", "corrected"] = "perspective"

    # batch_input_pattern: 可选的 glob 模式（例如 "*.jpg"），None 表示使用默认后缀集合
    batch_input_pattern: Optional[str] = None

    # batch_chunk_workers: 单个 chunk 内并发 worker 数（CPU 模式下建议为 1）
    batch_chunk_workers: int = 1

    # batch_frame_limit: 单次批处理最大帧数，None 或 0/-1 表示不限制
    batch_frame_limit: Optional[int] = None


settings = Settings()
