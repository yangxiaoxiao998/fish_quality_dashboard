// frontend/src/api/system.ts
//
// 后端 /api/system 的前端封装：
//   - 数据备份：status / preview / run
//   - 软件更新：status / upload / run
//
// 注意：所有路径/命令都走后端，前端不直接执行本地脚本。

import { http } from '@/api/http'

export interface UsbMount {
  path: string
  free: string
  total: string
}

export interface BackupStatusResp {
  ok: boolean
  usb_inserted: boolean
  usb_mounts: UsbMount[]
  default_backup_dir: string
  latest_backup: string | null
  all_backups: string[]
  project_root?: string
  backup_script_exists?: boolean
}

export interface BackupPreviewResp {
  ok: boolean
  target_dir: string
  already_backed_up: string[]
  new_backup_name: string
  new_backup_path: string
  estimated_size_bytes: number
  estimated_size: string
  existing_data_dirs: string[]
  missing_data_dirs: string[]
}

export interface BackupRunResp {
  ok: boolean
  returncode: number
  stdout: string
  stderr: string
  duration_seconds?: number
  all_backups?: string[]
  message: string
  // 仅在 ok=true 时存在
  new_backup_path?: string | null
  target_dir?: string
  already_backed_up?: string[]
  new_backup_name?: string
  estimated_size_bytes?: number
  estimated_size?: string
  existing_data_dirs?: string[]
  missing_data_dirs?: string[]
}

export interface UploadedPackage {
  name: string
  path: string
  size_bytes: number
  size: string
  mtime: string
}

export interface UpdateStatusResp {
  ok: boolean
  current_version: string
  version_file: string
  version_file_exists: boolean
  updater_script_exists: boolean
  uploaded_packages: UploadedPackage[]
}

export interface UpdateRunResp {
  ok: boolean
  returncode: number
  stdout: string
  stderr: string
  duration_seconds?: number
  current_version?: string
  new_version?: string
  package_path?: string
  message: string
  restart_required?: boolean
}

// ---- 数据备份 ----
export function getBackupStatus() {
  return http.get<BackupStatusResp>('/api/system/backup/status')
}

export function previewBackup(data: { target_dir?: string } = {}) {
  return http.post<BackupPreviewResp>('/api/system/backup/preview', data)
}

export function runBackup(data: { target_dir?: string } = {}) {
  return http.post<BackupRunResp>('/api/system/backup/run', data)
}

// ---- 软件更新 ----
export function getUpdateStatus() {
  return http.get<UpdateStatusResp>('/api/system/update/status')
}

export function uploadUpdatePackage(file: File) {
  const form = new FormData()
  form.append('file', file)
  return http.post<{
    ok: boolean
    package_name: string
    package_path: string
    size_bytes: number
    size: string
  }>('/api/system/update/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function runUpdate(data: { package_path: string }) {
  return http.post<UpdateRunResp>('/api/system/update/run', data)
}
