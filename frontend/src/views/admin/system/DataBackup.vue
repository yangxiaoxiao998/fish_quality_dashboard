<script setup lang="ts">
import { onMounted, ref } from "vue"
import { Message } from "@arco-design/web-vue"
import {
  ejectRsyncBackupDisk,
  getRsyncBackupStatus,
  runRsyncBackup,
  saveRsyncBackupSettings,
  type RsyncBackupRunResp,
  type RsyncBackupStatusResp,
} from "@/api/system"

const refreshing = ref(false)
const saving = ref(false)
const running = ref(false)
const ejecting = ref(false)
const status = ref<RsyncBackupStatusResp | null>(null)
const runResult = ref<RsyncBackupRunResp | null>(null)
const sourceDir = ref("")
const targetDir = ref("")
const enabled = ref(false)

async function refreshStatus() {
  refreshing.value = true
  try {
    const response = await getRsyncBackupStatus()
    status.value = response.data
    sourceDir.value = response.data.source_dir
    targetDir.value = response.data.target_dir
    enabled.value = response.data.enabled
  } catch (error) {
    Message.error(`刷新备份状态失败：${String(error)}`)
  } finally {
    refreshing.value = false
  }
}

async function saveSettings() {
  saving.value = true
  try {
    const response = await saveRsyncBackupSettings({ source_dir: sourceDir.value, target_dir: targetDir.value, interval_seconds: 3600, enabled: enabled.value })
    status.value = response.data
    Message.success(enabled.value ? "自动备份已启用，每 60 分钟同步一次" : "自动备份设置已保存")
  } catch (error) {
    Message.error(`保存备份设置失败：${String(error)}`)
  } finally {
    saving.value = false
  }
}

async function runNow() {
  if (running.value) return
  running.value = true
  try {
    const response = await runRsyncBackup()
    runResult.value = response.data
    if (!response.data.accepted) {
      Message.error(response.data.message)
      return
    }
    Message.info("同步任务已启动，后台执行中")
    await waitForCompletion()
  } catch (error) {
    Message.error(`执行同步失败：${String(error)}`)
  } finally {
    running.value = false
  }
}

async function ejectDisk() {
  if (status.value?.syncing || running.value) {
    Message.warning("同步正在执行，完成后才能弹出移动硬盘")
    return
  }
  ejecting.value = true
  try {
    const response = await ejectRsyncBackupDisk()
    if (response.data.ok) {
      Message.success(response.data.message)
      await refreshStatus()
    } else Message.error(response.data.message)
  } catch (error) {
    Message.error(`弹出移动硬盘失败：${String(error)}`)
  } finally {
    ejecting.value = false
  }
}

async function waitForCompletion() {
  for (;;) {
    await new Promise((resolve) => window.setTimeout(resolve, 2000))
    const response = await getRsyncBackupStatus()
    status.value = response.data
    if (!response.data.syncing) {
      const result = response.data.last_run
      if (result) {
        runResult.value = result
        result.ok ? Message.success(result.message) : Message.error(result.message)
      }
      return
    }
  }
}

onMounted(refreshStatus)
</script>

<template>
  <div class="data-backup">
    <div class="card">
      <div class="card-header">
        <div class="card-title">自动同步备份（rsync）</div>
        <a-space>
          <a-tag v-if="status?.syncing" color="orange">同步中</a-tag>
          <a-tag v-else-if="status?.scheduler_running" color="green">每 60 分钟运行</a-tag>
          <a-tag v-else color="gray">未启用</a-tag>
          <a-button type="primary" :loading="running" @click="runNow">立即同步</a-button>
          <a-popconfirm content="请确认同步已完成，再弹出移动硬盘" type="warning" @ok="ejectDisk">
            <a-button :loading="ejecting" :disabled="running || status?.syncing">弹出移动硬盘</a-button>
          </a-popconfirm>
        </a-space>
      </div>
      <a-alert v-if="status && !status.rsync_exists" type="error" title="系统未安装 rsync，自动同步不可用" />
      <a-form layout="vertical" class="rsync-form">
        <a-form-item label="备份数据路径"><a-input v-model="sourceDir" placeholder="默认：项目 data 目录" /></a-form-item>
        <a-form-item label="移动硬盘目标路径"><a-input v-model="targetDir" placeholder="默认：自动检测的移动硬盘目录" /></a-form-item>
        <a-form-item label="自动备份间隔"><a-input model-value="60 分钟" disabled /></a-form-item>
        <a-form-item><a-switch v-model="enabled" /><span class="switch-label">启用后台自动备份</span></a-form-item>
        <a-space>
          <a-button type="primary" :loading="saving" @click="saveSettings">保存设置</a-button>
          <a-button :loading="refreshing" @click="refreshStatus">刷新状态</a-button>
        </a-space>
      </a-form>
    </div>
    <div class="card">
      <div class="card-header"><div class="card-title">备份状态</div><a-tag v-if="runResult" :color="runResult.ok ? 'green' : 'red'">{{ runResult.message }}</a-tag></div>
      <a-descriptions v-if="status" :column="2" bordered size="small">
        <a-descriptions-item label="自动备份">{{ status.enabled ? "已启用" : "未启用" }}</a-descriptions-item>
        <a-descriptions-item label="同步间隔">60 分钟</a-descriptions-item>
        <a-descriptions-item label="调度器状态">{{ status.scheduler_running ? "后台运行中" : "已停止" }}</a-descriptions-item>
        <a-descriptions-item label="rsync 状态">{{ status.rsync_exists ? "可用" : "不可用" }}</a-descriptions-item>
        <a-descriptions-item label="数据源状态">{{ status.source_exists ? "正常" : "不存在" }}</a-descriptions-item>
        <a-descriptions-item label="目标硬盘状态">{{ status.target_exists ? "已连接" : "未连接或未创建" }}</a-descriptions-item>
        <a-descriptions-item label="最近一次结果" :span="2">{{ status.last_run?.message || "尚未执行" }}</a-descriptions-item>
      </a-descriptions>
      <a-empty v-else description="正在加载备份状态" />
    </div>
  </div>
</template>

<style lang="scss" scoped>
.data-backup { padding: 20px; background: #f7f8fa; display: flex; flex-direction: column; gap: 16px; min-height: 100%; }
.card { background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgb(0 0 0 / 6%); }
.card-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.card-title { font-size: 15px; font-weight: 600; color: #1d2129; }
.rsync-form { max-width: 760px; margin-top: 16px; }
.switch-label { margin-left: 8px; color: #4e5969; }
</style>
