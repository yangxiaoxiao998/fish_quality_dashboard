// frontend/src/api/led.ts
//
// LED 光圈控制 - 前端 API 层
//
// 重要：所有请求只走 /api/led/xxx，由 backend/app/routers/led_router.py 代理
// 到树莓派。前端绝不直接访问 raspberrypi.local。

import { http } from '@/api/http'

export interface LedColorPayload {
  r: number
  g: number
  b: number
}

export interface LedBrightnessPayload {
  brightness: number
}

export interface LedResponse<T = unknown> {
  ok: boolean
  pi_base_url: string
  data: T
}

/** 检查树莓派服务是否可达。*/
export function pingLed() {
  return http.get<LedResponse>('/api/led/ping')
}

/** 开启光圈（默认亮度 32，白光 255,255,255）。*/
export function startLed() {
  return http.post<LedResponse>('/api/led/start')
}

/** 关闭光圈。*/
export function offLed() {
  return http.post<LedResponse>('/api/led/off')
}

/** 设置 RGB 颜色（每通道 0~255）。*/
export function setLedColor(payload: LedColorPayload) {
  return http.post<LedResponse>('/api/led/color', payload)
}

/** 设置亮度（0~255）。*/
export function setLedBrightness(payload: LedBrightnessPayload) {
  return http.post<LedResponse>('/api/led/brightness', payload)
}