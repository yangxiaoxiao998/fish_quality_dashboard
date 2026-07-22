<template>
  <div class="camera-settings-content">
    <div class="status-bar">
      <div class="status-item">
        <span class="status-label">后端状态：</span>
        <a-tag :color="cameraStateColor(backendState)">
          {{ cameraStateText(backendState) }}
        </a-tag>
      </div>
      <div class="status-item">
        <span class="status-label">当前任务：</span>
        <span class="status-value">
          <span v-if="backendCurrentVideoId">{{ backendCurrentVideoId }}</span>
          <span v-else style="color: #999">--</span>
        </span>
      </div>
      <div class="status-item">
        <span class="status-label">已检测鱼数：</span>
        <span class="status-value">
          <span v-if="backendState === 'running' || backendState === 'starting' || backendState === 'stopping'">
            {{ backendFishDetected }}
          </span>
          <span v-else style="color: #999">0</span>
        </span>
      </div>
      <div class="status-item" v-if="backendLastError">
        <span class="status-label">错误：</span>
        <span class="error-message">{{ backendLastError }}</span>
      </div>
      <div class="status-item" v-if="backendLastErrorHistory && !backendLastError && backendState === 'idle'">
        <span class="status-label">上一次错误：</span>
        <span class="error-message">{{ backendLastErrorHistory }}</span>
      </div>
      <div class="status-item" v-if="backendLastVideoId && !backendCurrentVideoId">
        <span class="status-label">上次任务：</span>
        <span class="status-value" style="color: #86909c; font-size: 12px">{{ backendLastVideoId }} ({{ backendLastFishDetected }} 条)</span>
      </div>
      <div class="status-item">
        <span class="status-label">树莓派状态：</span>
        <a-tag :color="piStatusColor">
          {{ piStatusText }}
        </a-tag>
        <span style="margin-left: 6px; font-size: 11px; color: #86909c">
          Pi running: {{ piRunning === null ? '--' : piRunning }} ·
          Pi reachable: {{ piReachable === null ? '--' : piReachable }} ·
          Pi stream_url: {{ piStreamUrl || '--' }}
        </span>
      </div>
      <div class="status-item" style="width: 100%" v-if="desync === true">
        <a-alert type="warning" :show-icon="true" style="width: 100%">
          <template #title>
            后端与树莓派摄像头状态不同步：{{ desyncReason || '请检查树莓派推流' }}
          </template>
          <div style="margin-top: 4px; line-height: 1.6">
            建议操作：
            <ul style="margin: 4px 0 0 18px; padding: 0">
              <li>如果只是后端状态丢失，可点击"同步/启动"复用现有推流。</li>
              <li>如果画面无法显示，可点击下方"强制重启摄像头"按钮。</li>
            </ul>
          </div>
        </a-alert>
      </div>
      <div class="status-item" style="width: 100%; margin-top: 8px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
        <a-button
          size="small"
          status="warning"
          :disabled="resetting"
          @click="handleReset"
        >
          {{ resetting ? '重置中…' : '重置摄像头状态' }}
        </a-button>
        <span style="font-size: 11px; color: #86909c">
          仅清理后端本地状态，不一定停止树莓派推流。
        </span>
        <a-button
          size="small"
          status="danger"
          :disabled="resetting"
          :loading="forceRestarting"
          @click="handleForceRestart"
        >
          {{ forceRestarting ? '强制重启中…' : '强制重启摄像头' }}
        </a-button>
        <span style="font-size: 11px; color: #86909c">
          会先停止树莓派推流，再重新启动。
        </span>
      </div>
    </div>

    <a-tabs v-model:active-key="activeTab">
      <a-tab-pane key="basic" title="基础">
        <div class="config-section">
          <div class="config-item">
            <label>视频流地址 (RTMP/RTSP/HTTP)</label>
            <a-input
              v-model="config.stream_url"
              placeholder="rtmp://host:1935/live/stream"
              allow-clear
            />
            <div class="hint">树莓派 / 服务器推流地址，保存后下次启动时生效</div>
          </div>
          <div class="config-item">
            <label>宽度 (px)</label>
            <a-input-number v-model="config.width" :min="320" :max="3840" :step="160" style="width: 100%" />
          </div>
          <div class="config-item">
            <label>高度 (px)</label>
            <a-input-number v-model="config.height" :min="240" :max="2160" :step="120" style="width: 100%" />
          </div>
          <div class="config-item">
            <label>帧率 (FPS)</label>
            <a-input-number v-model="config.fps" :min="1" :max="60" :step="1" style="width: 100%" />
          </div>
          <div class="config-item">
            <label>分辨率预设</label>
            <a-radio-group v-model="resolutionPreset" @change="applyResolution">
              <a-radio value="1920x1080">1920×1080</a-radio>
              <a-radio value="1280x720">1280×720</a-radio>
              <a-radio value="640x480">640×480</a-radio>
            </a-radio-group>
          </div>
        </div>
      </a-tab-pane>

      <a-tab-pane key="record" title="录制">
        <div class="config-section">
          <div class="config-item">
            <label>视频保存路径</label>
            <a-input
              :model-value="recordPathHint"
              readonly
              placeholder="由后端固定管理"
            />
            <div class="hint">保存路径由后端 <code>data/uploads/videos/</code> 固定管理，客户不能修改服务器路径</div>
          </div>
          <div class="config-item">
            <label>视频格式</label>
            <a-radio-group v-model="recordFormat">
              <a-radio value="mp4">MP4</a-radio>
            </a-radio-group>
            <div class="hint">当前仅支持 MP4</div>
          </div>
          <div class="config-item">
            <label>视频质量</label>
            <a-radio-group v-model="recordQuality">
              <a-radio value="high">高</a-radio>
              <a-radio value="medium">中</a-radio>
              <a-radio value="low">低</a-radio>
            </a-radio-group>
            <div class="hint">影响后端编码码率</div>
          </div>
        </div>
      </a-tab-pane>

      <a-tab-pane key="led" title="光圈设置">
        <div class="config-section">
          <div class="config-item">
            <label>树莓派服务</label>
            <div class="led-status-row">
              <a-tag :color="ledStatusColor">
                {{ ledStatusText }}
              </a-tag>
              <a-button size="small" :loading="ledLoading" @click="handleLedPing">
                检查服务
              </a-button>
            </div>
            <div class="hint">
              {{ ledStatusMessage || '通过后端代理访问树莓派光圈服务（不会让前端直连树莓派）' }}
            </div>
          </div>

          <div class="config-item">
            <label>开关</label>
            <a-space>
              <a-button
                type="primary"
                :loading="ledActionLoading === 'start'"
                @click="handleLedStart"
              >
                开启光圈
              </a-button>
              <a-button
                status="danger"
                :loading="ledActionLoading === 'off'"
                @click="handleLedOff"
              >
                关闭光圈
              </a-button>
            </a-space>
            <div class="hint">开启光圈默认亮度 32、白光（255,255,255）</div>
          </div>

          <div class="config-item">
            <label>常用颜色</label>
            <a-space wrap>
              <a-button
                v-for="preset in colorPresets"
                :key="preset.name"
                size="small"
                @click="applyColorPreset(preset)"
              >
                <span
                  class="color-dot"
                  :style="{ backgroundColor: `rgb(${preset.r}, ${preset.g}, ${preset.b})` }"
                ></span>
                {{ preset.name }}
              </a-button>
            </a-space>
          </div>

          <div class="config-item">
            <label>RGB 颜色</label>
            <a-row :gutter="8">
              <a-col :span="8">
                <a-input-number
                  v-model="ledColor.r"
                  :min="0"
                  :max="255"
                  placeholder="R"
                  style="width: 100%"
                />
              </a-col>
              <a-col :span="8">
                <a-input-number
                  v-model="ledColor.g"
                  :min="0"
                  :max="255"
                  placeholder="G"
                  style="width: 100%"
                />
              </a-col>
              <a-col :span="8">
                <a-input-number
                  v-model="ledColor.b"
                  :min="0"
                  :max="255"
                  placeholder="B"
                  style="width: 100%"
                />
              </a-col>
            </a-row>
            <div
              class="led-preview"
              :style="{ backgroundColor: `rgb(${ledColor.r}, ${ledColor.g}, ${ledColor.b})` }"
            ></div>
            <a-button
              type="primary"
              :loading="ledActionLoading === 'color'"
              @click="handleApplyLedColor"
            >
              应用颜色
            </a-button>
          </div>

          <div class="config-item">
            <label>亮度（{{ ledBrightness }}）</label>
            <a-slider
              v-model="ledBrightness"
              :min="0"
              :max="255"
              :step="1"
            />
            <a-button
              type="primary"
              :loading="ledActionLoading === 'brightness'"
              @click="handleApplyLedBrightness"
            >
              应用亮度
            </a-button>
            <div class="hint">亮度 0~255，推荐默认 32</div>
          </div>
        </div>
      </a-tab-pane>
    </a-tabs>

    <div class="panel-footer">
      <a-button @click="handleCancel">取消</a-button>
      <a-button type="primary" @click="handleApply">应用设置</a-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import { Message } from '@arco-design/web-vue'
import { http } from '@/api/http'
import { cameraStateText, cameraStateColor } from '@/utils/status'
import { toErrorMessage } from '@/api/http'
import {
  pingLed,
  startLed,
  offLed,
  setLedColor,
  setLedBrightness,
} from '@/api/led'

export interface CameraConfig {
  stream_url: string
  width: number
  height: number
  fps: number
  recordFormat: string
  recordQuality: string
}

const props = defineProps<{
  /** 是否自动开始状态轮询（弹窗场景：visible；页面场景：true） */
  autoPoll?: boolean
}>()

const emit = defineEmits<{
  (e: 'applied', config: CameraConfig): void
  (e: 'cancel'): void
}>()

const activeTab = ref('basic')

const config = reactive<CameraConfig>({
  stream_url: '',
  width: 1920,
  height: 1080,
  fps: 25,
  recordFormat: 'mp4',
  recordQuality: 'high',
})

const resolutionPreset = ref('1920x1080')

const recordFormat = ref('mp4')
const recordQuality = ref('high')
const recordPathHint = 'data/uploads/videos/ (后端管理)'

const backendState = ref<string>('idle')
const backendCurrentVideoId = ref<string>('')
const backendFishDetected = ref<number>(0)
const backendLastError = ref<string>('')
const backendLastErrorHistory = ref<string>('')
const backendLastVideoId = ref<string>('')
const backendLastFishDetected = ref<number>(0)
const resetting = ref<boolean>(false)
const forceRestarting = ref<boolean>(false)

// ---- 树莓派状态 / 状态不同步 ----
const piReachable = ref<boolean | null>(null)
const piRunning = ref<boolean | null>(null)
const piStreamUrl = ref<string>('')
const desync = ref<boolean | null>(null)
const desyncReason = ref<string>('')

const piStatusText = computed(() => {
  if (piReachable.value === false) return '不可达'
  if (piReachable.value === null) return '未知'
  return piRunning.value ? '正在推流' : '未推流'
})
const piStatusColor = computed(() => {
  if (piReachable.value === false) return 'red'
  if (piReachable.value === null) return 'gray'
  return piRunning.value ? 'green' : 'gray'
})

let statusTimer: number | null = null
let statusPolling = false

// ---------- LED 光圈设置状态 ----------
const ledStatus = ref<'unknown' | 'online' | 'offline'>('unknown')
const ledStatusMessage = ref('')
const ledLoading = ref(false)
const ledActionLoading = ref<string>('')
const ledColor = reactive({ r: 255, g: 255, b: 255 })
const ledBrightness = ref<number>(32)

const colorPresets = [
  { name: '白光', r: 255, g: 255, b: 255 },
  { name: '暖白', r: 255, g: 220, b: 180 },
  { name: '红', r: 255, g: 0, b: 0 },
  { name: '绿', r: 0, g: 255, b: 0 },
  { name: '蓝', r: 0, g: 0, b: 255 },
]

const ledStatusText = computed(() => {
  if (ledStatus.value === 'online') return '在线'
  if (ledStatus.value === 'offline') return '离线'
  return '未知'
})
const ledStatusColor = computed(() => {
  if (ledStatus.value === 'online') return 'green'
  if (ledStatus.value === 'offline') return 'red'
  return 'gray'
})

async function handleLedPing() {
  ledLoading.value = true
  try {
    const resp = await pingLed()
    ledStatus.value = 'online'
    const url = resp.data?.pi_base_url || ''
    ledStatusMessage.value = `树莓派 ${url} 可达`
    Message.success('树莓派服务连接正常')
  } catch (err) {
    ledStatus.value = 'offline'
    const msg = toErrorMessage(err)
    ledStatusMessage.value = msg
    Message.error(`树莓派服务连接失败：${msg}`)
  } finally {
    ledLoading.value = false
  }
}

async function runLedAction(
  name: string,
  fn: () => Promise<unknown>,
  successText: string,
) {
  ledActionLoading.value = name
  try {
    await fn()
    Message.success(successText)
  } catch (err) {
    Message.error(toErrorMessage(err))
  } finally {
    ledActionLoading.value = ''
  }
}

function applyColorPreset(preset: { r: number; g: number; b: number }) {
  ledColor.r = preset.r
  ledColor.g = preset.g
  ledColor.b = preset.b
}

async function handleLedStart() {
  await runLedAction('start', () => startLed(), '光圈已开启')
}
async function handleLedOff() {
  await runLedAction('off', () => offLed(), '光圈已关闭')
}
async function handleApplyLedColor() {
  await runLedAction(
    'color',
    () => setLedColor({
      r: Number(ledColor.r) || 0,
      g: Number(ledColor.g) || 0,
      b: Number(ledColor.b) || 0,
    }),
    '光圈颜色已更新',
  )
}
async function handleApplyLedBrightness() {
  await runLedAction(
    'brightness',
    () => setLedBrightness({ brightness: Number(ledBrightness.value) || 0 }),
    '光圈亮度已更新',
  )
}

function applyResolution() {
  const [w, h] = resolutionPreset.value.split('x').map(Number)
  config.width = w || 1920
  config.height = h || 1080
}

async function pollStatus() {
  // 同一时刻最多只能存在一个 /api/camera_yzt/status 请求 pending。
  if (statusPolling) return
  statusPolling = true
  try {
    const r = await http.get('/api/camera_yzt/status')
    const d = r.data
    backendState.value = d.state || 'idle'
    backendCurrentVideoId.value = d.current_video_id || ''
    backendFishDetected.value = d.fish_detected || 0
    backendLastError.value = d.last_error || ''
    backendLastErrorHistory.value = d.last_error_history || ''
    backendLastVideoId.value = d.last_video_id || ''
    backendLastFishDetected.value = d.last_fish_detected || 0

    // ---- 兼容新字段：backend / raspberry_pi / desync ----
    const pi = d.raspberry_pi || {}
    piReachable.value = pi.reachable === undefined ? null : Boolean(pi.reachable)
    piRunning.value = pi.running === undefined ? null : Boolean(pi.running)
    piStreamUrl.value = pi.stream_url || ''
    desync.value =
      d.desync === undefined || d.desync === null ? null : Boolean(d.desync)
    desyncReason.value = d.desync_reason || ''
  } catch (e) {
    // 状态轮询失败不能中断界面。
  } finally {
    statusPolling = false
  }
}

async function handleReset() {
  resetting.value = true
  try {
    await http.post('/api/camera_yzt/reset')
    Message.success('摄像头状态已重置')
    await pollStatus()
  } catch (e) {
    Message.error('重置失败：' + (e instanceof Error ? e.message : String(e)))
  } finally {
    resetting.value = false
  }
}

async function handleForceRestart() {
  if (forceRestarting.value || resetting.value) return
  forceRestarting.value = true
  try {
    const resp = await http.post('/api/camera_yzt/force-restart', {
      stream_url: config.stream_url,
      width: Number(config.width) || 1920,
      height: Number(config.height) || 1080,
      fps: Number(config.fps) || 25,
    })
    const data = resp.data || {}
    if (data.force_restarted) {
      Message.success('摄像头已强制重启')
    } else {
      Message.success('摄像头重启请求已发送')
    }
    await pollStatus()
  } catch (e) {
    Message.error('强制重启失败：' + toErrorMessage(e))
  } finally {
    forceRestarting.value = false
  }
}

function handleCancel() {
  emit('cancel')
}

function handleApply() {
  emit('applied', {
    stream_url: config.stream_url,
    width: config.width,
    height: config.height,
    fps: config.fps,
    recordFormat: recordFormat.value,
    recordQuality: recordQuality.value,
  })
  Message.success('摄像头设置已应用')
}

watch(() => props.autoPoll, (val) => {
  if (val) {
    pollStatus()
    if (!statusTimer) {
      statusTimer = window.setInterval(pollStatus, 2000)
    }
    // 启用面板时自动 ping 一次树莓派光圈服务；失败不阻塞
    handleLedPing()
  } else {
    if (statusTimer) { clearInterval(statusTimer); statusTimer = null }
  }
}, { immediate: true })

onMounted(() => {
  // pollStatus 由 autoPoll watch(immediate) 启动
})

onUnmounted(() => {
  if (statusTimer) { clearInterval(statusTimer); statusTimer = null }
})
</script>

<style lang="scss" scoped>
.camera-settings-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
  .status-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    padding: 10px 12px;
    background: rgb(245 245 245 / 80%);
    border-radius: 8px;
    .status-item {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 13px;
      .status-label {
        color: #4e5969;
      }
      .status-value {
        color: #1d2129;
        font-family: SarasaMonoSC, monospace;
      }
      .error-message {
        color: #f53f3d;
      }
    }
  }
  .config-section {
    padding: 12px 4px;
    .config-item {
      margin-bottom: 16px;
      label {
        display: block;
        margin-bottom: 6px;
        font-size: 13px;
        color: #1d2129;
      }
      .hint {
        margin-top: 4px;
        font-size: 12px;
        color: #86909c;
        code {
          font-family: monospace;
          color: #165dff;
        }
      }
      &.disabled {
        opacity: 0.6;
      }
    }
  }
  .panel-footer {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding-top: 8px;
    border-top: 1px solid #e5e6eb;
  }
}
:deep(.arco-tabs-content) {
  padding: 0 8px;
}
:deep(.arco-slider) {
  --arco-slider-runway-bg: rgb(0 0 0 / 8%);
  --arco-slider-main-bg: #00a8e8;
}
.led-status-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 4px;
}
.led-preview {
  width: 100%;
  height: 22px;
  border-radius: 6px;
  margin: 8px 0;
  border: 1px solid #e5e6eb;
  background: rgb(255, 255, 255);
  transition: background-color 0.15s ease;
}
.color-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: middle;
  border: 1px solid rgb(0 0 0 / 12%);
}
</style>