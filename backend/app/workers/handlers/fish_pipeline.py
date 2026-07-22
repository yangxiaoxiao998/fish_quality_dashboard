# 作用：兼容旧单鱼 pipeline handler 文件名。
#
# 第一阶段已经把所有"逐鱼 mock crop + 五维"逻辑删除，
# 由 algorithm_video_pipeline handler 接管整视频算法 CLI pipeline。

from __future__ import annotations

from typing import Any


def run_fish_pipeline(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """
    已废弃：

    单鱼 pipeline 已废弃，请改用：
        app.workers.queue.enqueue_algorithm_video_pipeline
    """
    raise RuntimeError(
        "run_fish_pipeline 已废弃；请改用 enqueue_algorithm_video_pipeline。"
    )


__all__ = ["run_fish_pipeline"]
