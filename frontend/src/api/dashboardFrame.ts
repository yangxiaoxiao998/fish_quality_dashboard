/**
 * frontend/src/api/dashboardFrame.ts
 *
 * 第一阶段已停用：
 *   后端 /api/dashboard/frame/select-fish 接口已删除。
 *   真实算法结果不再支持"点击帧选鱼"，前端应从
 *   /api/algorithm/videos/{video_id}/result 的 fish[] 列表中选择鱼。
 *
 * 为了避免破坏旧 import，本文件保留 selectFishFromFrame 函数名，
 * 但函数被禁用（返回 rejected Promise）。
 */

export interface SelectFishPayload {
  video_id: string
  frame_index: number
  x: number
  y: number
}

export function selectFishFromFrame(_data: SelectFishPayload) {
  return Promise.reject(
    new Error(
      'selectFishFromFrame 已废弃：后端 /api/dashboard/frame/select-fish 接口已删除。' +
        '新版算法结果请从 /api/algorithm/videos/{video_id}/result 的 fish[] 列表按 track_id 选择鱼。'
    )
  )
}
