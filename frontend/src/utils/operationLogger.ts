import { http } from '@/api/http'

interface OperationActionPayload {
  module: string
  action: string
  targetType?: string
  targetId?: string | number
  message?: string
  status?: 'success' | 'failed'
}

export async function logUserAction(payload: OperationActionPayload): Promise<void> {
  try {
    await http.post('/api/operation-logs', {
      module: payload.module,
      action: payload.action,
      target_type: payload.targetType || 'ui',
      target_id: payload.targetId == null ? '' : String(payload.targetId),
      message: payload.message || '',
      status: payload.status || 'success',
    })
  } catch (err) {
    console.warn('[operationLogger] log failed:', err)
  }
}
