# Redis 客户端（infra）
from __future__ import annotations

import redis
from app.settings import settings


def get_redis() -> redis.Redis:
    """
    统一 Redis 连接入口：
    - RQ queue 使用
    - pubsub/缓存后续也使用

    注意：RQ 内部会自己处理 job payload 的字节编码。
    必须用 decode_responses=False，否则 fetch_job 会因为
    payload 含二进制字节而抛 UnicodeDecodeError，导致
    任务 enqueue 成功但 worker 永远取不出来，video_task 永远 queued。
    """
    return redis.Redis.from_url(settings.redis_url, decode_responses=False)
