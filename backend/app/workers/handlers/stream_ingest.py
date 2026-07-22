# 作用：兼容旧 stream_ingest handler 文件名。
#
# 第一阶段已经把所有"抽帧 -> mock crop -> mock 五维"的旧链路删除，
# 并由 algorithm_video_pipeline handler 接管整视频算法 CLI pipeline。
#
# 本文件保留是因为仍可能有外部代码 import 它。实际 run_stream_ingest
# 不会执行任何算法业务，仅用于占位与显式错误。

from __future__ import annotations

from typing import Any


def run_stream_ingest(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """
    已废弃：

    旧实现：抽帧 + 单鱼五维 + 入库。
    当前阶段：算法统一通过整视频 CLI pipeline 跑，使用：
        app.workers.queue.enqueue_algorithm_video_pipeline_from_video_task
        app.workers.handlers.algorithm_video_pipeline.run_algorithm_video_pipeline
    """
    raise RuntimeError(
        "run_stream_ingest 已废弃；请改用 enqueue_algorithm_video_pipeline_from_video_task。"
    )


__all__ = ["run_stream_ingest"]
