<script setup lang="ts">
// 数据备份 - 备份状态 / 预览 / 执行 / 日志
//
// 流程：
//   1) mounted 调 getBackupStatus 探测 U 盘
//   2) 用户点"开始数据备份" → 调 previewBackup 显示预览信息 + 弹确认 modal
//   3) 用户点确认 → 调 runBackup 真正执行，loading + 日志实时展示
//   4) 完成后显示"备份成功 + 路径"或 stderr 错误
//
// 日志只在内存中累积（不做服务端持久化），超过 200KB 自动截断尾部。

import { ref, onMounted, computed } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  getBackupStatus,
  previewBackup,
  runBackup,
  type BackupStatusResp,
  type BackupPreviewResp,
  type BackupRunResp,
} from '@/api/system'

const status = ref<BackupStatusResp | null>(null)
const preview = ref<BackupPreviewResp | null>(null)
const runResult = ref<BackupRunResp | null>(null)

const refreshing = ref(false)
const previewing = ref(false)
const running = ref(false)
const confirmVisible = ref(false)
const customTargetDir = ref('')

const logs = ref('')
const MAX_LOG_BYTES = 200 * 1024

function appendLog(chunk: string) {
  if (!chunk) return
  logs.value += chunk
  if (logs.value.length > MAX_LOG_BYTES) {
    logs.value = logs.value.slice(-MAX_LOG_BYTES)
  }
}

async function refreshStatus() {
  refreshing.value = true
  try {
    const r = await getBackupStatus()
    status.value = r.data
  } catch (err) {
    Message.error('刷新备份状态失败：' + String(err))
  } finally {
    refreshing.value = false
  }
}

async function onPreview() {
  previewing.value = true
  logs.value = ''
  runResult.value = null
  try {
    const r = await previewBackup({
      target_dir: customTargetDir.value || undefined,
    })
    preview.value = r.data
    if (r.data.ok) {
      confirmVisible.value = true
    } else {
      Message.error('备份预览失败')
    }
  } catch (err) {
    Message.error('备份预览失败：' + String(err))
  } finally {
    previewing.value = false
  }
}

async function onConfirmRun() {
  if (!preview.value) return
  confirmVisible.value = false
  running.value = true
  logs.value = ''
  try {
    const r = await runBackup({
      target_dir: preview.value.target_dir,
    })
    runResult.value = r.data
    // 后端用 200 + ok=false 表达"命令跑了但失败"，所以这里都看 stdout/stderr
    appendLog('--- stdout ---\n' + (r.data.stdout || '') + '\n')
    appendLog('--- stderr ---\n' + (r.data.stderr || '') + '\n')
    appendLog(`--- exit ${r.data.returncode} ---\n`)
    if (r.data.ok) {
      Message.success(r.data.message || '备份完成')
      // 备份完后刷新 status 拿到最新 latest_backup
      await refreshStatus()
    } else {
      Message.error(r.data.message || '备份失败')
    }
  } catch (err) {
    Message.error('备份请求失败：' + String(err))
  } finally {
    running.value = false
  }
}

function onCancelRun() {
  confirmVisible.value = false
}

const statusSummary = computed(() => {
  if (!status.value) return ''
  const s = status.value
  const lines: string[] = []
  if (s.usb_inserted && s.usb_mounts.length) {
    const usb = s.usb_mounts[0]
    if (usb) {
      lines.push(`检测到 U 盘：${usb.path}（空闲 ${usb.free} / 总 ${usb.total}）`)
    }
  } else {
    lines.push('未检测到 U 盘，将备份到默认目录')
  }
  if (s.latest_backup) {
    lines.push(`已有最新备份：${s.latest_backup}`)
  } else {
    lines.push('暂无备份文件')
  }
  return lines.join('；')
})

onMounted(() => {
  refreshStatus()
})
</script>

<template>
  <div class="data-backup">
    <div class="card">
      <div class="card-header">
        <div class="card-title">数据备份状态</div>
        <a-space>
          <a-button @click="refreshStatus" :loading="refreshing">刷新状态</a-button>
          <a-button
            type="primary"
            :loading="previewing || running"
            @click="onPreview"
          >
            开始数据备份
          </a-button>
        </a-space>
      </div>
      <div class="status-line">{{ statusSummary || '加载中...' }}</div>

      <a-descriptions
        v-if="status"
        :column="2"
        bordered
        size="small"
        class="meta"
      >
        <a-descriptions-item label="是否检测到 U 盘">
          {{ status.usb_inserted ? '是' : '否' }}
        </a-descriptions-item>
        <a-descriptions-item label="默认备份目录">
          <code style="word-break: break-all;">{{ status.default_backup_dir }}</code>
        </a-descriptions-item>
        <a-descriptions-item v-if="status.usb_mounts.length" label="U 盘列表">
          <a-tag
            v-for="u in status.usb_mounts"
            :key="u.path"
            color="arcoblue"
            style="margin-right: 6px;"
          >
            {{ u.path }} (free {{ u.free }})
          </a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="当前已有备份">
          {{ status.all_backups?.length || 0 }} 个
          <span v-if="status.latest_backup" style="margin-left: 8px; color: #86909c;">
            最新：{{ status.latest_backup }}
          </span>
        </a-descriptions-item>
      </a-descriptions>

      <div class="custom-target">
        <a-input
          v-model="customTargetDir"
          placeholder="可选：自定义备份目录（留空则优先 U 盘 / 否则 ./backups/）"
          allow-clear
        />
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div class="card-title">执行日志</div>
        <a-space>
          <a-tag v-if="running" color="orange">执行中...</a-tag>
          <a-tag v-else-if="runResult?.ok" color="green">已完成</a-tag>
          <a-tag v-else-if="runResult && !runResult.ok" color="red">失败</a-tag>
          <a-button size="small" :disabled="!logs" @click="logs = ''">清空日志</a-button>
        </a-space>
      </div>
      <pre class="log-box">{{ logs || '日志将在备份执行后显示' }}</pre>
    </div>

    <!-- 确认弹窗 -->
    <a-modal
      v-model:visible="confirmVisible"
      title="确认执行数据备份"
      :ok-text="'确认备份'"
      :cancel-text="'取消'"
      @ok="onConfirmRun"
      @cancel="onCancelRun"
      :ok-button-props="{ loading: running, disabled: running }"
    >
      <div v-if="preview">
        <a-alert type="info" style="margin-bottom: 12px">
          备份目标：
          <code>{{ preview.target_dir }}</code>
        </a-alert>
        <a-descriptions :column="1" bordered size="small">
          <a-descriptions-item label="新备份文件">
            {{ preview.new_backup_name }}
          </a-descriptions-item>
          <a-descriptions-item label="预计大小">
            {{ preview.estimated_size }}（{{ preview.estimated_size_bytes }} 字节）
          </a-descriptions-item>
          <a-descriptions-item label="已有备份数量">
            {{ preview.already_backed_up.length }} 个
          </a-descriptions-item>
          <a-descriptions-item label="参与备份的目录">
            <div v-for="d in preview.existing_data_dirs" :key="d">
              <code>{{ d }}</code>
            </div>
            <div v-if="!preview.existing_data_dirs.length" style="color: #f53f3d">
              没有可备份的 data 目录！
            </div>
          </a-descriptions-item>
          <a-descriptions-item v-if="preview.missing_data_dirs.length" label="跳过的目录">
            <div v-for="d in preview.missing_data_dirs" :key="d" style="color: #86909c;">
              {{ d }}（不存在）
            </div>
          </a-descriptions-item>
        </a-descriptions>
        <a-alert
          v-if="preview.estimated_size_bytes > 5 * 1024 * 1024 * 1024"
          type="warning"
          style="margin-top: 12px"
        >
          预计大小超过 5 GB，确认目标目录有足够空间。
        </a-alert>
      </div>
    </a-modal>
  </div>
</template>

<style lang="scss" scoped>
.data-backup {
  padding: 20px;
  background: #f7f8fa;
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 100%;
  .card {
    background: #fff;
    padding: 20px;
    border-radius: 8px;
    box-shadow: 0 2px 8px rgb(0, 0, 0, 6%);
    .card-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 12px;
    }
    .card-title {
      font-size: 15px;
      font-weight: 600;
      color: #1d2129;
    }
    .status-line {
      color: #4e5969;
      font-size: 13px;
      margin-bottom: 12px;
    }
    .meta {
      margin-bottom: 12px;
    }
    .custom-target {
      margin-top: 8px;
    }
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
}
</style>