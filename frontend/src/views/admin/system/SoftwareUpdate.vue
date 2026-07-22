<script setup lang="ts">
// 软件更新 - 当前版本 / 上传更新包 / 执行更新 / 日志
//
// 流程：
//   1) mounted 调 getUpdateStatus
//   2) 用户点"选择更新包"→ a-upload 调 uploadUpdatePackage
//   3) 上传成功后展示该包 → 用户点"执行更新" → 调 runUpdate
//   4) 完成后展示 ok/message + 提示"需要重启服务" + 显示新版本

import { ref, onMounted, computed } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  getUpdateStatus,
  uploadUpdatePackage,
  runUpdate,
  type UpdateStatusResp,
  type UpdateRunResp,
} from '@/api/system'

const status = ref<UpdateStatusResp | null>(null)
const runResult = ref<UpdateRunResp | null>(null)
const fileList = ref<{ name: string; path: string; size: string } | null>(null)

const refreshing = ref(false)
const uploading = ref(false)
const running = ref(false)
const confirmVisible = ref(false)
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
    const r = await getUpdateStatus()
    status.value = r.data
  } catch (err) {
    Message.error('刷新更新状态失败：' + String(err))
  } finally {
    refreshing.value = false
  }
}

async function onUploadChange(fileItem: any) {
  const raw: File | undefined = fileItem?.file
  if (!raw) return false
  uploading.value = true
  try {
    const r = await uploadUpdatePackage(raw)
    if (r.data.ok) {
      fileList.value = {
        name: r.data.package_name,
        path: r.data.package_path,
        size: r.data.size,
      }
      Message.success('更新包上传成功')
      // 顺手刷一下
      await refreshStatus()
    } else {
      Message.error('更新包上传失败')
    }
  } catch (err) {
    Message.error('更新包上传失败：' + String(err))
  } finally {
    uploading.value = false
  }
  return false // 阻止 Arco 自动上传，我们自己处理
}

function onRemove() {
  fileList.value = null
  runResult.value = null
  logs.value = ''
}

function onConfirmRun() {
  confirmVisible.value = true
}

async function onRealRun() {
  if (!fileList.value) {
    Message.error('没有可用的更新包')
    return
  }
  confirmVisible.value = false
  running.value = true
  logs.value = ''
  runResult.value = null
  try {
    const r = await runUpdate({ package_path: fileList.value.path })
    runResult.value = r.data
    appendLog('--- stdout ---\n' + (r.data.stdout || '') + '\n')
    appendLog('--- stderr ---\n' + (r.data.stderr || '') + '\n')
    appendLog(`--- exit ${r.data.returncode} ---\n`)
    if (r.data.ok) {
      Message.success(r.data.message || '更新完成')
      await refreshStatus()
    } else {
      Message.error(r.data.message || '更新失败')
    }
  } catch (err) {
    Message.error('更新请求失败：' + String(err))
  } finally {
    running.value = false
  }
}

const uploadTip = computed(() => {
  return '支持 .zip 包；大小不受前端限制。'
})

onMounted(() => {
  refreshStatus()
})
</script>

<template>
  <div class="software-update">
    <div class="card">
      <div class="card-header">
        <div class="card-title">软件更新</div>
        <a-space>
          <a-button @click="refreshStatus" :loading="refreshing">刷新状态</a-button>
        </a-space>
      </div>

      <a-descriptions
        v-if="status"
        :column="2"
        bordered
        size="small"
        class="meta"
      >
        <a-descriptions-item label="当前版本">
          <a-tag color="arcoblue">{{ status.current_version }}</a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="version.json">
          {{ status.version_file_exists ? '存在' : '不存在' }}
        </a-descriptions-item>
        <a-descriptions-item label="updater.py">
          {{ status.updater_script_exists ? '存在' : '不存在（不可更新）' }}
        </a-descriptions-item>
        <a-descriptions-item label="已上传更新包">
          {{ status.uploaded_packages.length }} 个
        </a-descriptions-item>
      </a-descriptions>

      <div class="upload-row">
        <a-upload
          :auto-upload="false"
          :file-list="fileList ? [fileList] : []"
          accept=".zip"
          :tip="uploadTip"
          :on-change="onUploadChange"
          :on-remove="onRemove"
        >
          <template #upload-button>
            <a-button :loading="uploading" type="primary">
              选择更新包（.zip）
            </a-button>
          </template>
        </a-upload>
        <a-button
          v-if="fileList"
          type="primary"
          status="warning"
          :loading="running"
          @click="onConfirmRun"
        >
          执行更新
        </a-button>
      </div>

      <a-alert
        v-if="fileList"
        type="info"
        style="margin-top: 12px;"
      >
        待更新包：
        <code>{{ fileList.name }}</code>（{{ fileList.size }}）
      </a-alert>

      <a-alert
        v-if="runResult && runResult.restart_required"
        type="warning"
        style="margin-top: 12px;"
      >
        更新成功，<b>需要重启后端服务</b>才能生效。当前新版本：
        <a-tag color="green">{{ runResult.new_version }}</a-tag>
        （服务进程：
        <code>bash deploy/start_gpu.sh</code>）。
      </a-alert>
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
      <pre class="log-box">{{ logs || '日志将在更新执行后显示' }}</pre>
    </div>

    <a-modal
      v-model:visible="confirmVisible"
      title="确认执行软件更新"
      :ok-text="'执行更新'"
      :cancel-text="'取消'"
      @ok="onRealRun"
      @cancel="() => (confirmVisible = false)"
      :ok-button-props="{ loading: running, disabled: running, status: 'warning' }"
    >
      <a-alert type="warning" style="margin-bottom: 12px;">
        即将覆盖 <code>version.json</code> 指向的源文件。失败时 updater.py 会自动回滚。
      </a-alert>
      <div v-if="fileList">
        包名：<code>{{ fileList.name }}</code><br />
        路径：<code style="word-break: break-all;">{{ fileList.path }}</code>
      </div>
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
    .meta {
      margin-bottom: 12px;
    }
    .upload-row {
      display: flex;
      align-items: center;
      gap: 12px;
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