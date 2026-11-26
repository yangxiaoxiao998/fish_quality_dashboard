/**
 * frontend/src/api/algorithm.ts
 *
 * 真实算法 CLI 对接前端 API 封装。
 *
 * 后端接口文档：
 *   docs/backend_api/ALGORITHM_BACKEND_API.md
 *   docs/backend_api/BACKEND_ALGORITHM_MIGRATION_SUMMARY.md
 *
 * 后端服务入口：
 *   backend/app/services/algorithm_cli_service.py
 *   backend/app/services/algorithm_result_service.py
 */
import { http } from '@/api/http'

// ----------------- 类型 -----------------

export type AlgorithmStatus =
  | 'done'
  | 'failed'
  | 'interrupted'
  | 'processing'
  | 'queued'
  | 'recording'
  | string

export interface AlgorithmCalibrationDetail {
  enabled?: boolean
  camera_id?: string
  calibration_id?: string
  source_image_path?: string | null
  source_image_url?: string | null
  belt_mask_path?: string | null
  belt_mask_url?: string | null
  rectified_preview_path?: string | null
  rectified_preview_url?: string | null
  transform_matrix?: number[][] | number[] | null
  vertical_distance?: number | null
  pixel_length?: number | null
  mm_per_pixel?: number | null
  image_width?: number | null
  image_height?: number | null
  note?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface AlgorithmCalibrationStatus {
  ok: boolean
  exists: boolean
  calibration_json: string | null
  calibration_url: string | null
  data?: AlgorithmCalibrationDetail | null
  camera_id?: string | null
  updated_at?: string | null
  source_image_url?: string | null
  belt_mask_url?: string | null
  rectified_preview_url?: string | null
  transform_matrix?: number[][] | number[] | null
  vertical_distance?: number | null
  pixel_length?: number | null
  note?: string | null
  error?: string | null
}

export interface RunCalibrationPayload {
  source_image: string
  camera_id?: string
  device?: string
  overwrite?: boolean
  real_length_mm?: number
}

export interface RunCalibrationResp {
  ok: boolean
  calibration_json?: string
  message?: string
  returncode?: number
  log_path?: string
  error_message?: string
}

export interface CaptureCalibrationPayload {
  camera_id?: string
  device?: string
  overwrite?: boolean
  save_only?: boolean
  // 复用"摄像头输入"按钮当前配置
  stream_url?: string
  width?: number
  height?: number
  fps?: number
  // 标定拍摄后自动停止摄像头
  stop_after_capture?: boolean
  real_length_mm?: number
}

export interface CaptureCalibrationResp {
  ok: boolean
  saved?: boolean
  calibrated?: boolean
  message?: string
  source_image_path?: string | null
  source_image_url?: string | null
  calibration_json?: string | null
  calibration_url?: string | null
  adjusted_preview_path?: string | null
  adjusted_preview_url?: string | null
  adjusted_preview_session?: string | null
  adjusted_preview_warning?: string | null
  log_path?: string | null
  returncode?: number | null
  error_message?: string | null
  error_code?: string
  // HTTP 错误时 axios 把 detail 放到 data 上；这里兼容旧字段
  detail?: {
    ok?: boolean
    error_code?: string
    error_message?: string
  }
}

export interface AlgorithmSummary {
  total_fish?: number
  complete_fish?: number
  incomplete_fish?: number
  failed_fish?: number
  damaged_fish?: number
  avg_surface_damage_ratio?: number | null
  max_surface_damage_ratio?: number | null
}

export interface AlgorithmFish {
  video_id: string
  track_id: number | string
  fish_uid?: string
  frame_index?: number
  crop_image_path?: string
  crop_image_url?: string
  body_length_cm?: number | null
  body_length_no_tail_cm?: number | null
  body_height_cm?: number | null
  pred_weight_g?: number | null
  condition_factor?: number | null
  surface_damage_ratio?: number | null
  status?: string
  error_message?: string
  display_fish_id?: number | string
}

export interface AlgorithmVideoResultData {
  video_id: string
  status: AlgorithmStatus
  result_video_url?: string
  grounded_video_url?: string
  measurement_csv_url?: string
  summary?: AlgorithmSummary
  fish?: AlgorithmFish[]
  error_message?: string
  failed_stage?: string
}

export interface AlgorithmVideoResultResp {
  ok: boolean
  data: AlgorithmVideoResultData
}

export interface ErrorConclusionResp {
  ok: boolean
  video_id: string
  text: string
}

// ----------------- 接口封装 -----------------

export function getAlgorithmCalibrationStatus() {
  return http.get<AlgorithmCalibrationStatus>('/api/algorithm/calibration/status')
}

export function getCalibrationConfig() {
  return http.get<{ ok: boolean; real_length_mm: number }>('/api/algorithm/calibration/config')
}

export function saveCalibrationConfig(real_length_mm: number) {
  return http.post<{ ok: boolean; real_length_mm: number }>(
    '/api/algorithm/calibration/config',
    { real_length_mm },
  )
}

export function runAlgorithmCalibration(payload: RunCalibrationPayload) {
  return http.post<RunCalibrationResp>(
    '/api/algorithm/calibration',
    {
      camera_id: 'default',
      device: 'cuda',
      overwrite: true,
      ...payload,
    },
    { timeout: 30 * 60 * 1000 }
  )
}

export function getAlgorithmVideoResult(videoId: string) {
  return http.get<AlgorithmVideoResultResp>(
    `/api/algorithm/videos/${encodeURIComponent(videoId)}/result`
  )
}

export function getAlgorithmErrorConclusion(videoId: string) {
  return http.get<ErrorConclusionResp>(
    `/api/algorithm/videos/${encodeURIComponent(videoId)}/error-conclusion`
  )
}

export function importAlgorithmResult(videoId: string) {
  return http.post(
    `/api/algorithm/import-result/${encodeURIComponent(videoId)}`
  )
}

export function captureAndRunCalibration(
  payload: CaptureCalibrationPayload = {}
) {
  return http.post<CaptureCalibrationResp>(
    '/api/algorithm/calibration/capture',
    {
      camera_id: 'default',
      device: 'cuda',
      overwrite: true,
      save_only: false,
      stop_after_capture: true,
      ...payload,
    },
    { timeout: 30 * 60 * 1000 }
  )
}

export function captureAndSaveCalibrationFrame(
  payload: CaptureCalibrationPayload = {}
) {
  return captureAndRunCalibration({ ...payload, save_only: true })
}

// 标定抓帧 / 标定执行耗时较长，必须使用长 timeout，避免默认 15000ms 超时。
export const CALIBRATION_CAPTURE_TIMEOUT_MS = 5 * 60 * 1000
export const CALIBRATION_RUN_TIMEOUT_MS = 30 * 60 * 1000

export interface CaptureCalibrationFramePayload {
  stream_url?: string
  timeout?: number
  width?: number
  height?: number
  fps?: number
  stop_after_capture?: boolean
}

export interface CaptureCalibrationFrameResp {
  ok: boolean
  saved?: boolean
  source_image_path?: string | null
  source_image_url?: string | null
  error_code?: string
  error_message?: string | null
  message?: string
}

/**
 * 独立抓拍一张标定帧：只走 /api/algorithm/calibration/capture-frame，
 * 内部会自动通知树莓派推流 + 抓帧 + 自动停止，且写入 current_calibration.json 旁的 JPG。
 *
 * 显式使用 5 分钟 timeout，覆盖 http.ts 全局 axios 默认 15000ms。
 */
export function captureCalibrationFrame(
  payload: CaptureCalibrationFramePayload = {}
) {
  return http.post<CaptureCalibrationFrameResp>(
    '/api/algorithm/calibration/capture-frame',
    {
      timeout: 30,
      stop_after_capture: true,
      ...payload,
    },
    { timeout: CALIBRATION_CAPTURE_TIMEOUT_MS }
  )
}

// ----------------- 辅助函数 -----------------

export function formatPercentFromRatio(
  value?: number | null,
  digits = 2
): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `${(Number(value) * 100).toFixed(digits)}%`
}

export function formatNumber(value?: number | null, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return Number(value).toFixed(digits)
}

export function isAlgorithmDone(respOrData: any): boolean {
  if (!respOrData) return false
  return respOrData.ok === true && respOrData.status === 'done'
}

export function algorithmStatusLabel(status?: AlgorithmStatus | null): string {
  switch (status) {
    case 'done':
      return '已完成'
    case 'failed':
      return '失败'
    case 'interrupted':
      return '已中断'
    case 'processing':
      return '处理中'
    case 'queued':
      return '排队中'
    case 'recording':
      return '录制中'
    default:
      return status || '未知'
  }
}
