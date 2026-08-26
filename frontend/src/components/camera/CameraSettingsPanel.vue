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
              <li v-if="isReverseDesync">
                后端状态丢失，可点击下方"同步/启动"复用现有推流。
              </li>
              <li v-if="isAbnormalDesync">
                树莓派未推流，可点击下方"强制重启摄像头"按钮。
              </li>
              <li v-if="!isReverseDesync && !isAbnormalDesync">
                请检查树莓派推流与网络状态。
              </li>
            </ul>
          </div>
        </a-alert>
      </div>
      <div class="status-item" style="width: 100%; margin-top: 8px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
        <a-button
          v-if="isReverseDesync"
          size="small"
          type="primary"
          :loading="syncing"
          @click="handleSync"
        >
          {{ syncing ? '同步中…' : '同步/启动' }}
        </a-button>
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
      <a-tab-pane key="focus" title="摄像焦距设置">
        <div class="config-section">
          <div class="focus-row focus-mode-row">
            <div class="config-item compact-item">
              <label>对焦模式选择</label>
              <a-radio-group v-model="focusMode">
                <a-radio value="continuous">continuous</a-radio>
                <a-radio value="auto">auto</a-radio>
                <a-radio value="manual">manual</a-radio>
              </a-radio-group>
            </div>
            <div class="config-item compact-item" :class="{ disabled: focusMode !== 'manual' }">
              <label>手动镜头位置</label>
              <a-input-number v-model="focusLensPosition" :disabled="focusMode !== 'manual'" :min="0" :max="32" :step="0.1" />
            </div>
          </div>

          <div class="focus-row focus-camera-row">
            <a-button type="primary" :loading="focusPreviewLoading" @click="startFocusPreview">开启摄像头</a-button>
            <a-button :loading="focusApplying" :disabled="!focusPreviewActive" @click="restartFocusCamera">重启摄像头</a-button>
            <a-button :disabled="!focusPreviewActive" :loading="focusStopping" @click="stopFocusPreview">关闭摄像头</a-button>
          </div>
          <div class="focus-preview-section">
            <div class="focus-preview">
              <div v-if="focusPreviewLoading" class="aperture-preview-state">
                <i class="fa-solid fa-spinner fa-spin"></i>
                <span>正在启动摄像头预览...</span>
              </div>
              <div
                v-else-if="focusPreviewError"
                class="aperture-preview-state error"
              >
                <i class="fa-solid fa-triangle-exclamation"></i>
                <span>{{ focusPreviewError }}</span>
              </div>
              <div
                v-else-if="!focusPreviewActive"
                class="aperture-preview-state"
              >
                <i class="fa-solid fa-video-slash"></i>
                <span>摄像头未启动，无法预览</span>
              </div>
              <div
                v-if="
                  focusPreviewActive
                  && !focusPreviewError
                  && !focusFrameUrl
                "
                class="aperture-preview-state"
              >
                <i class="fa-solid fa-spinner fa-spin"></i>
                <span>正在获取实时画面...</span>
              </div>
              <img
                v-show="
                  focusPreviewActive
                  && !focusPreviewError
                  && focusFrameUrl
                "
                :src="focusFrameUrl"
                class="aperture-frame-img"
                alt="焦距预览"
                @error="onFocusFrameImgError"
              />
              <div class="aperture-preview-tip">
                预览为实时 JPEG 抓帧，用于辅助调焦参考
              </div>
            </div>
      <a-button class="focus-capture-button" type="primary" :disabled="!focusPreviewActive" :loading="focusCapturing" @click="captureFocusPhoto">拍照</a-button>
          </div>

          <div class="focus-photos">
            <div class="focus-photo-card">
              <label>刚拍下的原始照片</label>
              <div ref="afStageRef" class="focus-photo-stage af-stage" :style="focusPhotoStageStyle" @pointerdown="onAfStagePointerDown" @pointermove="onAfStagePointerMove" @pointerup="onAfStagePointerUp">
                <img v-if="focusOriginalUrl" :src="focusOriginalUrl" class="focus-photo-image" alt="刚拍下的原始照片" />
                <span v-else class="photo-placeholder">点击“拍照”生成原始照片</span>
                <div class="af-window" :style="afWindowStyle">
                  <span>对焦窗口</span>
                  <div class="af-handle af-handle-tl" @pointerdown.stop="onAfHandlePointerDown($event, 'tl')"></div>
                  <div class="af-handle af-handle-tr" @pointerdown.stop="onAfHandlePointerDown($event, 'tr')"></div>
                  <div class="af-handle af-handle-bl" @pointerdown.stop="onAfHandlePointerDown($event, 'bl')"></div>
                  <div class="af-handle af-handle-br" @pointerdown.stop="onAfHandlePointerDown($event, 'br')"></div>
                </div>
              </div>
            </div>
            <div class="focus-photo-card">
              <label>调焦后照片</label>
              <div class="focus-photo-stage">
                <img v-if="focusFocusedUrl" :src="focusFocusedUrl" alt="调焦后照片" />
                <span v-else class="photo-placeholder">点击“确定对焦参数”生成</span>
              </div>
            </div>
          </div>

          <div class="focus-row focus-action-row">
            <a-button type="primary" :loading="focusConfirming" :disabled="!focusOriginalUrl" @click="confirmFocusParameters">确定对焦参数</a-button>
            <a-button @click="resetFocusWindow">重置对焦窗口</a-button>
          </div>
          <div class="focus-log"><strong>日志</strong><div v-for="(entry, index) in focusLogs" :key="index">{{ entry }}</div></div>
        </div>
      </a-tab-pane>

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
              <a-radio value="960x540">960×540</a-radio>
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
import { ref, reactive, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { Message } from '@arco-design/web-vue'
import { http, httpLong } from '@/api/http'
import { cameraStateText, cameraStateColor } from '@/utils/status'
import { toErrorMessage } from '@/api/http'
import {
  pingLed,
  startLed,
  offLed,
  setLedColor,
  setLedBrightness,
} from '@/api/led'
import { useCameraJpegFramePolling } from '@/composables/useCameraJpegFramePolling'

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

const activeTab = ref('focus')

const config = reactive<CameraConfig>({
  stream_url: '',
  width: 1920,
  height: 1080,
  // fps: 25,
  // width: 960,
  // height: 540,
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
const syncing = ref<boolean>(false)

// ---- 树莓派状态 / 状态不同步 ----
const piReachable = ref<boolean | null>(null)
const piRunning = ref<boolean | null>(null)
const piStreamUrl = ref<string>('')
const desync = ref<boolean | null>(null)
const desyncReason = ref<string>('')

// 逆向流：后端未运行、树莓派在推流 → 可“同步/启动”复用现有推流
const isReverseDesync = computed(
  () => desync.value === true && desyncReason.value.includes('后端未运行')
)
// 异常流：后端运行、树莓派未推流 → 需要“强制重启摄像头”
const isAbnormalDesync = computed(
  () => desync.value === true && desyncReason.value.includes('后端认为正在运行')
)

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

async function loadSettings() {
  try {
    const response = await http.get('/api/camera_yzt/settings')
    const saved = response.data || {}
    config.stream_url = saved.stream_url || ''
    config.width = Number(saved.width) || 1920
    config.height = Number(saved.height) || 1080
    config.fps = Number(saved.fps) || 25
    resolutionPreset.value = `${config.width}x${config.height}`
  } catch (e) {
    // 配置读取失败时保留页面默认值，仍允许用户提交。
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

async function handleSync() {
  if (syncing.value) return
  syncing.value = true
  try {
    const resp = await http.post('/api/camera_yzt/sync')
    const data = resp.data || {}
    if (data.status === 'error') {
      Message.error('同步失败：' + (data.message || '未知错误'))
    } else {
      Message.success('已重新同步，正在复用树莓派现有推流')
    }
    await pollStatus()
  } catch (e) {
    Message.error('同步失败：' + toErrorMessage(e))
  } finally {
    syncing.value = false
  }
}

function handleCancel() {
  emit('cancel')
}

async function handleApply() {
  try {
    const response = await http.put('/api/camera_yzt/settings', {
      stream_url: config.stream_url,
      width: Number(config.width),
      height: Number(config.height),
      fps: Number(config.fps),
    })
    const saved = response.data?.settings || config
    config.stream_url = saved.stream_url || ''
    config.width = Number(saved.width) || config.width
    config.height = Number(saved.height) || config.height
    config.fps = Number(saved.fps) || config.fps
    emit('applied', {
      stream_url: config.stream_url,
      width: config.width,
      height: config.height,
      fps: config.fps,
      recordFormat: recordFormat.value,
      recordQuality: recordQuality.value,
    })
    Message.success('摄像头设置已保存，将在下次启动时生效')
  } catch (e) {
    Message.error('摄像头设置保存失败：' + toErrorMessage(e))
  }
}

watch(() => props.autoPoll, (val) => {
  if (val) {
    loadSettings()
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

// ---------- 摄像焦距设置 ----------
const focusMode = ref('continuous')
const focusLensPosition = ref(2.0)
const focusAfX = ref(0.35)
const focusAfY = ref(0.30)
const focusAfW = ref(0.30)
const focusAfH = ref(0.40)
const focusApplying = ref(false)
const focusOnlineUpdating = ref(false)
const focusPreviewActive = ref(false)
const focusPreviewLoading = ref(false)
const focusPreviewError = ref('')
const focusPreviewStartedByUs = ref(false)
const focusCapturing = ref(false)
const focusConfirming = ref(false)
const focusStopping = ref(false)
const focusOriginalUrl = ref('')
const focusFocusedUrl = ref('')
const focusLogs = ref<string[]>([])

const focusPhotoStageStyle = computed(() => ({
  aspectRatio: `${Math.max(1, Number(config.width) || 1920)} / ${Math.max(1, Number(config.height) || 1080)}`,
}))

const {
  frameUrl: focusFrameUrl,
  frameState: focusFrameState,
  start: focusFrameStart,
  stop: focusFrameStop,
  clearFrame: focusFrameClear,
  onVisibleImgError: onFocusFrameImgError,
} = useCameraJpegFramePolling({ endpoint: '/api/camera_yzt/frame', intervalMs: 200 })

const afStageRef = ref<HTMLElement | null>(null)

let afDrag: {
  type: 'move' | 'resize'
  corner?: 'tl' | 'tr' | 'bl' | 'br'
  offsetX?: number
  offsetY?: number
  startX?: number
  startY?: number
  w?: number
  h?: number
  x?: number
  y?: number
} | null = null

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

function readAfWindow(): [number, number, number, number] {
  const minSize = 0.05
  let x = focusAfX.value
  let y = focusAfY.value
  let w = focusAfW.value
  let h = focusAfH.value
  w = clamp(w, minSize, 1)
  h = clamp(h, minSize, 1)
  x = clamp(x, 0, 1 - w)
  y = clamp(y, 0, 1 - h)
  focusAfX.value = x
  focusAfY.value = y
  focusAfW.value = w
  focusAfH.value = h
  return [x, y, w, h]
}

function writeAfWindow(x: number, y: number, w: number, h: number) {
  w = clamp(w, 0.05, 1)
  h = clamp(h, 0.05, 1)
  x = clamp(x, 0, 1 - w)
  y = clamp(y, 0, 1 - h)
  focusAfX.value = x
  focusAfY.value = y
  focusAfW.value = w
  focusAfH.value = h
}

const afWindowStyle = computed(() => {
  const [x, y, w, h] = readAfWindow()
  return {
    left: `${x * 100}%`,
    top: `${y * 100}%`,
    width: `${w * 100}%`,
    height: `${h * 100}%`,
  }
})

const afWindowText = computed(() => {
  const [x, y, w, h] = readAfWindow()
  return `${x.toFixed(2)},${y.toFixed(2)},${w.toFixed(2)},${h.toFixed(2)}`
})

function setCenterAfWindow() {
  writeAfWindow(0.35, 0.30, 0.30, 0.40)
}

function setWideAfWindow() {
  writeAfWindow(0.20, 0.25, 0.60, 0.50)
}

function onFocusModeChange() {
  // mode change handled reactively
}

function addFocusLog(message: string) {
  focusLogs.value.unshift(`${new Date().toLocaleTimeString()} ${message}`)
  focusLogs.value = focusLogs.value.slice(0, 30)
}

async function loadFocusSettings() {
  try {
    const { data } = await http.get('/api/camera_yzt/focus/settings')
    focusMode.value = data.autofocus_mode || focusMode.value
    focusLensPosition.value = Number(data.lens_position ?? focusLensPosition.value)
    const values = String(data.autofocus_window || '').split(',').map(Number)
    if (values.length === 4 && values.every((value) => Number.isFinite(value))) {
      const x = Number(values[0])
      const y = Number(values[1])
      const w = Number(values[2])
      const h = Number(values[3])
      writeAfWindow(x, y, w, h)
    }
  } catch (e) {
    console.warn('[CameraSettings] load focus settings failed:', e)
  }
}

function focusPayload(): Record<string, any> {
  const payload: Record<string, any> = {
    autofocus_mode: focusMode.value,
    width: Number(config.width) || 1920,
    height: Number(config.height) || 1080,
  }
  if (focusMode.value === 'manual') payload.lens_position = focusLensPosition.value
  else payload.autofocus_window = afWindowText.value
  return payload
}

async function captureFocusPhoto() {
  focusCapturing.value = true
  try {
    const { data } = await http.post('/api/camera_yzt/focus/photo')
    focusOriginalUrl.value = `${data.photo_url}?t=${Date.now()}`
    focusFocusedUrl.value = ''
    addFocusLog('已保存原始照片')
  } catch (e: any) {
    Message.error(`拍照失败：${toErrorMessage(e)}`)
    addFocusLog(`拍照失败：${toErrorMessage(e)}`)
  } finally {
    focusCapturing.value = false
  }
}

async function confirmFocusParameters() {
  focusConfirming.value = true
  try {
    // 旧版树莓派没有动态对焦接口时，后端会重启预览并等待首帧，可能超过普通 8 秒请求超时。
    const { data } = await httpLong.post('/api/camera_yzt/focus/confirm', focusPayload())
    focusFocusedUrl.value = `${data.photo_url}?t=${Date.now()}`
    addFocusLog('已应用对焦窗口并生成调焦后照片')
  } catch (e: any) {
    Message.error(`确定对焦参数失败：${toErrorMessage(e)}`)
    addFocusLog(`确定失败：${toErrorMessage(e)}`)
  } finally {
    focusConfirming.value = false
  }
}

function resetFocusWindow() {
  setCenterAfWindow()
  addFocusLog('已重置对焦窗口')
}

async function restartFocusCamera() {
  focusApplying.value = true
  try {
    if (focusPreviewActive.value) await stopFocusPreview()
    await startFocusPreview()
    addFocusLog('摄像头已重启并应用当前对焦参数')
  } catch (e: any) {
    Message.error(`重启摄像头失败：${toErrorMessage(e)}`)
    addFocusLog(`重启失败：${toErrorMessage(e)}`)
  } finally {
    focusApplying.value = false
  }
}

function pointToRatio(event: PointerEvent): { x: number; y: number } {
  const el = afStageRef.value
  if (!el) return { x: 0, y: 0 }
  const rect = el.getBoundingClientRect()
  return {
    x: clamp((event.clientX - rect.left) / rect.width, 0, 1),
    y: clamp((event.clientY - rect.top) / rect.height, 0, 1),
  }
}

function onAfStagePointerDown(event: PointerEvent) {
  const [x, y, w, h] = readAfWindow()
  const point = pointToRatio(event)
  if ((event.target as HTMLElement)?.classList.contains('af-handle')) {
    const corner = [...(event.target as HTMLElement).classList].find((name) =>
      ['tl', 'tr', 'bl', 'br'].includes(name),
    ) as 'tl' | 'tr' | 'bl' | 'br' | undefined
    afDrag = { type: 'resize', corner, startX: point.x, startY: point.y, x, y, w, h }
  } else if ((event.target as HTMLElement)?.closest('.af-window')) {
    afDrag = { type: 'move', offsetX: point.x - x, offsetY: point.y - y, w, h }
  } else {
    writeAfWindow(point.x - w / 2, point.y - h / 2, w, h)
    afDrag = { type: 'move', offsetX: w / 2, offsetY: h / 2, w, h }
  }
  const stage = afStageRef.value
  if (stage) {
    stage.setPointerCapture(event.pointerId)
  }
  event.preventDefault()
}

function onAfStagePointerMove(event: PointerEvent) {
  if (!afDrag) return
  const point = pointToRatio(event)
  if (afDrag.type === 'resize') {
    const minSize = 0.05
    let left = afDrag.x!
    let top = afDrag.y!
    let right = afDrag.x! + afDrag.w!
    let bottom = afDrag.y! + afDrag.h!
    if (afDrag.corner?.includes('l')) left = clamp(point.x, 0, right - minSize)
    if (afDrag.corner?.includes('r')) right = clamp(point.x, left + minSize, 1)
    if (afDrag.corner?.includes('t')) top = clamp(point.y, 0, bottom - minSize)
    if (afDrag.corner?.includes('b')) bottom = clamp(point.y, top + minSize, 1)
    writeAfWindow(left, top, right - left, bottom - top)
  } else {
    writeAfWindow(
      point.x - afDrag.offsetX!,
      point.y - afDrag.offsetY!,
      afDrag.w!,
      afDrag.h!,
    )
  }
}

function onAfStagePointerUp(event: PointerEvent) {
  afDrag = null
  const stage = afStageRef.value
  if (stage) {
    stage.releasePointerCapture(event.pointerId)
  }
}

function onAfHandlePointerDown(event: PointerEvent, corner: 'tl' | 'tr' | 'bl' | 'br') {
  event.stopPropagation()
  const point = pointToRatio(event)
  const [x, y, w, h] = readAfWindow()
  afDrag = { type: 'resize', corner, startX: point.x, startY: point.y, x, y, w, h }
  afStageRef.value?.setPointerCapture(event.pointerId)
  event.preventDefault()
}

async function startFocusPreview(): Promise<void> {
  focusPreviewLoading.value = true
  focusPreviewError.value = ''
  try {
    const payload: Record<string, any> = {
      stream_url: config.stream_url || undefined,
      width: Number(config.width) || 1920,
      height: Number(config.height) || 1080,
      fps: Number(config.fps) || 25,
      autofocus_mode: focusMode.value,
    }
    if (focusMode.value === 'manual') {
      payload.lens_position = focusLensPosition.value
    } else {
      payload.autofocus_window = afWindowText.value
    }
    const resp = await http.post('/api/camera_yzt/preview/start', payload)
    const data = resp.data || {}
    if (data.status === 'error') {
      focusPreviewError.value = data.message || data.error || '摄像头预览启动失败'
      Message.error(focusPreviewError.value)
      return
    }
    focusPreviewActive.value = true
    focusPreviewStartedByUs.value = true
    focusFrameStart()
    addFocusLog('摄像头已开启')
  } catch (e: any) {
    focusPreviewError.value = e?.message || String(e)
    Message.error(`焦距预览启动失败：${focusPreviewError.value}`)
  } finally {
    focusPreviewLoading.value = false
  }
}

async function stopFocusPreview(): Promise<void> {
  if (focusStopping.value) return
  focusStopping.value = true
  try {
    focusFrameStop({ clearFrame: true })
    await http.post('/api/camera_yzt/preview/stop')
  } catch (e) {
    console.warn('[CameraSettings] stop focus preview failed:', e)
  } finally {
    focusPreviewActive.value = false
    focusPreviewStartedByUs.value = false
    focusStopping.value = false
    addFocusLog('摄像头已停止')
  }
}

async function applyFocusSettings(): Promise<void> {
  focusApplying.value = true
  try {
    if (focusPreviewActive.value) {
      await stopFocusPreview()
    }
    await startFocusPreview()
  } finally {
    focusApplying.value = false
  }
}

async function applyFocusOnline(): Promise<void> {
  focusOnlineUpdating.value = true
  try {
    const payload: Record<string, any> = {
      autofocus_mode: focusMode.value,
    }
    if (focusMode.value === 'manual') {
      payload.lens_position = focusLensPosition.value
    } else {
      payload.autofocus_window = afWindowText.value
    }
    await http.post('/api/camera_yzt/focus', payload)
    Message.success('焦距已在线更新')
  } catch (e: any) {
    Message.error(`在线更新失败：${toErrorMessage(e)}`)
  } finally {
    focusOnlineUpdating.value = false
  }
}

async function checkAndReuseStream(): Promise<void> {
  let statusData: any = null
  try {
    const resp = await http.get('/api/camera_yzt/status')
    statusData = resp.data || {}
  } catch (e) {
    console.warn('[CameraSettings][focus] fetch camera status failed:', e)
  }
  if (statusData) {
    const sessionMode = statusData.session_mode || 'idle'
    if (sessionMode === 'recording' || sessionMode === 'preview') {
      focusPreviewActive.value = true
      focusPreviewStartedByUs.value = false
      focusFrameStart()
      return
    }
  }
  focusPreviewActive.value = false
  focusPreviewStartedByUs.value = false
}

watch(activeTab, (newTab, oldTab) => {
  if (newTab === 'focus') {
    loadFocusSettings()
    checkAndReuseStream()
  }
  if (oldTab === 'focus' && focusPreviewStartedByUs.value) {
    stopFocusPreview()
  }
}, { immediate: true })

onUnmounted(() => {
  if (statusTimer) { clearInterval(statusTimer); statusTimer = null }
  if (focusPreviewStartedByUs.value) {
    stopFocusPreview()
  }
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

// ---------- 摄像焦距设置 ----------
.focus-preview-section {
  margin-bottom: 16px;
}
.focus-row {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
.focus-mode-row { align-items: flex-end; }
.compact-item { margin-bottom: 12px !important; }
.focus-camera-row, .focus-action-row { margin: 10px 0 14px; }
.focus-capture-button { margin-top: 8px; }
.focus-photos {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin: 14px 0;
}
.focus-photo-card label { display: block; margin-bottom: 6px; font-size: 13px; color: #1d2129; }
.focus-photo-stage {
  position: relative;
  width: 100%;
  min-height: 180px;
  overflow: hidden;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  background-color: #111;
  display: flex;
  align-items: center;
  justify-content: center;
  img { width: 100%; height: 100%; object-fit: contain; }
}
.focus-photo-image { width: 100%; height: 100%; object-fit: contain; }
.photo-placeholder { color: #86909c; font-size: 12px; }
.focus-log {
  min-height: 90px;
  max-height: 160px;
  overflow: auto;
  padding: 10px 12px;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  background: #f7f8fa;
  color: #4e5969;
  font: 12px/1.7 SarasaMonoSC, monospace;
  strong { display: block; margin-bottom: 4px; color: #1d2129; font-family: inherit; }
}
.focus-preview {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  min-height: 220px;
  max-height: min(52vh, 560px);
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  background: #000;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}
.aperture-preview-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  color: #86909c;
  font-size: 13px;
  i {
    font-size: 24px;
  }
  &.error {
    color: #f53f3d;
  }
}
.aperture-frame-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
  background: #000;
}
.aperture-preview-tip {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  padding: 6px 12px;
  background: rgb(0 0 0 / 55%);
  color: #fff;
  font-size: 11px;
  text-align: center;
}
.focus-af-actions {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}
.focus-af-tool {
  margin-top: 8px;
}
.af-stage {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  border: 1px solid rgb(15 118 110 / 35%);
  border-radius: 8px;
  background-color: #111;
  background-position: center;
  background-size: 12.5% 12.5%, 12.5% 12.5%, cover, cover;
  user-select: none;
  touch-action: none;
  &::before,
  &::after {
    content: "";
    position: absolute;
    pointer-events: none;
    background: rgb(15 118 110 / 35%);
  }
  &::before {
    left: 50%;
    top: 0;
    width: 1px;
    height: 100%;
  }
  &::after {
    left: 0;
    top: 50%;
    width: 100%;
    height: 1px;
  }
}
.af-window {
  position: absolute;
  display: grid;
  place-items: center;
  border: 2px solid #f59e0b;
  background: rgb(245 158 11 / 14%);
  box-shadow: 0 0 0 999px rgb(12 24 18 / 18%);
  color: #78350f;
  cursor: move;
  font-weight: 800;
  span {
    border-radius: 999px;
    background: rgb(255 251 235 / 90%);
    padding: 4px 8px;
    font-size: 12px;
  }
}
.af-handle {
  position: absolute;
  width: 14px;
  height: 14px;
  border: 2px solid #fff;
  border-radius: 50%;
  background: #f59e0b;
  box-shadow: 0 2px 8px rgb(0 0 0 / 22%);
}
.af-handle-tl { left: -7px; top: -7px; cursor: nwse-resize; }
.af-handle-tr { right: -7px; top: -7px; cursor: nesw-resize; }
.af-handle-bl { left: -7px; bottom: -7px; cursor: nesw-resize; }
.af-handle-br { right: -7px; bottom: -7px; cursor: nwse-resize; }
</style>
