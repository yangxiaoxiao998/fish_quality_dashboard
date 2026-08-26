<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { Message } from "@arco-design/web-vue";
import { toErrorMessage } from "@/api/http";
import {
  browseUpdatePackageFiles,
  createRollbackTask,
  createUpdateTask,
  getUpdateSummary,
  getUpdateTask,
  getUpdateTaskLog,
  getUpgradeBackups,
  getUpgradeManagerInfo,
  getUpgradeManagerStatus,
  importUpdatePackageFromPath,
  normalizeUpgradeManagerBaseUrl,
  precheckUpdatePackage,
  type BackupEntry,
  type UpdatePackageBrowserResp,
  type UpdatePackageBrowserRoot,
  type UpdateSummaryResp,
  type UpgradeManagerInfoResp,
  type UpgradeManagerStatusResp,
  type UpgradePrecheckResp,
  type UpgradeTaskState,
} from "@/api/system";

const summary = ref<UpdateSummaryResp | null>(null);
const managerInfo = ref<UpgradeManagerInfoResp | null>(null);
const managerStatus = ref<UpgradeManagerStatusResp | null>(null);
const backups = ref<BackupEntry[]>([]);
const precheck = ref<UpgradePrecheckResp | null>(null);
const currentTask = ref<UpgradeTaskState | null>(null);
const selectedPackage = ref<{ name: string; path: string; size: string } | null>(null);
const localPackagePath = ref("");
const browseVisible = ref(false);
const browseState = ref<UpdatePackageBrowserResp | null>(null);

const refreshing = ref(false);
const importing = ref(false);
const browsing = ref(false);
const taskSubmitting = ref(false);
const confirmVisible = ref(false);
const logs = ref("");

const FINAL_TASK_STATUSES = new Set(["success", "rolled_back", "failed"]);
let pollTimer: ReturnType<typeof window.setInterval> | null = null;

const managerBaseUrl = computed(() => normalizeUpgradeManagerBaseUrl(managerInfo.value));
const managerReady = computed(() => Boolean(managerBaseUrl.value));
const taskRunning = computed(() => {
  const status = currentTask.value?.status;
  return !!status && !FINAL_TASK_STATUSES.has(status);
});
const componentNames = computed(() => {
  const items = precheck.value?.components || [];
  if (items.length > 0) return items.map((item) => item.name).join(", ");
  return precheck.value?.updated_targets.join(", ") || "--";
});
const archiveNames = computed(() => {
  const items = precheck.value?.components || [];
  if (items.length > 0) return items.map((item) => item.archive).join(", ");
  return "--";
});
const currentLinks = computed(() => managerStatus.value?.current_links || {});

function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

function taskStatusColor(status?: string | null) {
  if (!status) return "gray";
  if (status === "success") return "green";
  if (status === "rolled_back") return "orange";
  if (status === "failed" || status === "precheck_failed") return "red";
  return "arcoblue";
}

function taskStatusLabel(status?: string | null) {
  const mapping: Record<string, string> = {
    uploaded: "已导入",
    prechecking: "预检中",
    precheck_failed: "预检失败",
    queued: "排队中",
    running: "执行中",
    backing_up: "备份中",
    applying: "应用更新中",
    verifying: "健康检查中",
    success: "成功",
    rollbacking: "回滚中",
    rolled_back: "已回滚",
    failed: "失败",
  };
  return mapping[status || ""] || status || "--";
}

function rootLabel(root: UpdatePackageBrowserRoot) {
  if ((root.kind === "usb" || root.kind === "drive") && root.free && root.total) {
    return `${root.label} (${root.free} / ${root.total})`;
  }
  return root.label;
}

async function refreshSummary() {
  try {
    const response = await getUpdateSummary();
    summary.value = response.data;
  } catch (err) {
    summary.value = null;
    throw err;
  }
}

async function refreshManagerInfo() {
  try {
    const response = await getUpgradeManagerInfo();
    managerInfo.value = response.data;
  } catch (err) {
    managerInfo.value = null;
    managerStatus.value = null;
    backups.value = [];
    throw err;
  }
}

async function refreshManagerStatus() {
  if (!managerReady.value) {
    managerStatus.value = null;
    return;
  }
  const response = await getUpgradeManagerStatus(managerBaseUrl.value);
  managerStatus.value = response.data;
  const activeTask = response.data.active_task;
  if (activeTask?.task_id) {
    currentTask.value = activeTask;
    startPolling(activeTask.task_id);
  }
}

async function refreshBackups() {
  if (!managerReady.value) {
    backups.value = [];
    return;
  }
  const response = await getUpgradeBackups(managerBaseUrl.value);
  backups.value = response.data.items;
}

async function refreshAll() {
  refreshing.value = true;
  try {
    await Promise.all([refreshSummary(), refreshManagerInfo()]);
    if (managerReady.value) {
      await Promise.all([refreshManagerStatus(), refreshBackups()]);
    }
  } catch (err) {
    Message.error("刷新软件更新信息失败：" + toErrorMessage(err));
  } finally {
    refreshing.value = false;
  }
}

async function pollTask(taskId: string) {
  try {
    const [taskResp, logResp] = await Promise.all([
      getUpdateTask(managerBaseUrl.value, taskId),
      getUpdateTaskLog(managerBaseUrl.value, taskId),
    ]);
    currentTask.value = taskResp.data.task;
    logs.value = logResp.data.log || "";

    if (FINAL_TASK_STATUSES.has(taskResp.data.task.status)) {
      stopPolling();
      await Promise.all([refreshSummary(), refreshManagerStatus(), refreshBackups()]);
      if (taskResp.data.task.status === "success") {
        Message.success(taskResp.data.task.message || "升级任务执行成功");
      } else if (taskResp.data.task.status === "rolled_back") {
        Message.warning(taskResp.data.task.message || "升级失败，已自动回滚");
      } else {
        Message.error(taskResp.data.task.message || "任务执行失败");
      }
    }
  } catch (err) {
    stopPolling();
    Message.error("轮询升级任务失败：" + toErrorMessage(err));
  }
}

function startPolling(taskId: string) {
  stopPolling();
  void pollTask(taskId);
  pollTimer = window.setInterval(() => {
    void pollTask(taskId);
  }, 1500);
}

async function runImportFromPath(sourcePath: string) {
  if (importing.value || taskRunning.value || taskSubmitting.value) return;
  if (!managerReady.value) {
    Message.error("Upgrade Manager 不可用，无法导入更新包");
    return;
  }

  const normalizedPath = sourcePath.trim();
  if (!normalizedPath) {
    Message.error("请输入本机或 U 盘上的更新包路径");
    return;
  }

  importing.value = true;
  try {
    const importResp = await importUpdatePackageFromPath(managerBaseUrl.value, {
      source_path: normalizedPath,
    });
    const importedPath = importResp.data.path || importResp.data.package_path || "";
    const importedName =
      importResp.data.name ||
      importResp.data.package_name ||
      importedPath.split(/[\\/]/).pop() ||
      "update_package.tar.xz";

    if (!importedPath) {
      throw new Error("导入接口没有返回可用的 package_path");
    }

    selectedPackage.value = {
      name: importedName,
      path: importedPath,
      size: importResp.data.size || `${importResp.data.size_bytes ?? "--"}`,
    };

    const precheckResp = await precheckUpdatePackage(managerBaseUrl.value, {
      package_path: importedPath,
    });
    precheck.value = precheckResp.data;
    logs.value = "";
    Message.success("本机更新包导入并预检成功");
    await Promise.all([refreshManagerStatus(), refreshBackups()]);
  } catch (err) {
    selectedPackage.value = null;
    precheck.value = null;
    Message.error("导入或预检失败：" + toErrorMessage(err));
  } finally {
    importing.value = false;
  }
}

async function onImportLocalPackage() {
  await runImportFromPath(localPackagePath.value);
}

async function loadBrowseState(path?: string) {
  if (!managerReady.value) {
    Message.error("Upgrade Manager 不可用，无法浏览本机更新包");
    return;
  }

  browsing.value = true;
  try {
    const response = await browseUpdatePackageFiles(managerBaseUrl.value, path);
    browseState.value = response.data;
  } catch (err) {
    Message.error("加载本机目录失败：" + toErrorMessage(err));
  } finally {
    browsing.value = false;
  }
}

async function onOpenBrowseModal() {
  if (importing.value || browsing.value || taskRunning.value || taskSubmitting.value) return;
  browseVisible.value = true;
  await loadBrowseState(localPackagePath.value.trim() || undefined);
}

async function onEnterBrowseDirectory(path: string) {
  if (browsing.value) return;
  await loadBrowseState(path);
}

async function onSelectBrowseFile(path: string) {
  localPackagePath.value = path;
  await runImportFromPath(path);
  if (selectedPackage.value) {
    browseVisible.value = false;
  }
}

function onRemove() {
  selectedPackage.value = null;
  precheck.value = null;
  logs.value = "";
  currentTask.value = null;
  localPackagePath.value = "";
  stopPolling();
}

function onConfirmRun() {
  if (!selectedPackage.value || !precheck.value) {
    Message.error("请先导入并通过预检");
    return;
  }
  confirmVisible.value = true;
}

async function onRealRun() {
  if (!selectedPackage.value || !managerReady.value) {
    Message.error("没有可执行的更新包");
    return;
  }

  confirmVisible.value = false;
  taskSubmitting.value = true;
  logs.value = "";
  currentTask.value = null;
  try {
    const response = await createUpdateTask(managerBaseUrl.value, {
      package_path: selectedPackage.value.path,
    });
    currentTask.value = response.data.task;
    Message.success(response.data.task.message || "升级任务已启动");
    startPolling(response.data.task.task_id);
    await refreshManagerStatus();
  } catch (err) {
    Message.error("创建升级任务失败：" + toErrorMessage(err));
  } finally {
    taskSubmitting.value = false;
  }
}

async function onRollback(backup: BackupEntry) {
  if (!managerReady.value) {
    Message.error("Upgrade Manager 不可用，无法执行回滚");
    return;
  }
  const confirmed = window.confirm(`确认基于备份 ${backup.name} 执行回滚吗？`);
  if (!confirmed) return;

  taskSubmitting.value = true;
  logs.value = "";
  currentTask.value = null;
  try {
    const response = await createRollbackTask(managerBaseUrl.value, {
      backup_dir: backup.path,
    });
    currentTask.value = response.data.task;
    Message.success(response.data.task.message || "回滚任务已启动");
    startPolling(response.data.task.task_id);
    await refreshManagerStatus();
  } catch (err) {
    Message.error("创建回滚任务失败：" + toErrorMessage(err));
  } finally {
    taskSubmitting.value = false;
  }
}

onMounted(() => {
  void refreshAll();
});

onBeforeUnmount(() => {
  stopPolling();
});
</script>

<template>
  <div class="software-update">
    <div class="card">
      <div class="card-header">
        <div class="card-title">软件更新</div>
        <a-button :loading="refreshing" @click="refreshAll">刷新状态</a-button>
      </div>

      <a-descriptions v-if="summary" :column="2" bordered size="small" class="meta">
        <a-descriptions-item label="当前版本">
          <a-tag color="arcoblue">{{ summary.current_version }}</a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="Upgrade Manager">
          <a-tag :color="managerInfo?.healthy ? 'green' : 'orange'">
            {{ managerInfo?.healthy ? '可用' : '不可用' }}
          </a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="项目根目录">
          <code>{{ managerStatus?.install_root || '--' }}</code>
        </a-descriptions-item>
        <a-descriptions-item label="当前配置版本">
          {{ managerStatus?.current_config_version ?? '--' }}
        </a-descriptions-item>
      </a-descriptions>

      <a-alert
        v-if="managerInfo"
        :type="managerInfo.healthy ? 'success' : 'warning'"
        style="margin-top: 12px;"
      >
        Upgrade Manager：<code>{{ managerBaseUrl || '--' }}</code>
        <span v-if="managerInfo.error" style="margin-left: 8px;">{{ managerInfo.error }}</span>
      </a-alert>

      <a-alert v-if="managerStatus?.current_links" type="info" style="margin-top: 12px;">
        backend：<code>{{ currentLinks.backend || '--' }}</code><br />
        frontend：<code>{{ currentLinks.frontend || '--' }}</code><br />
        algo-env：<code>{{ currentLinks['algo-env'] || '--' }}</code>
      </a-alert>
    </div>

    <div class="card">
      <div class="card-header">
        <div class="card-title">选择本机更新包</div>
        <a-tag v-if="currentTask" :color="taskStatusColor(currentTask.status)">
          {{ taskStatusLabel(currentTask.status) }}
        </a-tag>
      </div>

      <a-alert type="info" style="margin-bottom: 12px;">
        此页面不会上传压缩包内容，只会读取本机、挂载磁盘或 U 盘上的更新包路径，再由本机 Upgrade Manager 执行预检和更新。
      </a-alert>

      <div class="import-row">
        <a-input
          v-model="localPackagePath"
          allow-clear
          placeholder="输入本机或 U 盘上的更新包绝对路径，例如 /mnt/usb/update_package_v1.0.0.tar.xz"
          :disabled="importing || taskRunning || taskSubmitting"
          class="path-input"
        />
        <a-button
          type="primary"
          :loading="importing"
          :disabled="taskRunning || taskSubmitting"
          @click="onImportLocalPackage"
        >
          读取本机路径
        </a-button>
        <a-button
          :disabled="importing || taskRunning || taskSubmitting"
          @click="onOpenBrowseModal"
        >
          浏览选择
        </a-button>
        <a-button
          v-if="selectedPackage"
          :disabled="importing || taskRunning || taskSubmitting"
          @click="onRemove"
        >
          清除已导入包
        </a-button>
        <a-button
          v-if="selectedPackage"
          type="primary"
          status="warning"
          :loading="taskSubmitting"
          :disabled="taskRunning"
          @click="onConfirmRun"
        >
          创建升级任务
        </a-button>
      </div>

      <a-alert v-if="selectedPackage" type="info" style="margin-top: 12px;">
        已记录本机更新包路径：<code>{{ selectedPackage.name }}</code>（{{ selectedPackage.size }}）
      </a-alert>

      <div v-if="precheck" class="precheck-block">
        <div class="section-title">预检结果</div>
        <a-descriptions :column="2" bordered size="small">
          <a-descriptions-item label="当前版本">
            {{ precheck.current_version }}
          </a-descriptions-item>
          <a-descriptions-item label="目标版本">
            <a-tag color="green">{{ precheck.new_version }}</a-tag>
          </a-descriptions-item>
          <a-descriptions-item label="执行器">
            <code>{{ precheck.updater_script }}</code>
          </a-descriptions-item>
          <a-descriptions-item label="更新目标">
            {{ precheck.updated_targets.join(', ') || '--' }}
          </a-descriptions-item>
          <a-descriptions-item label="组件列表">
            {{ componentNames }}
          </a-descriptions-item>
          <a-descriptions-item label="归档文件">
            {{ archiveNames }}
          </a-descriptions-item>
          <a-descriptions-item label="最低配置版本">
            {{ precheck.min_config_version }}
          </a-descriptions-item>
          <a-descriptions-item label="当前配置版本">
            {{ precheck.current_config_version }}
          </a-descriptions-item>
          <a-descriptions-item label="包格式版本">
            {{ precheck.bundle_format_version }}
          </a-descriptions-item>
          <a-descriptions-item label="健康检查">
            <code>{{ precheck.healthcheck }}</code>
          </a-descriptions-item>
        </a-descriptions>
      </div>

      <div v-if="currentTask" class="precheck-block">
        <div class="section-title">当前任务</div>
        <a-descriptions :column="2" bordered size="small">
          <a-descriptions-item label="任务 ID">
            <code>{{ currentTask.task_id }}</code>
          </a-descriptions-item>
          <a-descriptions-item label="任务类型">
            {{ currentTask.task_type }}
          </a-descriptions-item>
          <a-descriptions-item label="状态">
            <a-tag :color="taskStatusColor(currentTask.status)">
              {{ taskStatusLabel(currentTask.status) }}
            </a-tag>
          </a-descriptions-item>
          <a-descriptions-item label="目标版本">
            {{ currentTask.target_version || '--' }}
          </a-descriptions-item>
          <a-descriptions-item label="备份目录">
            <code>{{ currentTask.backup_dir || '--' }}</code>
          </a-descriptions-item>
          <a-descriptions-item label="任务消息">
            {{ currentTask.message || currentTask.error_message || '--' }}
          </a-descriptions-item>
        </a-descriptions>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div class="card-title">备份与回滚</div>
        <a-button size="small" :disabled="!managerReady" @click="refreshBackups">刷新备份</a-button>
      </div>

      <a-table
        :data="backups"
        :pagination="{ pageSize: 5 }"
        size="small"
        :bordered="false"
        row-key="path"
      >
        <template #columns>
          <a-table-column title="备份目录" data-index="name" />
          <a-table-column title="状态" data-index="status">
            <template #cell="{ record }">
              <a-tag :color="record.status === 'completed' ? 'green' : 'orange'">
                {{ record.status || '--' }}
              </a-tag>
            </template>
          </a-table-column>
          <a-table-column title="版本变化">
            <template #cell="{ record }">
              {{ record.current_version || '--' }} -> {{ record.new_version || '--' }}
            </template>
          </a-table-column>
          <a-table-column title="组件">
            <template #cell="{ record }">
              {{ record.targets?.join(', ') || '--' }}
            </template>
          </a-table-column>
          <a-table-column title="操作" :width="120">
            <template #cell="{ record }">
              <a-button
                size="mini"
                status="warning"
                :disabled="taskRunning || taskSubmitting || !managerReady"
                @click="onRollback(record)"
              >
                回滚
              </a-button>
            </template>
          </a-table-column>
        </template>
      </a-table>
    </div>

    <div class="card">
      <div class="card-header">
        <div class="card-title">任务日志</div>
        <a-button size="small" :disabled="!logs" @click="logs = ''">清空日志</a-button>
      </div>
      <pre class="log-box">{{ logs || '任务日志会在升级或回滚任务创建后显示。' }}</pre>
    </div>

    <a-modal
      v-model:visible="confirmVisible"
      title="确认创建升级任务"
      ok-text="创建任务"
      cancel-text="取消"
      :ok-button-props="{ loading: taskSubmitting, disabled: taskSubmitting || taskRunning, status: 'warning' }"
      @ok="onRealRun"
      @cancel="() => (confirmVisible = false)"
    >
      <a-alert type="warning" style="margin-bottom: 12px;">
        这次更新会先备份当前项目中的目标目录，再把源码与前端静态产物覆盖到原目录；如果健康检查失败，会自动按备份回滚。
      </a-alert>
      <div v-if="selectedPackage">
        包名：<code>{{ selectedPackage.name }}</code><br />
        路径：<code style="word-break: break-all;">{{ selectedPackage.path }}</code>
      </div>
      <div v-if="precheck" style="margin-top: 12px;">
        版本：{{ precheck.current_version }} -> {{ precheck.new_version }}<br />
        组件：{{ componentNames }}<br />
        最低配置版本：{{ precheck.min_config_version }}<br />
        当前配置版本：{{ precheck.current_config_version }}
      </div>
    </a-modal>

    <a-modal
      v-model:visible="browseVisible"
      title="浏览本机、挂载磁盘或 U 盘中的更新包"
      :footer="false"
      width="980px"
      :mask-closable="!browsing"
      unmount-on-close
    >
      <div class="browser-toolbar">
        <div class="browser-roots">
          <a-button
            v-for="root in browseState?.roots || []"
            :key="root.path"
            size="small"
            :disabled="browsing"
            @click="onEnterBrowseDirectory(root.path)"
          >
            {{ rootLabel(root) }}
          </a-button>
        </div>
        <div class="browser-actions">
          <a-button
            size="small"
            :disabled="browsing || !browseState?.parent_path"
            @click="browseState?.parent_path && onEnterBrowseDirectory(browseState.parent_path)"
          >
            上一级
          </a-button>
          <a-button
            size="small"
            :loading="browsing"
            :disabled="browsing"
            @click="loadBrowseState(browseState?.current_path)"
          >
            刷新
          </a-button>
        </div>
      </div>

      <a-input
        :model-value="browseState?.current_path || ''"
        readonly
        class="browser-current-path"
      />

      <a-spin :loading="browsing" class="browser-spin">
        <div class="browser-grid">
          <div class="browser-panel">
            <div class="section-title">目录</div>
            <div v-if="browseState?.directories.length" class="browser-list">
              <button
                v-for="dir in browseState.directories"
                :key="dir.path"
                type="button"
                class="browser-entry browser-entry-dir"
                @click="onEnterBrowseDirectory(dir.path)"
              >
                <span class="browser-entry-name">{{ dir.name }}</span>
                <span class="browser-entry-meta">{{ dir.path }}</span>
              </button>
            </div>
            <a-empty v-else description="当前目录下没有可浏览的子目录" />
          </div>

          <div class="browser-panel">
            <div class="section-title">tar.xz 更新包</div>
            <div v-if="browseState?.files.length" class="browser-list">
              <button
                v-for="file in browseState.files"
                :key="file.path"
                type="button"
                class="browser-entry browser-entry-file"
                @click="onSelectBrowseFile(file.path)"
              >
                <span class="browser-entry-name">{{ file.name }}</span>
                <span class="browser-entry-meta">{{ file.size || '--' }} · {{ file.path }}</span>
              </button>
            </div>
            <a-empty v-else description="当前目录下没有 .tar.xz / .txz 更新包" />
          </div>
        </div>
      </a-spin>
    </a-modal>
  </div>
</template>

<style lang="scss" scoped>
.software-update {
  padding: 20px;
  background: #f7f8fa;
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 100%;
}
.card {
  background: #fff;
  padding: 20px;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgb(0 0 0 / 6%);
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  gap: 12px;
}
.card-title {
  font-size: 15px;
  font-weight: 600;
  color: #1d2129;
}
.meta {
  margin-bottom: 12px;
}
.import-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.path-input {
  min-width: 420px;
  flex: 1;
}
.precheck-block {
  margin-top: 16px;
}
.section-title {
  margin-bottom: 8px;
  color: #4e5969;
  font-weight: 600;
}
.log-box {
  margin: 0;
  padding: 12px;
  background: #0d1117;
  color: #d1d5da;
  border-radius: 6px;
  font-family: SarasaMonoSC, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  max-height: 480px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.browser-toolbar {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.browser-roots,
.browser-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.browser-current-path {
  margin-bottom: 12px;
}
.browser-spin {
  width: 100%;
}
.browser-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.browser-panel {
  min-height: 320px;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  padding: 12px;
  background: #fafafa;
}
.browser-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 420px;
  overflow: auto;
}
.browser-entry {
  width: 100%;
  text-align: left;
  border: 1px solid #e5e6eb;
  background: #fff;
  border-radius: 8px;
  padding: 10px 12px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.browser-entry:hover {
  border-color: #4080ff;
  box-shadow: 0 0 0 1px rgb(64 128 255 / 12%);
}
.browser-entry-name {
  font-weight: 600;
  color: #1d2129;
  word-break: break-all;
}
.browser-entry-meta {
  font-size: 12px;
  color: #86909c;
  word-break: break-all;
}

@media (width <= 960px) {
  .browser-grid {
    grid-template-columns: 1fr;
  }
}
</style>
