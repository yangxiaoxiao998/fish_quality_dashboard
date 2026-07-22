# backend/app/routers/led_router.py
#
# LED 光圈控制 - HTTP 路由
#
# 接口（前缀 /api/led）：
#   GET  /api/led/ping
#   POST /api/led/start
#   POST /api/led/off
#   POST /api/led/color        body: {"r": 0~255, "g": 0~255, "b": 0~255}
#   POST /api/led/brightness   body: {"brightness": 0~255}
#
# 返回统一结构：
#   {
#     "ok": true,
#     "pi_base_url": "...",
#     "data": {... 树莓派原始响应 ...}
#   }
#
# 失败时 raise HTTPException(502)，前端看到的是 detail 字符串。

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.services import led_service
from app.services.pi_endpoint_resolver import (
    PiResolutionError,
    get_pi_logical_base_url,
    get_pi_resolution_diag,
)


router = APIRouter(prefix="/api/led", tags=["led"])


class LedColorRequest(BaseModel):
    r: int = Field(..., ge=0, le=255, description="红色 0~255")
    g: int = Field(..., ge=0, le=255, description="绿色 0~255")
    b: int = Field(..., ge=0, le=255, description="蓝色 0~255")


class LedBrightnessRequest(BaseModel):
    brightness: int = Field(..., ge=0, le=255, description="亮度 0~255")


def _diag_block() -> dict:
    """返回非敏感诊断信息：逻辑地址 + 解析 IP（仅诊断用）。"""
    diag = get_pi_resolution_diag()
    return {
        "pi_base_url": diag.get("logical_base_url"),
        "resolved_ip": diag.get("resolved_ip"),
    }


async def _wrap_async(call) -> dict:
    """包一层：把同步树莓派请求放到线程池，避免阻塞事件循环。

    失败 → HTTP 502。返回结构兼容旧字段：
        ok / pi_base_url / data / resolved_ip
    """
    try:
        result = await run_in_threadpool(call)
    except led_service.LedServiceError as exc:
        diag = _diag_block()
        raise HTTPException(
            status_code=502,
            detail={
                "ok": False,
                "error": str(exc),
                "pi_base_url": diag.get("pi_base_url"),
                "resolved_ip": diag.get("resolved_ip"),
            },
        )
    except PiResolutionError as exc:
        diag = _diag_block()
        raise HTTPException(
            status_code=502,
            detail={
                "ok": False,
                "error": str(exc),
                "pi_base_url": diag.get("pi_base_url"),
                "resolved_ip": None,
            },
        )
    diag = _diag_block()
    return {
        "ok": True,
        "pi_base_url": diag.get("pi_base_url"),
        "resolved_ip": diag.get("resolved_ip"),
        "data": result,
    }


@router.get("/ping")
async def ping_led_service():
    """检查树莓派服务是否可达。"""
    return await _wrap_async(led_service.ping)


@router.post("/start")
async def start_led():
    """开启光圈（默认亮度 32，白光 255,255,255）。"""
    return await _wrap_async(led_service.start)


@router.post("/off")
async def off_led():
    """关闭光圈。"""
    return await _wrap_async(led_service.off)


@router.post("/color")
async def set_led_color(req: LedColorRequest):
    """设置 RGB 颜色。"""
    return await _wrap_async(
        lambda: led_service.set_color(req.r, req.g, req.b)
    )


@router.post("/brightness")
async def set_led_brightness(req: LedBrightnessRequest):
    """设置亮度。"""
    return await _wrap_async(
        lambda: led_service.set_brightness(req.brightness)
    )


@router.get("/diag")
async def led_diag():
    """诊断：返回逻辑地址、当前解析 IP、缓存状态。"""
    diag = get_pi_resolution_diag()
    diag["pi_base_url"] = diag.get("logical_base_url")
    return {"ok": True, "data": diag}