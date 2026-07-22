// frontend/src/api/opsAlgorithmTasks.ts
//
// 系统运维 -> 算法任务监控 后端 API 封装。
// 全部接口仅供运维使用，普通用户页面继续走原有简单提示。

import { http } from '@/api/http'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface OpsAlgorithmTaskWorker {
  queue: string
  queued_jobs: number
  started_jobs: number
  deferred_jobs: number
  finished_jobs: number
  failed_jobs: number
  redis_ok: boolean
  error_message?: string | null
}

export interface OpsAlgorithmTaskGpu {
  available: boolean
  name?: string
  memory_used_mb?: number
  memory_total_mb?: number
}

export interface OpsAlgorithmTaskModelRuntime {
  mode: 'cli' | 'resident' | string
  preload_supported: boolean
  loaded: boolean
  message: string
  gpu?: OpsAlgorithmTaskGpu
}

export interface OpsAlgorithmTaskSummary {
  today_total: number
  queued: number
  processing: number
  done: number
  failed: number
  stuck: number
  worker: OpsAlgorithmTaskWorker
  model_runtime: OpsAlgorithmTaskModelRuntime
}

export interface OpsAlgorithmTaskItem {
  video_id: string
  source_type: string
  status: string
  display_status: string
  is_stuck: boolean
  stuck_reason: string
  current_stage: string | null
  progress_percent: number
  total_fish: number
  processed_fish: number
  elapsed_seconds: number | null
  eta_seconds: number | null
  eta_text: string
  eta_confidence: 'low' | 'medium' | 'high' | string
  queue_state: string
  has_pipeline_result: boolean
  has_error_conclusion: boolean
  has_grounded_video: boolean
  last_error: string | null
  failed_stage: string | null
  latest_log_path: string | null
  latest_log_returncode: number | null
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

export interface OpsAlgorithmTaskListResp {
  ok: boolean
  total: number
  items: OpsAlgorithmTaskItem[]
}

export interface OpsAlgorithmTaskSummaryResp {
  ok: boolean
  data: OpsAlgorithmTaskSummary
}

export interface OpsAlgorithmTaskStage {
  key: string
  name: string
  status: 'done' | 'running' | 'pending' | 'failed' | 'skipped' | string
  elapsed_seconds: number | null
  input_count: number | null
  output_count: number | null
  error_message: string | null
}

export interface OpsAlgorithmTaskFileInfo {
  exists: boolean
  path?: string
  size?: number
  mtime?: string
  error?: string
  // measurement_csv / 其他多候选文件：可能的真实路径列表
  candidates?: string[]
}

export interface OpsAlgorithmTaskQueue {
  redis_ok: boolean
  queue: string | null
  job_id: string | null
  job_state: string | null
  func_name: string | null
  args_summary: string | null
  started_at: string | null
  ended_at: string | null
  exc_info: string | null
  error_message?: string | null
}

export interface OpsAlgorithmTaskLatestLog {
  path: string | null
  exists: boolean
  returncode: number | null
  command: string | null
  stdout_tail: string
  stderr_tail: string
  mtime: string | null
  parse_error: string | null
  // 兜底来源标识：algorithm_cli | pipeline_state
  source?: string
  message?: string
}

export interface OpsAlgorithmTaskPipelineStateSummary {
  exists: boolean
  path: string
  mtime: string | null
  status: string | null
  current_stage: string | null
  failed_stage: string | null
  error_message: string | null
  completed_stages: string[]
  command: string | null
  raw_tail: string
  message: string
}

export interface OpsAlgorithmTaskLogSource {
  type: 'algorithm_cli' | 'pipeline_state' | 'worker'
  title: string
  exists: boolean
  path?: string | null
  mtime?: string | null
  // algorithm_cli 字段
  returncode?: number | null
  command?: string | null
  stdout_tail?: string
  stderr_tail?: string
  // pipeline_state 字段
  status?: string | null
  current_stage?: string | null
  failed_stage?: string | null
  error_message?: string | null
  completed_stages?: string[]
  raw_tail?: string
  // worker 字段
  matched_lines?: number
  tail?: string
  // 通用
  message?: string
}

export interface OpsAlgorithmTaskDetail {
  video: Record<string, unknown>
  queue: OpsAlgorithmTaskQueue
  files: Record<string, OpsAlgorithmTaskFileInfo | unknown>
  pipeline_state: {
    raw: Record<string, unknown> | null
    parse_error: string | null
    summary?: OpsAlgorithmTaskPipelineStateSummary
  }
  pipeline_result: { raw: Record<string, unknown> | null; parse_error: string | null }
  stages: OpsAlgorithmTaskStage[]
  display_status: string
  is_stuck: boolean
  stuck_reason: string
  current_stage?: string | null
  failed_stage?: string | null
  last_activity_at?: string | null
  last_activity_source?: string
  eta_seconds: number | null
  eta_text: string
  eta_confidence: string
  latest_log: OpsAlgorithmTaskLatestLog
  log_sources: OpsAlgorithmTaskLogSource[]
  diagnosis: { is_stuck: boolean; reason: string; suggestion: string }
  model_runtime: OpsAlgorithmTaskModelRuntime
}

export interface OpsAlgorithmTaskDetailResp {
  ok: boolean
  data: OpsAlgorithmTaskDetail
}

export interface OpsAlgorithmTaskListParams {
  status?: string
  date?: string
  limit?: number
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

export function getOpsAlgorithmTaskSummary() {
  return http.get<OpsAlgorithmTaskSummaryResp>(
    '/api/ops/algorithm-tasks/summary',
  )
}

export function listOpsAlgorithmTasks(
  params: OpsAlgorithmTaskListParams = {},
) {
  return http.get<OpsAlgorithmTaskListResp>(
    '/api/ops/algorithm-tasks',
    { params },
  )
}

export function getOpsAlgorithmTaskDetail(videoId: string) {
  return http.get<OpsAlgorithmTaskDetailResp>(
    `/api/ops/algorithm-tasks/${encodeURIComponent(videoId)}`,
  )
}

export function markOpsAlgorithmTaskFailed(
  videoId: string,
  reason: string,
  force = false,
) {
  return http.post<{ ok: boolean; video_id: string; status: string; last_error: string }>(
    `/api/ops/algorithm-tasks/${encodeURIComponent(videoId)}/mark-failed`,
    { reason, force },
  )
}

export function retryOpsAlgorithmTask(
  videoId: string,
  force = false,
) {
  return http.post<{ ok: boolean; video_id: string; status: string; job_id: string; input_video: string }>(
    `/api/ops/algorithm-tasks/${encodeURIComponent(videoId)}/retry`,
    { force },
  )
}

// ---------------------------------------------------------------------------
// 算法模型运行状态（system ops -> 模型运行状态）
// ---------------------------------------------------------------------------

export interface OpsAlgorithmRuntimeGpu {
  available: boolean
  name?: string
  memory_used_mb?: number
  memory_total_mb?: number
}

export interface OpsAlgorithmRuntimeTorch {
  installed: boolean
  version?: string | null
  cuda_available?: boolean
  cuda_version?: string | null
  device_name?: string | null
  error?: string
}

export interface OpsAlgorithmRuntimeModel {
  key: string
  name: string
  path: string
  exists: boolean
  size_mb: number | null
  source?: string
}

export interface OpsAlgorithmRuntimeData {
  mode: 'cli' | 'resident' | string
  preload_supported: boolean
  loaded: boolean
  message: string
  gpu: OpsAlgorithmRuntimeGpu
  models: OpsAlgorithmRuntimeModel[]
  models_summary: { total: number; found: number; missing: number }
  python: string
  torch: OpsAlgorithmRuntimeTorch
}

export interface OpsAlgorithmRuntimeStatusResp {
  ok: boolean
  data: OpsAlgorithmRuntimeData
}

export interface OpsAlgorithmRuntimePreloadResp {
  ok: boolean
  code?: string
  message?: string
}

export function getOpsAlgorithmRuntimeStatus() {
  return http.get<OpsAlgorithmRuntimeStatusResp>(
    '/api/ops/algorithm-runtime/status',
  )
}

export function postOpsAlgorithmRuntimePreload() {
  return http.post<OpsAlgorithmRuntimePreloadResp>(
    '/api/ops/algorithm-runtime/preload',
    {},
  )
}
