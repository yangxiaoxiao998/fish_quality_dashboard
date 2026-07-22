/**
 * frontend/src/api/calibration.ts
 *
 * 第一阶段：本文件已经切到真实算法 CLI 接口：
 *   - getCalibrationStatus()  -> /api/algorithm/calibration/status
 *   - runCalibrationByImagePath() -> /api/algorithm/calibration
 *
 * 旧的 /api/calibration/status / /api/calibration/save / /api/calibration/clear
 * 已经被后端标记为 legacy；保留函数名兼容，但内部走新接口。
 */
import { http } from '@/api/http'
import {
  getAlgorithmCalibrationStatus,
  runAlgorithmCalibration,
  type AlgorithmCalibrationStatus,
} from '@/api/algorithm'

export type CalibrationStatus = AlgorithmCalibrationStatus

export function getCalibrationStatus() {
  return getAlgorithmCalibrationStatus()
}

export function runCalibrationByImagePath(
  source_image: string,
  camera_id: string = 'default'
) {
  return runAlgorithmCalibration({
    source_image,
    camera_id,
    device: 'cuda',
    overwrite: true,
  })
}

/**
 * Legacy 兼容：saveCalibrationConfig / clearCalibrationConfig 不再用于真实算法流程。
 * 当前阶段保留函数名以避免破坏旧 import，但内部指向新接口或返回明确错误。
 */
export function saveCalibrationConfig(_data: any) {
  console.warn(
    '[calibration.ts] saveCalibrationConfig 已废弃；请改用 runCalibrationByImagePath(source_image)。'
  )
  return Promise.reject(
    new Error(
      'saveCalibrationConfig 已废弃。请使用 runCalibrationByImagePath(source_image) 调用 /api/algorithm/calibration。'
    )
  )
}

export function clearCalibrationConfig() {
  console.warn(
    '[calibration.ts] clearCalibrationConfig 已废弃；后端不再提供清除接口。如需重新标定，请使用 overwrite=true。'
  )
  return Promise.reject(
    new Error(
      'clearCalibrationConfig 已废弃。请使用 runCalibrationByImagePath(..., overwrite=true)。'
    )
  )
}

// 旧 /api/calibration/perspective 上传图片自动计算透视矩阵：已删除。
// 前端若仍调用，会返回 404 / 410。
export function calibrateFromUpload(_file: File) {
  return http.post('/api/calibration/perspective', _file).catch((err) => {
    throw new Error(
      'calibrateFromUpload 已废弃。请使用 runCalibrationByImagePath(source_image=服务器标定图片路径) 调用 /api/algorithm/calibration。原始错误：' +
        (err?.message || String(err))
    )
  })
}
