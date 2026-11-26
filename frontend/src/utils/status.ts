// 统一状态文案 / 颜色映射

export const VIDEO_STATUS_TEXT: Record<string, string> = {
  queued: '排队中',
  processing: '处理中',
  done: '已完成',
  failed: '失败',
  no_fish_detected: '未检测到鱼',
  stopped: '已停止',
  pending: '排队中',
  completed: '已完成',
}

export const VIDEO_STATUS_COLOR: Record<string, string> = {
  queued: 'gray',
  processing: 'blue',
  done: 'green',
  failed: 'red',
  no_fish_detected: 'orange',
  stopped: 'orange',
  pending: 'gray',
  completed: 'green',
}

export const CAMERA_STATE_TEXT: Record<string, string> = {
  idle: '未连接',
  starting: '正在连接',
  running: '已连接',
  stopping: '正在停止',
  error: '连接错误',
  connected: '已连接',
  disconnected: '未连接',
  reconnecting: '重新连接',
}

export const CAMERA_STATE_COLOR: Record<string, string> = {
  idle: 'gray',
  starting: 'orange',
  running: 'green',
  stopping: 'orange',
  error: 'red',
  reconnecting: 'orange',
}

export function videoStatusText(status?: string | null): string {
  if (!status) return '-'
  return VIDEO_STATUS_TEXT[status] ?? status
}

export function videoStatusColor(status?: string | null): string {
  if (!status) return 'gray'
  return VIDEO_STATUS_COLOR[status] ?? 'gray'
}

export function cameraStateText(state?: string | null): string {
  if (!state) return '未连接'
  return CAMERA_STATE_TEXT[state] ?? state
}

export function cameraStateColor(state?: string | null): string {
  if (!state) return 'gray'
  return CAMERA_STATE_COLOR[state] ?? 'gray'
}
