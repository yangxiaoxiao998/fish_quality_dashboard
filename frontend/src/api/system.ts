import axios from "axios";
import { http } from "@/api/http";

const UPDATE_PRECHECK_TIMEOUT_MS = Number(import.meta.env.VITE_UPDATE_PRECHECK_TIMEOUT_MS ?? 60 * 60 * 1000);
const UPGRADE_MANAGER_TIMEOUT_MS = 15 * 1000;

export interface UsbMount {
  path: string;
  free: string;
  total: string;
}

export interface BackupStatusResp {
  ok: boolean;
  usb_inserted: boolean;
  usb_mounts: UsbMount[];
  default_backup_dir: string;
  latest_backup: string | null;
  all_backups: string[];
  project_root?: string;
  backup_script_exists?: boolean;
}

export interface BackupPreviewResp {
  ok: boolean;
  target_dir: string;
  already_backed_up: string[];
  new_backup_name: string;
  new_backup_path: string;
  estimated_size_bytes: number;
  estimated_size: string;
  existing_data_dirs: string[];
  missing_data_dirs: string[];
}

export interface BackupRunResp {
  ok: boolean;
  returncode: number;
  stdout: string;
  stderr: string;
  duration_seconds?: number;
  all_backups?: string[];
  message: string;
  new_backup_path?: string | null;
  target_dir?: string;
  already_backed_up?: string[];
  new_backup_name?: string;
  estimated_size_bytes?: number;
  estimated_size?: string;
  existing_data_dirs?: string[];
  missing_data_dirs?: string[];
}

export interface UploadedPackage {
  name: string;
  path: string;
  size_bytes: number;
  size: string;
  mtime: string;
}

export interface ImportedPackage extends Omit<UploadedPackage, "name" | "path"> {
  source_path?: string;
  package_name?: string;
  package_path?: string;
  name?: string;
  path?: string;
}

export interface UpdatePackageBrowserRoot {
  label: string;
  path: string;
  kind: string;
  free?: string;
  total?: string;
}

export interface UpdatePackageBrowserEntry {
  name: string;
  path: string;
  size_bytes?: number;
  size?: string;
  mtime?: string;
}

export interface UpdatePackageBrowserResp {
  ok: boolean;
  current_path: string;
  parent_path: string | null;
  roots: UpdatePackageBrowserRoot[];
  directories: UpdatePackageBrowserEntry[];
  files: UpdatePackageBrowserEntry[];
}

export interface UpdateStatusResp {
  ok: boolean;
  current_version: string;
  version_file: string;
  version_file_exists: boolean;
  updater_script: string;
  updater_script_exists: boolean;
  uploaded_packages: UploadedPackage[];
}

export interface UpdateSummaryResp {
  ok: boolean;
  current_version: string;
  version_file: string;
  version_file_exists: boolean;
  updater_script: string;
  updater_script_exists: boolean;
}

export interface UpgradeManagerInfoResp {
  ok: boolean;
  base_url: string;
  api_base_url: string;
  available: boolean;
  healthy: boolean;
  error: string | null;
  health: Record<string, any> | null;
}

export interface UpgradeTaskState {
  task_id: string;
  task_type: "update" | "rollback";
  package_path?: string | null;
  backup_dir?: string | null;
  current_version?: string | null;
  target_version?: string | null;
  status:
    | "uploaded"
    | "prechecking"
    | "precheck_failed"
    | "queued"
    | "running"
    | "backing_up"
    | "applying"
    | "verifying"
    | "success"
    | "rollbacking"
    | "rolled_back"
    | "failed";
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  updated_targets: string[];
  error_message?: string | null;
  message?: string | null;
  runner_pid?: number | null;
  metadata?: Record<string, any>;
}

export interface UpgradeManagerStatusResp {
  ok: boolean;
  current_version: string;
  version_file: string;
  version_file_exists: boolean;
  updater_script: string;
  updater_script_exists: boolean;
  uploaded_packages: UploadedPackage[];
  active_task?: UpgradeTaskState | null;
  latest_task?: UpgradeTaskState | null;
  install_root?: string;
  config_root?: string;
  data_root?: string;
  state_root?: string;
  current_config_version?: number;
  current_links?: Record<string, string | null>;
}

export interface UpgradePrecheckComponent {
  name: string;
  archive: string;
  target_subdir: string;
  current_link: string;
}

export interface UpgradePrecheckResp {
  ok: boolean;
  package_path: string;
  current_version: string;
  new_version: string;
  package_root: string;
  updated_targets: string[];
  updater_script: string;
  components: UpgradePrecheckComponent[];
  includes_algo_env: boolean;
  min_config_version: number;
  current_config_version: number;
  bundle_format_version: number;
  requires_restart: string[];
  healthcheck: string;
  package_format?: string;
  build_time?: string | null;
  git_commit?: string | null;
  frontend_runtime_mode?: string | null;
  preserve_paths?: string[];
  excluded_paths?: string[];
}

export interface UpgradeTaskResp {
  ok: boolean;
  task: UpgradeTaskState;
}

export interface UpgradeTaskLogResp {
  ok: boolean;
  task_id: string;
  log: string;
}

export interface BackupEntry {
  name: string;
  path: string;
  mtime: string;
  status?: string;
  current_version?: string;
  new_version?: string;
  targets?: string[];
  manifest_error?: string;
}

export interface UpgradeBackupListResp {
  ok: boolean;
  items: BackupEntry[];
}

export interface UpdateRunResp {
  ok: boolean;
  returncode: number;
  stdout: string;
  stderr: string;
  duration_seconds?: number;
  current_version?: string;
  new_version?: string;
  package_path?: string;
  message: string;
  restart_required?: boolean;
}

export function normalizeUpgradeManagerBaseUrl(baseUrl: unknown): string {
  if (typeof baseUrl === "string") {
    return baseUrl.trim().replace(/\/$/, "");
  }
  if (baseUrl && typeof baseUrl === "object") {
    const record = baseUrl as Record<string, unknown>;
    for (const candidate of [
      record.base_url,
      record.baseUrl,
      record.api_base_url,
      record.apiBaseUrl,
    ]) {
      if (typeof candidate === "string" && candidate.trim()) {
        return candidate.trim().replace(/\/$/, "");
      }
    }
  }
  return "";
}

function getUpgradeManagerHttp(baseUrl: unknown) {
  const normalized = normalizeUpgradeManagerBaseUrl(baseUrl);
  if (!normalized) {
    throw new Error("Upgrade Manager 地址无效，请刷新页面后重试");
  }
  return axios.create({
    baseURL: normalized,
    timeout: UPGRADE_MANAGER_TIMEOUT_MS,
    withCredentials: false,
  });
}

export function getBackupStatus() {
  return http.get<BackupStatusResp>("/api/system/backup/status");
}

export function previewBackup(data: { target_dir?: string } = {}) {
  return http.post<BackupPreviewResp>("/api/system/backup/preview", data);
}

export function runBackup(data: { target_dir?: string } = {}) {
  return http.post<BackupRunResp>("/api/system/backup/run", data);
}

export interface RsyncBackupStatusResp {
  ok: boolean;
  configured: boolean;
  source_dir: string;
  target_dir: string;
  interval_seconds: number;
  enabled: boolean;
  scheduler_running: boolean;
  source_exists: boolean;
  target_exists: boolean;
  rsync_exists: boolean;
  last_run: RsyncBackupRunResp | null;
  current_run: { started_at: string; message: string } | null;
  syncing: boolean;
  ejecting: boolean;
}

export interface RsyncBackupRunResp {
  ok: boolean;
  returncode?: number;
  source_dir?: string;
  target_dir?: string;
  log_path?: string;
  stdout?: string;
  stderr?: string;
  duration_seconds?: number;
  finished_at?: string;
  message: string;
  accepted?: boolean;
}

export function getRsyncBackupStatus() {
  return http.get<RsyncBackupStatusResp>("/api/system/backup/rsync/status");
}

export function saveRsyncBackupSettings(data: {
  source_dir: string;
  target_dir: string;
  interval_seconds: number;
  enabled: boolean;
}) {
  return http.put<RsyncBackupStatusResp>("/api/system/backup/rsync/settings", data);
}

export function runRsyncBackup() {
  return http.post<RsyncBackupRunResp>("/api/system/backup/rsync/run");
}

export interface RsyncBackupEjectResp {
  ok: boolean;
  message: string;
  target_dir?: string;
  mount_point?: string;
  source?: string;
  stdout?: string;
  stderr?: string;
  returncode?: number;
}

export function ejectRsyncBackupDisk() {
  return http.post<RsyncBackupEjectResp>("/api/system/backup/rsync/eject");
}

export function getUpdateSummary() {
  return http.get<UpdateSummaryResp>("/api/system/update/summary");
}

export function getUpgradeManagerInfo() {
  return http.get<UpgradeManagerInfoResp>("/api/system/update/manager-info");
}

export function getUpdateStatus() {
  return http.get<UpdateStatusResp>("/api/system/update/status");
}

export function getUpgradeManagerStatus(baseUrl: string) {
  return getUpgradeManagerHttp(baseUrl).get<UpgradeManagerStatusResp>("/api/upgrade/status");
}

export function getUpgradePackages(baseUrl: string) {
  return getUpgradeManagerHttp(baseUrl).get<{ ok: boolean; items: UploadedPackage[] }>("/api/upgrade/packages");
}

export function importUpdatePackageFromPath(baseUrl: string, data: { source_path: string }) {
  return getUpgradeManagerHttp(baseUrl).post<ImportedPackage>("/api/upgrade/packages/import", data);
}

export function browseUpdatePackageFiles(baseUrl: string, path?: string) {
  return getUpgradeManagerHttp(baseUrl).get<UpdatePackageBrowserResp>("/api/upgrade/packages/browse", {
    params: path ? { path } : undefined,
  });
}

export function precheckUpdatePackage(baseUrl: string, data: { package_path: string }) {
  return getUpgradeManagerHttp(baseUrl).post<UpgradePrecheckResp>("/api/upgrade/packages/precheck", data, {
    timeout: UPDATE_PRECHECK_TIMEOUT_MS,
  });
}

export function createUpdateTask(baseUrl: string, data: { package_path: string }) {
  return getUpgradeManagerHttp(baseUrl).post<UpgradeTaskResp>("/api/upgrade/tasks", data);
}

export function getUpdateTask(baseUrl: string, taskId: string) {
  return getUpgradeManagerHttp(baseUrl).get<UpgradeTaskResp>(`/api/upgrade/tasks/${taskId}`);
}

export function getUpdateTaskLog(baseUrl: string, taskId: string) {
  return getUpgradeManagerHttp(baseUrl).get<UpgradeTaskLogResp>(`/api/upgrade/tasks/${taskId}/log`);
}

export function getUpgradeBackups(baseUrl: string) {
  return getUpgradeManagerHttp(baseUrl).get<UpgradeBackupListResp>("/api/upgrade/backups");
}

export function createRollbackTask(baseUrl: string, data: { backup_dir: string }) {
  return getUpgradeManagerHttp(baseUrl).post<UpgradeTaskResp>("/api/upgrade/rollback", data);
}

export function runUpdate(data: { package_path: string }) {
  return http.post<UpdateRunResp>("/api/system/update/run", data);
}
