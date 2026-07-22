from __future__ import annotations

from typing import Any

from rq import Queue
from rq.job import Retry

from app.infra.redis_client import get_redis


DEFAULT_QUEUE_NAME = "fish"


def get_queue(name: str = DEFAULT_QUEUE_NAME) -> Queue:
    return Queue(
        name=name,
        connection=get_redis(),
        default_timeout=60 * 60 * 2,  # 2 hours
    )


# ---------------------------------------------------------------------------
# 新主流程：整视频算法 CLI pipeline
# ---------------------------------------------------------------------------


def enqueue_algorithm_video_pipeline(
    *,
    input_video: str,
    camera_id: str = "default",
    device: str = "cuda",
    video_fps: int = 25,
    overwrite: bool = True,
) -> str:
    """
    入队整视频算法 CLI pipeline。

    真实逻辑：调用 algorithms.tools.run_video_pipeline 后导入数据库。
    """
    from app.workers.handlers.algorithm_video_pipeline import (
        run_algorithm_video_pipeline,
    )

    q = get_queue()
    job = q.enqueue(
        run_algorithm_video_pipeline,
        kwargs={
            "input_video": input_video,
            "camera_id": camera_id,
            "device": device,
            "video_fps": video_fps,
            "overwrite": overwrite,
        },
        retry=Retry(max=2, interval=[10, 30]),
        job_timeout=60 * 60 * 4,
        result_ttl=60 * 60 * 2,
        failure_ttl=60 * 60,
    )
    return job.id


def enqueue_algorithm_video_pipeline_from_video_task(
    *,
    video_id: str,
    input_video: str,
) -> str:
    """
    视频上传 / 摄像头停止时使用的便捷入口。
    video_id 仅作日志 / 数据库追溯用，算法 CLI 会自动生成算法 video_id。
    """
    from app.workers.handlers.algorithm_video_pipeline import (
        run_algorithm_video_pipeline,
    )

    q = get_queue()
    job = q.enqueue(
        run_algorithm_video_pipeline,
        kwargs={
            "upload_video_id": video_id,
            "input_video": input_video,
        },
        retry=Retry(max=2, interval=[10, 30]),
        job_timeout=60 * 60 * 4,
        result_ttl=60 * 60 * 2,
        failure_ttl=60 * 60,
    )
    return job.id


# ---------------------------------------------------------------------------
# 旧接口兼容
# ---------------------------------------------------------------------------


def enqueue_stream_ingest(*args: Any, **kwargs: Any) -> str:
    """
    兼容旧调用者：transition 期间保留旧函数名。

    旧语义是抽帧 + 单鱼五维，本阶段已切到整视频算法 CLI pipeline。
    调用方应改用：
        enqueue_algorithm_video_pipeline_from_video_task(...)
    """
    raise RuntimeError(
        "enqueue_stream_ingest 已废弃；请使用 "
        "app.workers.queue.enqueue_algorithm_video_pipeline 或 "
        "enqueue_algorithm_video_pipeline_from_video_task。"
    )


def enqueue_fish_pipeline(*args: Any, **kwargs: Any) -> str:
    """
    兼容旧调用者：单鱼 pipeline 已废弃。
    """
    raise RuntimeError(
        "enqueue_fish_pipeline 已废弃；请使用 "
        "app.workers.queue.enqueue_algorithm_video_pipeline。"
    )
