<template>
  <div class="dashboard-viewport data-input-page">
    <div class="dashboard-fixed" :style="fixedScreenStyle">
  <div class="dashboard-background">
    <Layout>
      <!-- 左侧区域：4个ECharts图表 (20%) —— DataInput 不需要 -->
      <!-- 中间区域：实时检测分析（DataInput 完整保留中间视频 + 摄像头控制 + 光圈设置） -->
      <template #middle>
        <LayoutPanel title="实时检测分析" class="center-panel-wrapper">
          <div class="center-panel">

            <!-- 视频播放区（按优先级渲染：实时帧 > 录制完成MP4 > VideoWall） -->
            <div class="video-area">
              <div class="video-wrapper">

                <!-- 优先级1：摄像头实时帧 -->
                <template v-if="isFormalRecordingLive">
                  <div class="camera-stream-container">
                    <img
                      v-if="sharedCameraFrameUrl"
                      :src="sharedCameraFrameUrl"
                      alt="摄像头实时画面"
                      class="camera-stream-img"
                      :class="{ 'camera-frame-reconnecting': sharedCameraFrameState === 'reconnecting' }"
                      @click="handleCameraFrameClick"
                      @error="onSharedFrameImgError"
                    />
                    <div class="camera-statusbar">
                      <!-- <div class="status">
                        <span class="k">视频流：</span>
                        <span class="v">{{ streamUrl }}</span>
                      </div> -->
                      <div class="status">
                        <span class="k">视频 ID：</span>
                        <span class="v">{{ currentVideoId || '--' }}</span>
                      </div>
                      <div class="status">
                        <span class="k">状态：</span>
                        <span class="v">{{ cameraState }}</span>
                      </div>
                      <div v-if="timedRecording && !timerPending" class="status">
                        <span class="k">剩余：</span>
                        <span class="v remaining-value">{{ formatSeconds(remainingSeconds) }}</span>
                      </div>
                      <div v-else-if="timerPending" class="status">
                        <span class="k">剩余：</span>
                        <span class="v remaining-value">-- (启动倒计时)</span>
                      </div>
                    </div>
                  </div>
                </template>

                <!-- 优先级2：录制完成 MP4 加载中 -->
                <div
                  v-else-if="recordedVideoLoading"
                  class="video-status-text"
                >
                  正在准备录制视频...
                </div>

                <!-- 优先级2：录制完成 MP4 加载失败 -->
                <div
                  v-else-if="recordedVideoError"
                  class="video-status-text error"
                >
                  {{ recordedVideoError }}
                </div>

                <!-- 优先级2：录制完成 MP4（与实时流同一 video-wrapper） -->
                <div
                  v-else-if="recordedVideoUrl"
                  class="recorded-video-wrap"
                >
                  <video
                    :key="recordedVideoUrl"
                    :src="recordedVideoUrl"
                    class="recorded-video"
                    controls
                    playsinline
                    preload="metadata"
                    @error="handleRecordedVideoError"
                  />
                  <div class="recorded-video-meta">
                    <span>录制视频：</span>
                    <span>{{ recordedVideoId }}</span>
                  </div>
                </div>

                <!-- 优先级3：未启动 / 无录制 → VideoWall（含历史视频 + 浏览器原生 controls） -->
                <VideoWall
                  v-else
                  ref="videoWallRef"
                />
              </div>
            </div>

            <!-- 控制按钮（来自 Dashboard 第262-275行） -->
            <div class="control-buttons">
              <div class="camera-group">
                <button
                  class="ctrl-btn camera-btn"
                  :disabled="cameraState === 'stopping' || cameraStarting || cameraState === 'starting'"
                  @click="handleCameraInput"
                >
                  <i :class="cameraButtonIcon"></i>
                  <span>{{ cameraButtonText }}</span>
                </button>
                <button
                  class="ctrl-btn settings-btn"
                  @click="handleOpenApertureSettings"
                >
                  <i class="fa-solid fa-gear"></i>
                  <span>录制设置</span>
                </button>
              </div>
            </div>

            <!-- 光圈设置弹窗（来自 Dashboard 第298-478行） -->
            <a-modal
              v-model:visible="apertureDialogVisible"
              title="录制设置"
              :width="840"
              :mask-closable="true"
              :footer="false"
              :unmount-on-close="false"
              @cancel="closeApertureDialog"
            >
              <div class="aperture-dialog">
                <div class="aperture-preview">
                  <div v-if="aperturePreviewLoading" class="aperture-preview-state">
                    <i class="fa-solid fa-spinner fa-spin"></i>
                    <span>正在启动摄像头预览...</span>
                  </div>
                  <div
                    v-else-if="aperturePreviewError"
                    class="aperture-preview-state error"
                  >
                    <i class="fa-solid fa-triangle-exclamation"></i>
                    <span>{{ aperturePreviewError }}</span>
                  </div>
                  <div
                    v-else-if="!aperturePreviewActive"
                    class="aperture-preview-state"
                  >
                    <i class="fa-solid fa-video-slash"></i>
                    <span>摄像头未启动，无法预览</span>
                  </div>
                  <div
                    v-if="
                      aperturePreviewActive
                      && !aperturePreviewError
                      && !sharedCameraFrameUrl
                    "
                    class="aperture-preview-state"
                  >
                    <i class="fa-solid fa-spinner fa-spin"></i>
                    <span>正在获取实时画面...</span>
                  </div>
                  <img
                    v-show="
                      aperturePreviewActive
                      && !aperturePreviewError
                      && sharedCameraFrameUrl
                    "
                    :src="sharedCameraFrameUrl"
                    class="aperture-frame-img"
                    alt="光圈预览"
                    @error="onSharedFrameImgError"
                  />
                  <div class="aperture-preview-tip">
                    预览为实时 JPEG 抓帧，不会保存为视频，也不会进入视频历史记录
                  </div>
                </div>

                <div class="aperture-controls">
                  <div class="aperture-section aperture-integrity-section">
                    <div class="aperture-section-title">完整性检测</div>
                    <a-radio-group
                      :model-value="needsIntegrity"
                      :disabled="integritySettingLoading"
                      @change="handleIntegritySettingChange"
                    >
                      <a-radio :value="true">需要检测完整性</a-radio>
                      <a-radio :value="false">不检测完整性</a-radio>
                    </a-radio-group>
                    <div class="aperture-section-desc">
                      此设置会保存，并在下一次立即录制或计划任务开始时生效。
                    </div>
                  </div>
                  <div
                    class="aperture-mode-tip"
                    :class="`aperture-mode-${aperturePreviewMode}`"
                  >
                    <i v-if="aperturePreviewMode === 'preview'" class="fa-solid fa-eye"></i>
                    <i v-else-if="aperturePreviewMode === 'recording-reuse'" class="fa-solid fa-link"></i>
                    <i v-else class="fa-solid fa-circle-info"></i>
                    <span v-if="aperturePreviewMode === 'preview'">
                      当前为临时预览模式，关闭弹窗后会停止摄像头预览，不会保存视频。
                    </span>
                    <span v-else-if="aperturePreviewMode === 'recording-reuse'">
                      当前正在录制，预览复用正在录制的摄像头画面。关闭弹窗不会停止录制。
                    </span>
                    <span v-else>
                      正在检测摄像头状态...
                    </span>
                  </div>
                  <div class="aperture-section">
                    <div class="aperture-section-title">
                      树莓派光圈服务
                      <a-tag :color="apertureLedStatusColor" size="small">
                        {{ apertureLedStatusText }}
                      </a-tag>
                    </div>
                    <div class="aperture-section-desc">
                      {{ apertureLedStatusMessage || '通过后端代理访问树莓派，前端不直连' }}
                    </div>
                    <a-space>
                      <a-button
                        size="small"
                        :loading="apertureLedLoading"
                        @click="handleApertureLedPing"
                      >
                        检查服务
                      </a-button>
                      <a-button
                        type="primary"
                        size="small"
                        :loading="apertureLedActionLoading === 'start'"
                        @click="handleApertureLedStart"
                      >
                        开启光圈
                      </a-button>
                      <a-button
                        status="danger"
                        size="small"
                        :loading="apertureLedActionLoading === 'off'"
                        @click="handleApertureLedOff"
                      >
                        关闭光圈
                      </a-button>
                    </a-space>
                  </div>

                  <a-divider style="margin: 12px 0" />

                  <div class="aperture-section">
                    <div class="aperture-section-title">常用颜色</div>
                    <div class="aperture-color-presets">
                      <button
                        v-for="preset in apertureLedColorPresets"
                        :key="preset.name"
                        type="button"
                        class="aperture-color-preset"
                        @click="applyApertureColorPreset(preset)"
                      >
                        <span
                          class="aperture-color-dot"
                          :style="{ backgroundColor: `rgb(${preset.r}, ${preset.g}, ${preset.b})` }"
                        ></span>
                        {{ preset.name }}
                      </button>
                    </div>
                  </div>

                  <div class="aperture-section">
                    <div class="aperture-section-title">RGB 颜色</div>
                    <div class="aperture-rgb-row">
                      <a-input-number
                        v-model="apertureLedColor.r"
                        :min="0"
                        :max="255"
                        placeholder="R"
                      />
                      <a-input-number
                        v-model="apertureLedColor.g"
                        :min="0"
                        :max="255"
                        placeholder="G"
                      />
                      <a-input-number
                        v-model="apertureLedColor.b"
                        :min="0"
                        :max="255"
                        placeholder="B"
                      />
                    </div>
                    <div
                      class="aperture-color-preview"
                      :style="{ backgroundColor: `rgb(${apertureLedColor.r}, ${apertureLedColor.g}, ${apertureLedColor.b})` }"
                    ></div>
                    <a-button
                      type="primary"
                      size="small"
                      :loading="apertureLedActionLoading === 'color'"
                      @click="handleApertureApplyColor"
                    >
                      应用颜色
                    </a-button>
                  </div>

                  <div class="aperture-section">
                    <div class="aperture-section-title">
                      亮度（{{ apertureLedBrightness }}）
                    </div>
                    <a-slider
                      v-model="apertureLedBrightness"
                      :min="0"
                      :max="255"
                      :step="1"
                    />
                    <a-button
                      type="primary"
                      size="small"
                      :loading="apertureLedActionLoading === 'brightness'"
                      @click="handleApertureApplyBrightness"
                    >
                      应用亮度
                    </a-button>
                    <div class="aperture-section-desc">亮度 0~255，推荐默认 32</div>
                  </div>
                </div>
                <div class="aperture-dialog-footer">
                  <a-button type="primary" @click="closeApertureDialog">
                    关闭
                  </a-button>
                </div>
              </div>
            </a-modal>
            <!-- 唯一录制计划入口：RecordScheduleDialog -->
            <RecordScheduleDialog
              v-model:visible="recordScheduleDialogVisible"
              mode="duration"
              @confirm="handleRecordScheduleConfirm"
            />
            <RecordScheduleDialog
              v-model:visible="recordPlanningDialogVisible"
              mode="schedule"
            />
            <a-modal
              v-model:visible="recordModeDialogVisible"
              title="请选择录制方式"
              :footer="false"
              width="420px"
            >
              <a-space direction="vertical" fill>
                <div>请选择录制方式</div>
                <a-button type="primary" long @click="chooseImmediateRecording">
                  立即开始
                </a-button>
                <a-button type="primary" long @click="chooseScheduledRecording">
                  计划开始
                </a-button>
              </a-space>
            </a-modal>

          </div>
        </LayoutPanel>
      </template>
    </Layout>
  </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import Layout from '@/layout/Layout.vue'
import { LayoutPanel } from '@/layout'
import VideoWall from '@/components/stream/VideoWall.vue'
import { useCameraJpegFramePolling } from '@/composables/useCameraJpegFramePolling'
import { http } from '@/api/http'
import {
  pingLed,
  startLed,
  offLed,
  setLedColor,
  setLedBrightness,
} from '@/api/led'
import { logUserAction } from '@/utils/operationLogger'
import { useFixedScreen } from '@/utils/useFixedScreen'
import RecordScheduleDialog, {
  type RecordTask,
} from '@/components/camera/RecordScheduleDialog.vue'

// ============================================================
// 摄像头状态机（后端 /api/camera_yzt/status 权威）
// ============================================================

const cameraStreamActive = ref(false)
const cameraLoading = ref(false)
// 后端 state：idle | starting | running | stopping | stopped | error
const cameraState = ref<string>('idle')
const cameraStarting = ref<boolean>(false)

// ============================================================
// 共享 JPEG 轮询的使用者所有权
// - cameraFrameConsumerActive: 正式录制主画面是否需要 JPEG 轮询
// - apertureFrameConsumerActive: 光圈弹窗是否需要 JPEG 轮询
// 共享轮询只在至少一个 consumer 为 true 时运行。
// ============================================================
const cameraFrameConsumerActive = ref<boolean>(false)
const apertureFrameConsumerActive = ref<boolean>(false)

function syncSharedFramePolling(
  options: { clearWhenStopped?: boolean } = {},
): void {
  const shouldRun =
    cameraFrameConsumerActive.value
    || apertureFrameConsumerActive.value
  if (shouldRun) {
    startSharedFramePolling()
    return
  }
  stopSharedFramePolling({
    clearFrame: options.clearWhenStopped ?? true,
  })
}

// ============================================================
// 会话类型分类（区分正式录制 / 临时预览 / 空闲）
// ============================================================
type CameraSessionMode = 'idle' | 'recording' | 'preview' | 'unknown'
const activeSessionMode = ref<CameraSessionMode>('idle')
let lastLoggedSessionMode: CameraSessionMode | null = null

function getStatusVideoId(data: any): string {
  return String(
    data?.current_video_id
    || data?.video_id
    || '',
  ).trim()
}

function getBackendSessionMode(data: any): CameraSessionMode {
  const explicit = String(
    data?.session_mode
    || data?.backend?.session_mode
    || '',
  ).trim()
  if (explicit === 'idle' || explicit === 'recording' || explicit === 'preview') {
    return explicit as CameraSessionMode
  }
  // 兼容旧后端：流在运行 + 有 video_id = 正式录制
  const previewFlag = Boolean(
    data?.preview_mode
    || data?.backend?.preview_mode
    || false,
  )
  if (previewFlag) return 'preview'
  const state = String(data?.state || 'idle')
  const backendLive = Boolean(data?.is_active)
    || Boolean(data?.stream_ready)
    || state === 'starting'
    || state === 'running'
    || state === 'stopping'
  const videoId = getStatusVideoId(data)
  if (backendLive && videoId) return 'recording'
  if (backendLive && !videoId) return 'preview'
  if (state === 'idle') return 'idle'
  return 'unknown'
}

// 中间主画面是否显示实时摄像头（仅正式录制）
const isFormalRecordingLive = computed(() => {
  if (activeSessionMode.value !== 'recording') return false
  return (
    cameraStreamActive.value
    || cameraState.value === 'starting'
    || cameraState.value === 'running'
    || cameraState.value === 'stopping'
  ) && Boolean(currentVideoId.value)
})

// 统一实时帧轮询 composable（主画面与光圈预览共享 frameUrl）
const {
  frameUrl: sharedCameraFrameUrl,
  frameState: sharedCameraFrameState,
  start: startSharedFramePolling,
  stop: stopSharedFramePolling,
  onVisibleImgError: onSharedFrameImgError,
} = useCameraJpegFramePolling()

// 摄像头采集分辨率从系统摄像头设置读取，启动录制时原样传给后端。
const cameraConfig = reactive({
  stream_url: '',
  width: 960,
  height: 540,
  fps: 25,
})

// 录制时间状态（来自后端 status，本地仅做 UI 平滑）
const recordDurationSeconds = ref<number | null>(null)
const scheduledStopAt = ref<string | null>(null)
const remainingSeconds = ref<number | null>(null)
const timedRecording = ref<boolean>(false)
const timerPending = ref<boolean>(false)
const autoStopTriggered = ref<boolean>(false)
const stopReason = ref<string>('')
const stopSource = ref<string>('')

const currentVideoId = ref<string>('')

const streamUrl = computed(() => cameraConfig.stream_url)

async function loadCameraSettings(): Promise<void> {
  try {
    const resp = await http.get('/api/camera_yzt/settings')
    const settings = resp.data || {}
    cameraConfig.stream_url = String(settings.stream_url || '')
    cameraConfig.width = Number(settings.width) || 960
    cameraConfig.height = Number(settings.height) || 540
    cameraConfig.fps = Number(settings.fps) || 25
    needsIntegrity.value = settings.force_all_complete === undefined
      ? true
      : !Boolean(settings.force_all_complete)
  } catch (err) {
    console.warn('[DataInput][Camera] load settings failed:', err)
  }
}

async function handleIntegritySettingChange(value: boolean): Promise<void> {
  needsIntegrity.value = Boolean(value)
  integritySettingLoading.value = true
  try {
    await http.put('/api/camera_yzt/settings', {
      stream_url: cameraConfig.stream_url,
      width: cameraConfig.width,
      height: cameraConfig.height,
      fps: cameraConfig.fps,
      force_all_complete: !needsIntegrity.value,
    })
    Message.success('完整性检测设置已保存')
  } catch (error: any) {
    Message.error(`完整性设置保存失败：${error?.message || String(error)}`)
  } finally {
    integritySettingLoading.value = false
  }
}

// ============================================================
// 录制完成后回放（与实时流同一 video-wrapper）
// ============================================================
const recordedVideoId = ref<string>('')
const recordedVideoUrl = ref<string>('')
const recordedVideoLoading = ref<boolean>(false)
const recordedVideoError = ref<string>('')
// 保存最近一次后端 running/starting 期间非空 video_id，
// 用于自动/手动停止后加载成品视频。
const lastRunningVideoId = ref<string>('')
// 防止 status 轮询期间同一 video 重复请求。
const loadedRecordedVideoId = ref<string>('')
let recordedVideoPollTimer: number | null = null

// ============================================================
// 视频检测报告状态机（DataInput 内）
// ============================================================
type VideoReportState =
  | 'idle'
  | 'recording'
  | 'queued'
  | 'processing'
  | 'generating'
  | 'ready'
  | 'failed'

const LAST_REPORT_VIDEO_ID_KEY = 'data_input_last_report_video_id'

const reportState = ref<VideoReportState>('idle')
const reportVideoId = ref('')
const reportPdfUrl = ref('')
const reportDocxUrl = ref('')
const reportError = ref('')
const reportChecking = ref(false)
const reportGenerateRequestedVideoId = ref('')
let reportStatusTimer: number | null = null

// ============================================================
// 唯一录制入口：RecordScheduleDialog
// ============================================================
const recordScheduleDialogVisible = ref(false)
const recordModeDialogVisible = ref(false)
const recordPlanningDialogVisible = ref(false)
const needsIntegrity = ref(true)
const integritySettingLoading = ref(false)

// ============================================================
// 摄像头按钮文案 / 图标（来自 Dashboard 第2107-2119行）
// ============================================================
const cameraButtonText = computed(() => {
  if (cameraState.value === 'starting') return '正在连接'
  if (cameraState.value === 'stopping') return '正在关闭'
  // 只有正式录制才显示「关闭摄像头」；临时预览保持「摄像头输入」以避免误导。
  if (isFormalRecordingLive.value) return '结束录制'
  if (activeSessionMode.value === 'preview') return '开始录制（预览中）'
  return '开始录制'
})

const cameraButtonIcon = computed(() => {
  if (isFormalRecordingLive.value) return 'fa-solid fa-stop'
  return 'fa-solid fa-camera'
})

const canStart = computed(() => {
  // 正式录制时不能再 start；preview 也占用后端流，需先 stop preview。
  if (
    isFormalRecordingLive.value
    || activeSessionMode.value === 'preview'
    || activeSessionMode.value === 'recording'
  ) {
    return false
  }
  return cameraState.value === 'idle' || cameraState.value === 'error'
})

// ============================================================
// 光圈设置弹窗 + 临时预览状态（来自 Dashboard 第998-1047行）
// ============================================================
const apertureDialogVisible = ref(false)
const aperturePreviewLoading = ref(false)
const aperturePreviewActive = ref(false)
const aperturePreviewStartedByDialog = ref(false)
const aperturePreviewMode = ref<'preview' | 'recording-reuse' | 'unknown'>('unknown')
const aperturePreviewError = ref('')

const apertureLedStatus = ref<'unknown' | 'online' | 'offline'>('unknown')
const apertureLedStatusMessage = ref('')
const apertureLedLoading = ref(false)
const apertureLedActionLoading = ref<string>('')
const apertureLedColor = reactive({ r: 255, g: 255, b: 255 })
const apertureLedBrightness = ref<number>(32)
const apertureLedColorPresets = [
  { name: '白光', r: 255, g: 255, b: 255 },
  { name: '暖白', r: 255, g: 220, b: 180 },
  { name: '红', r: 255, g: 0, b: 0 },
  { name: '绿', r: 0, g: 255, b: 0 },
  { name: '蓝', r: 0, g: 0, b: 255 },
]

const apertureLedStatusText = computed(() => {
  if (apertureLedStatus.value === 'online') return '在线'
  if (apertureLedStatus.value === 'offline') return '离线'
  return '未知'
})
const apertureLedStatusColor = computed(() => {
  if (apertureLedStatus.value === 'online') return 'green'
  if (apertureLedStatus.value === 'offline') return 'red'
  return 'gray'
})

// VideoWall ref
const videoWallRef = ref<InstanceType<typeof VideoWall> | null>(null)

// ============================================================
// 计算属性 / 工具函数
// ============================================================
const isCameraLive = computed(
  () => cameraStreamActive.value || cameraState.value === 'starting' || cameraState.value === 'running',
)

function apiDomainForFrame(): string {
  const base: string =
    (import.meta.env.VITE_API_BASE_URL as string) ||
    (import.meta.env.VITE_API_DOMAIN as string) ||
    ''
  return base.replace(/\/$/, '')
}

function formatSeconds(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return Number(v).toFixed(digits)
}

// ============================================================
// 摄像头状态轮询（来自 Dashboard pollCameraStatus）
// ============================================================
let cameraStatusTimer: number | null = null
let remainingTickTimer: number | null = null
let cameraStatusRequestActive = false
let cameraStatusFailureCount = 0

async function pollCameraStatus(): Promise<void> {
  if (cameraStatusRequestActive) return
  cameraStatusRequestActive = true
  try {
    const resp = await http.get('/api/camera_yzt/status', {
      params: { _t: Date.now() },
      timeout: 30000,
    })
    applyCameraTimingFromStatus(resp.data)
    cameraStatusFailureCount = 0
  } catch (err) {
    cameraStatusFailureCount += 1
    if (cameraStatusFailureCount === 1 || cameraStatusFailureCount % 10 === 0) {
      console.warn('[DataInput][Camera] status poll failed', err)
    }
  } finally {
    cameraStatusRequestActive = false
  }
}

// 状态应用：先按后端 session_mode 分支，再分别处理正式录制 / 临时预览 / 空闲。
// 这是解决「刷新后中间区域错误显示预览流」的关键：
//   - preview 永远不能进入中间主画面。
//   - 只有 session_mode=recording 且有 video_id 才算正式录制。
function applyCameraTimingFromStatus(data: any): void {
  const previousSessionMode = activeSessionMode.value
  const previousState = cameraState.value
  const nextState = String(data.state || 'idle')
  const statusVideoId = getStatusVideoId(data)
  const sessionMode = getBackendSessionMode(data)
  activeSessionMode.value = sessionMode

  // 状态切换时只打印一次会话分类日志（不每 1.5s 重复刷）
  if (sessionMode !== lastLoggedSessionMode) {
    if (lastLoggedSessionMode !== null || sessionMode !== 'idle') {
      console.log(
        `[DataInput][Camera] session classified as ${sessionMode}`,
      )
    }
    lastLoggedSessionMode = sessionMode
  }

  if (sessionMode === 'recording' && statusVideoId) {
    applyFormalRecordingStatus(
      data,
      nextState,
      statusVideoId,
      previousSessionMode,
    )
    return
  }
  if (sessionMode === 'preview') {
    applyPreviewOnlyStatus(data, nextState)
    return
  }
  applyInactiveCameraStatus(
    data,
    nextState,
    previousSessionMode,
    previousState,
  )
}

function applyFormalRecordingStatus(
  data: any,
  nextState: string,
  statusVideoId: string,
  previousSessionMode: CameraSessionMode,
): void {
  cameraState.value = nextState
  cameraStreamActive.value = Boolean(
    data.is_active
    || data.stream_ready
    || nextState === 'starting'
    || nextState === 'running'
    || nextState === 'stopping',
  )
  currentVideoId.value = statusVideoId
  lastRunningVideoId.value = statusVideoId

  recordDurationSeconds.value = (typeof data.frames_captured === 'number')
    ? Math.floor((data.frames_captured || 0) / Math.max(1, cameraConfig.fps || 25))
    : (data.record_duration_seconds ?? null)
  scheduledStopAt.value = data.scheduled_stop_at || null
  remainingSeconds.value = (typeof data.remaining_seconds === 'number') ? data.remaining_seconds : null
  timedRecording.value = Boolean(data.timed_recording)
  timerPending.value = Boolean(data.timer_pending)
  autoStopTriggered.value = Boolean(data.auto_stop_triggered)
  stopReason.value = data.stop_reason || ''
  stopSource.value = data.stop_source || ''

  if (timerPending.value) {
    // 等后端确认启动完成
  } else if (remainingSeconds.value !== null && remainingSeconds.value > 0) {
    startRemainingTick()
  } else {
    stopRemainingTick()
  }

  if (cameraStreamActive.value) {
    startCameraFramePolling()
  } else {
    stopCameraFramePolling()
  }

  // 只有 previousSessionMode===recording 才算真正停止；
  // preview 永远不会触发 MP4 加载和报告轮询。
  const wasRecording = activeSessionMode.value === 'recording'
  const isStopped = nextState === 'idle' || nextState === 'error'
  if (wasRecording && isStopped) {
    const videoId = lastRunningVideoId.value
    if (videoId) {
      if (loadedRecordedVideoId.value !== videoId) {
        void loadRecordedCameraVideo(videoId)
      }
      try {
        localStorage.setItem(LAST_REPORT_VIDEO_ID_KEY, videoId)
      } catch {
        /* ignore */
      }
      reportVideoId.value = videoId
      if (reportState.value !== 'ready') {
        reportState.value = 'queued'
      }
      startReportStatusPolling(videoId)
    }
  } else if (!wasRecording && cameraState.value === 'running') {
    // preview -> recording 或 idle -> running 的早期阶段：
    // 不触发任何停止逻辑。
  }
}

function applyPreviewOnlyStatus(
  data: any,
  nextState: string,
): void {
  // 临时预览绝不能进入中间主画面。
  cameraState.value = 'idle'
  cameraStreamActive.value = false
  stopCameraFramePolling({ clearFrame: true })
  recordDurationSeconds.value = null
  scheduledStopAt.value = null
  remainingSeconds.value = null
  timedRecording.value = false
  timerPending.value = false
  autoStopTriggered.value = false
  stopRemainingTick()
  // 重要：不要修改 currentVideoId / lastRunningVideoId；
  // 不要写报告轮询。
}

function applyInactiveCameraStatus(
  _data: any,
  nextState: string,
  previousSessionMode: CameraSessionMode,
  _previousState: string,
): void {
  cameraState.value = nextState
  // idle / unknown 都不是正式录制。
  // stream_ready 不得用来判断主录制是否活跃。
  cameraStreamActive.value = false
  cameraFrameConsumerActive.value = false
  syncSharedFramePolling({
    clearWhenStopped: !apertureFrameConsumerActive.value,
  })
  recordDurationSeconds.value = null
  scheduledStopAt.value = null
  remainingSeconds.value = null
  timedRecording.value = false
  timerPending.value = false
  autoStopTriggered.value = false
  stopRemainingTick()
  // 只有 recording → idle 才会把 lastRunningVideoId 用作成品视频入口。
  if (
    previousSessionMode === 'recording'
    && (nextState === 'idle' || nextState === 'error')
  ) {
    const videoId = lastRunningVideoId.value
    if (videoId) {
      try {
        localStorage.setItem(LAST_REPORT_VIDEO_ID_KEY, videoId)
      } catch {
        /* ignore */
      }
      reportVideoId.value = videoId
      if (reportState.value !== 'ready') {
        reportState.value = 'queued'
      }
      startReportStatusPolling(videoId)
    }
  }
}

function startCameraFramePolling(): void {
  // 仅当后端处于正式录制会话（session_mode=recording + 有 video_id + 流活跃）
  // 时，主画面才是 JPEG 轮询的合法消费者。
  cameraFrameConsumerActive.value = Boolean(
    activeSessionMode.value === 'recording'
    && Boolean(currentVideoId.value)
    && (
      cameraStreamActive.value
      || cameraState.value === 'starting'
      || cameraState.value === 'running'
      || cameraState.value === 'stopping'
    )
  )
  syncSharedFramePolling()
}

function onCameraFrameError(): void {
  // 委托 composable 统一处理：清空错误帧 + 重连状态
  onSharedFrameImgError()
}

// ============================================================
// 视频检测报告：状态轮询 + 兜底生成
// ============================================================
async function checkVideoReportStatus(videoId: string): Promise<void> {
  const normalizedVideoId = String(videoId || '').trim()
  if (!normalizedVideoId) return
  if (reportChecking.value) return
  if (reportVideoId.value && reportVideoId.value !== normalizedVideoId) {
    // 用户已开始新录制，停止老轮询
    return
  }
  reportChecking.value = true
  try {
    const resp = await http.get(
      `/api/algorithm/videos/${encodeURIComponent(normalizedVideoId)}/result`,
    )
    const payload: any = (resp.data && (resp.data.data || resp.data)) || {}
    const status = String(payload.status || '').toLowerCase()
    const pdfUrl = String(
      payload.report_pdf_url || payload.report_pdf || '',
    ).trim()
    const docxUrl = String(
      payload.report_docx_url || payload.report_docx || '',
    ).trim()

    // 已 ready：停止轮询
    if (status === 'done' && (pdfUrl || docxUrl)) {
      reportVideoId.value = normalizedVideoId
      reportPdfUrl.value = pdfUrl
      reportDocxUrl.value = docxUrl
      reportState.value = 'ready'
      reportError.value = ''
      stopReportStatusPolling()
      return
    }

    // done 但 PDF/DOCX 都没有：兜底发起一次生成（同一 video_id 只 POST 一次）
    if (
      status === 'done' &&
      !pdfUrl &&
      !docxUrl &&
      reportGenerateRequestedVideoId.value !== normalizedVideoId
    ) {
      reportGenerateRequestedVideoId.value = normalizedVideoId
      try {
        await http.post('/api/reports/fish-quality/video-pdf/generate', {
          video_id: normalizedVideoId
        })
      } catch (err) {
        console.warn('[DataInput] 兜底生成视频报告失败：', err)
      }
      reportState.value = 'generating'
      // 继续轮询，等下次 checkVideoReportStatus 拿 URL
      return
    }

    if (status === 'queued') {
      reportState.value = 'queued'
      reportError.value = ''
      return
    }
    if (status === 'processing' || status === 'running') {
      reportState.value = 'processing'
      reportError.value = ''
      return
    }
    if (status === 'failed' || status === 'interrupted' || status === 'incomplete') {
      reportState.value = 'failed'
      reportError.value = String(
        payload.error_message || payload.last_error || '视频检测失败',
      )
      stopReportStatusPolling()
      return
    }

    // 未知 / 其他状态：保持当前，不动
  } catch (err: any) {
    // 404 时：视频可能刚停止入队算法，视为 queued，不直接失败
    const status = err?.response?.status
    if (status === 404) {
      if (reportState.value !== 'recording') {
        reportState.value = 'queued'
      }
      return
    }
    console.warn('[DataInput] 查询算法结果失败：', err)
  } finally {
    reportChecking.value = false
  }
}

function startReportStatusPolling(videoId: string): void {
  stopReportStatusPolling()
  void checkVideoReportStatus(videoId)
  reportStatusTimer = window.setInterval(() => {
    void checkVideoReportStatus(videoId)
  }, 2000)
}

function stopReportStatusPolling(): void {
  if (reportStatusTimer !== null) {
    clearInterval(reportStatusTimer)
    reportStatusTimer = null
  }
}

function tickRemainingSeconds(): void {
  if (!timedRecording.value) return
  if (timerPending.value) return
  if (remainingSeconds.value === null || remainingSeconds.value === undefined) return
  remainingSeconds.value = Math.max(0, remainingSeconds.value - 1)
  if (remainingSeconds.value <= 0) {
    stopRemainingTick()
  }
}

function startRemainingTick(): void {
  if (remainingTickTimer) return
  remainingTickTimer = window.setInterval(tickRemainingSeconds, 1000)
}

function stopRemainingTick(): void {
  if (remainingTickTimer) {
    clearInterval(remainingTickTimer)
    remainingTickTimer = null
  }
}

function startCameraStatusPolling(immediate = true): void {
  if (cameraStatusTimer) return
  if (immediate) {
    void pollCameraStatus()
  }
  cameraStatusTimer = window.setInterval(() => {
    void pollCameraStatus()
  }, 1500)
}

function stopCameraStatusPolling(): void {
  if (cameraStatusTimer) {
    clearInterval(cameraStatusTimer)
    cameraStatusTimer = null
  }
}

// ============================================================
// 录制完成视频加载（与实时流同一 video-wrapper 切换显示）
// ============================================================
function buildApiUrl(path: string): string {
  const base = String(
    (import.meta as ImportMeta).env.VITE_API_DOMAIN || '',
  ).replace(/\/+$/, '')
  if (/^https?:\/\//i.test(path)) return path
  return `${base}/${String(path).replace(/^\/+/, '')}`
}

async function loadRecordedCameraVideo(videoId: string): Promise<void> {
  const normalizedVideoId = String(videoId || '').trim()
  if (!normalizedVideoId) return
  if (
    loadedRecordedVideoId.value === normalizedVideoId &&
    recordedVideoUrl.value
  ) {
    return
  }
  stopRecordedVideoPolling()
  recordedVideoId.value = normalizedVideoId
  recordedVideoLoading.value = true
  recordedVideoError.value = ''

  const tryLoad = async (): Promise<void> => {
    try {
      const base = String(
        (import.meta as ImportMeta).env.VITE_API_DOMAIN || '',
      ).replace(/\/+$/, '')
      const response = await fetch(
        `${base}/api/camera_yzt/video/${encodeURIComponent(normalizedVideoId)}`,
        { method: 'GET', cache: 'no-store' },
      )
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const data = await response.json()
      if (data.playable && data.play_url) {
        recordedVideoUrl.value = buildApiUrl(data.play_url)
        recordedVideoLoading.value = false
        recordedVideoError.value = ''
        loadedRecordedVideoId.value = normalizedVideoId
        stopRecordedVideoPolling()
        return
      }
      if (data.status === 'failed' || data.status === 'interrupted') {
        recordedVideoLoading.value = false
        recordedVideoError.value =
          data.last_error || '录制视频保存失败'
        stopRecordedVideoPolling()
        return
      }
      // 后端还在保存 / 转码：继续轮询
      recordedVideoPollTimer = window.setTimeout(() => {
        void tryLoad()
      }, 1500)
    } catch (error) {
      console.warn('[DataInput] 查询录制视频失败：', error)
      recordedVideoPollTimer = window.setTimeout(() => {
        void tryLoad()
      }, 2000)
    }
  }
  await tryLoad()
}

function stopRecordedVideoPolling(): void {
  if (recordedVideoPollTimer !== null) {
    clearTimeout(recordedVideoPollTimer)
    recordedVideoPollTimer = null
  }
}

function handleRecordedVideoError(): void {
  recordedVideoError.value = '录制视频加载失败，请检查视频文件和编码格式'
}

// ============================================================
// 摄像头按钮：点击逻辑（来自 Dashboard 第2725-2759行）
// ============================================================
async function verifyAndStopFormalRecording(): Promise<void> {
  if (
    cameraStarting.value
    || cameraState.value === 'starting'
    || cameraState.value === 'stopping'
  ) {
    return
  }

  try {
    const response = await http.get(
      '/api/camera_yzt/status',
      {
        params: {
          _t: Date.now(),
        },
      },
    )

    const statusData = response.data || {}
    const sessionMode = getBackendSessionMode(statusData)
    const videoId = getStatusVideoId(statusData)

    applyCameraTimingFromStatus(statusData)

    if (
      sessionMode === 'recording'
      && Boolean(videoId)
    ) {
      await stopCameraCapture()
      return
    }

    // 本地状态曾经认为是 recording，但后端已经不是正式录制。
    // 此时不调用 stop，改为打开录制弹窗。
    recordScheduleDialogVisible.value = true
  } catch (error) {
    console.warn(
      '[DataInput][Camera] verify formal recording failed:',
      error,
    )
    Message.warning('摄像头状态确认失败，请稍后重试')
  }
}

function handleCameraInput(): void {
  if (
    cameraStarting.value
    || cameraState.value === 'starting'
    || cameraState.value === 'stopping'
  ) {
    return
  }

  const hasFormalRecording = Boolean(
    activeSessionMode.value === 'recording'
    && String(currentVideoId.value || '').trim(),
  )

  if (hasFormalRecording) {
    void verifyAndStopFormalRecording()
    return
  }

  // 完整性设置已在光圈设置弹窗中完成；这里只选择立即或计划录制方式。
  recordModeDialogVisible.value = true
}

function chooseImmediateRecording(): void {
  recordModeDialogVisible.value = false
  recordScheduleDialogVisible.value = true
}

function chooseScheduledRecording(): void {
  recordModeDialogVisible.value = false
  recordPlanningDialogVisible.value = true
}

async function handleRecordScheduleConfirm(tasks: RecordTask[]): Promise<void> {
  if (!tasks || tasks.length === 0) return
  const first = tasks[0]
  if (!first) return
  await startCameraCapture(first.durationMinutes)
}

// ============================================================
// 启动 / 停止 录制（用户点击按钮时才调用）
// ============================================================
async function startCameraCapture(durationMinutes: number): Promise<void> {
  if (durationMinutes <= 0) {
    Message.error('录制时长必须大于 0 分钟')
    return
  }
  if (durationMinutes > 1440) {
    Message.error('录制时长不能超过 24 小时')
    return
  }

  // 启动前权威 status 拉取，确认后端实际会话类型。
  // 防止旧 localStorage 误判 + 避免 stream_ready=true 误开启正式录制。
  let currentMode = activeSessionMode.value
  try {
    const statusResp = await http.get('/api/camera_yzt/status', {
      params: { _t: Date.now() }
    })
    currentMode = getBackendSessionMode(statusResp.data || {})
  } catch (err) {
    console.warn('[DataInput][Camera] start preflight failed:', err)
  }

  if (currentMode === 'recording') {
    Message.warning('摄像头正在正式录制，请勿重复启动')
    return
  }

  if (currentMode === 'preview') {
    // 启动正式录制前停止任何 preview 会话（不一定属于本弹窗）。
    const stopped = await stopPreviewBeforeRecording()
    if (!stopped) {
      Message.error('临时预览停止失败，无法开始正式录制')
      return
    }
    const idleReached = await waitForCameraIdle()
    if (!idleReached) {
      Message.error('摄像头预览停止超时，无法开始正式录制')
      return
    }
  }  // 清理上一次录制视频 → 新启动的实时帧优先
  stopRecordedVideoPolling()
  recordedVideoId.value = ''
  recordedVideoUrl.value = ''
  recordedVideoLoading.value = false
  recordedVideoError.value = ''
  loadedRecordedVideoId.value = ''
  // 重置 lastRunningVideoId（即将开始新一轮录制）
  lastRunningVideoId.value = ''
  // 报告状态：开始新录制，停止旧报告轮询
  stopReportStatusPolling()
  reportState.value = 'recording'
  reportVideoId.value = ''
  reportPdfUrl.value = ''
  reportDocxUrl.value = ''
  reportError.value = ''
  reportGenerateRequestedVideoId.value = ''
  // 保留 localStorage 中上一份已生成的报告 video_id（页面刷新兜底）

  cameraStarting.value = true
  try {
    logUserAction({ module: '摄像头控制', action: 'start_camera', message: '点击启动摄像头' })
    const resp = await http.post('/api/camera_yzt/start', {
      stream_url: cameraConfig.stream_url || undefined,
      width: cameraConfig.width,
      height: cameraConfig.height,
      fps: cameraConfig.fps,
      duration_minutes: durationMinutes,
    })
    if (!resp || !resp.data) {
      throw new Error('启动返回为空')
    }
    Message.success('摄像头已启动')
    await pollCameraStatus()
  } catch (err: any) {
    Message.error('摄像头启动失败：' + (err?.message || String(err)))
  } finally {
    cameraStarting.value = false
  }
}

async function stopCameraCapture(): Promise<void> {
  logUserAction({
    module: '摄像头控制',
    action: 'stop_camera',
    message: 'DataInput点击停止摄像头',
  })
  cameraState.value = 'stopping'
  stopCameraFramePolling()
  stopRemainingTick()
  try {
    const resp = await http.post('/api/camera_yzt/stop', {
      reason: 'data_input_camera_button',
      source: 'DataInput.vue',
    })
    const data = resp.data
    if (data?.queued) {
      Message.success(
        `录制已停止，算法已入队${data.job_id ? `，job_id=${data.job_id}` : ''}`,
      )
    } else {
      Message.success('摄像头已停止')
    }
    // 拿到后端返回的 video_id，保存并立即加载成品视频
    const videoId = String(data?.video_id || '').trim()
    if (videoId) {
      currentVideoId.value = videoId
      lastRunningVideoId.value = videoId
      // 保存到 localStorage 供页面刷新 / 路由切换后恢复
      try {
        localStorage.setItem(LAST_REPORT_VIDEO_ID_KEY, videoId)
      } catch {
        // localStorage 可能被禁用
      }
      // 报告状态：开始轮询算法结果
      reportVideoId.value = videoId
      reportState.value = data?.queued ? 'queued' : 'generating'
      startReportStatusPolling(videoId)
      void loadRecordedCameraVideo(videoId)
    }
  } catch (err: any) {
    console.error('[DataInput] 停止摄像头失败：', err)
    Message.error(`停止摄像头失败：${err?.message || String(err)}`)
  } finally {
    // finally 不清空 currentVideoId / lastRunningVideoId
    await pollCameraStatus()
  }
}

function handleCameraFrameClick(_event: MouseEvent): void {
  // DataInput 不需要点位标注
}

// ============================================================
// 光圈设置（来自 Dashboard 第2799-2994行）
// ============================================================
async function handleOpenApertureSettings(): Promise<void> {
  apertureDialogVisible.value = true
  aperturePreviewError.value = ''
  aperturePreviewMode.value = 'unknown'

  let statusData: any = null
  try {
    const resp = await http.get('/api/camera_yzt/status')
    statusData = resp.data || {}
  } catch (e) {
    console.warn('[DataInput][aperture] fetch camera status failed:', e)
  }

  if (statusData) {
    const sessionMode = getBackendSessionMode(statusData)
    const statusVideoId = getStatusVideoId(statusData)
    if (sessionMode === 'recording' && statusVideoId) {
      // 正式录制已存在：复用推流，不调 preview/start
      aperturePreviewActive.value = true
      aperturePreviewStartedByDialog.value = false
      aperturePreviewMode.value = 'recording-reuse'
      startApertureFramePolling()
      void handleApertureLedPing()
      return
    }
    if (sessionMode === 'preview') {
      // 已存在临时预览：本页面打开弹窗可以接管这个预览。
      // 关闭弹窗时将根据 aperturePreviewStartedByDialog 决定是否 stop preview。
      aperturePreviewActive.value = true
      aperturePreviewStartedByDialog.value = true
      aperturePreviewMode.value = 'preview'
      startApertureFramePolling()
      void handleApertureLedPing()
      return
    }
  }

  // idle / unknown：启动新的临时预览。
  aperturePreviewMode.value = 'preview'
  await startAperturePreview()
}

async function startAperturePreview(): Promise<void> {
  aperturePreviewLoading.value = true
  aperturePreviewError.value = ''
  try {
    const resp = await http.post('/api/camera_yzt/preview/start', {
      stream_url: cameraConfig.stream_url || undefined,
      width: cameraConfig.width || 960,
      height: cameraConfig.height || 540,
      fps: cameraConfig.fps || 25,
    })
    const data = resp.data || {}
    if (data.status === 'error') {
      aperturePreviewError.value =
        data.message || data.error || '摄像头预览启动失败'
      Message.error(aperturePreviewError.value)
      return
    }
    aperturePreviewActive.value = true
    aperturePreviewStartedByDialog.value = true
    aperturePreviewMode.value = 'preview'
    startApertureFramePolling()
    void handleApertureLedPing()
  } catch (e: any) {
    aperturePreviewError.value = e?.message || String(e)
    Message.error(`光圈预览启动失败：${aperturePreviewError.value}`)
  } finally {
    aperturePreviewLoading.value = false
  }
}

function startApertureFramePolling(): void {
  // 光圈弹窗开启 = 共享轮询的合法消费者
  apertureFrameConsumerActive.value = true
  syncSharedFramePolling()
}

function stopApertureFramePolling(
  opts: { clearFrame?: boolean } = {},
): void {
  // 仅释放光圈消费者；若主画面 consumer 仍活跃，syncSharedFramePolling
  // 会保持轮询运行，保证正式录制主画面继续刷新。
  apertureFrameConsumerActive.value = false
  syncSharedFramePolling({
    clearWhenStopped: opts.clearFrame ?? true,
  })
}

function stopCameraFramePolling(
  opts: { clearFrame?: boolean } = {},
): void {
  // 释放主画面消费者；syncSharedFramePolling 会按光圈 consumer 是否仍
  // 存活决定是否继续轮询。
  cameraFrameConsumerActive.value = false
  syncSharedFramePolling({
    clearWhenStopped: opts.clearFrame ?? true,
  })
}

// ============================================================
// 等待后端进入 idle（最长 timeoutMs 毫秒）
// 用于正式录制开始前确认 preview 已停止
// ============================================================
async function waitForCameraIdle(timeoutMs = 8000): Promise<boolean> {
  const startedAt = Date.now()
  while (Date.now() - startedAt < timeoutMs) {
    try {
      const resp = await http.get(
        '/api/camera_yzt/status',
        { params: { _t: Date.now() } },
      )
      const data: any = resp.data || {}
      const mode = getBackendSessionMode(data)
      const state = String(data.state || 'idle')
      if (mode === 'idle' && state === 'idle') return true
    } catch (error) {
      console.warn('[DataInput][Camera] waitForCameraIdle failed:', error)
    }
    await new Promise<void>((resolve) => {
      window.setTimeout(resolve, 300)
    })
  }
  return false
}

// ============================================================
// 静默停止当前页面所拥有的临时预览
// 仅当 aperturePreviewStartedByDialog===true 时执行；preview 由 DataInput 启动。
// ============================================================
async function stopOwnedPreviewQuietly(): Promise<boolean> {
  if (!aperturePreviewStartedByDialog.value) return true
  try {
    await http.post('/api/camera_yzt/preview/stop')
    return true
  } catch (error) {
    console.warn('[DataInput][Camera] stop owned preview failed:', error)
    return false
  }
}

// ============================================================
// 启动正式录制前停止任意 preview 会话。
// 与 stopOwnedPreviewQuietly 的区别：
//   - 启动正式录制需要后端不残留 preview
//   - 因此可以停止任何 preview，不需要遵守 ownership 规则
//   - 与 pagehide / 关闭弹窗 路径分离
// ============================================================
async function stopPreviewBeforeRecording(): Promise<boolean> {
  try {
    aperturePreviewActive.value = false
    aperturePreviewStartedByDialog.value = false
    aperturePreviewMode.value = 'unknown'
    apertureFrameConsumerActive.value = false

    syncSharedFramePolling({
      clearWhenStopped: true,
    })

    await http.post('/api/camera_yzt/preview/stop')
    return true
  } catch (error) {
    console.warn(
      '[DataInput][Camera] stop preview before recording failed:',
      error,
    )
    return false
  }
}

// ============================================================
// 孤立预览清理：刷新后页面无弹窗所有权，但后端 preview 仍运行。
// 立即停止 preview，并清空共享轮询。
// 防止重复清理：每页面周期内只调用一次 preview/stop。
// ============================================================
let orphanPreviewCleanupAttempted = false
let orphanPreviewCleanupInFlight = false
async function cleanupOrphanPreviewAfterRefresh(): Promise<void> {
  if (orphanPreviewCleanupInFlight) return
  if (orphanPreviewCleanupAttempted) return
  orphanPreviewCleanupInFlight = true
  orphanPreviewCleanupAttempted = true
  try {
    console.log('[DataInput][Camera] orphan preview cleanup started')
    stopSharedFramePolling({ clearFrame: true })
    await http.post('/api/camera_yzt/preview/stop')
  } catch (error) {
    console.warn(
      '[DataInput][Camera] cleanup orphan preview failed:',
      error,
    )
  } finally {
    orphanPreviewCleanupInFlight = false
    console.log('[DataInput][Camera] orphan preview cleanup finished')
  }
}

function resetOrphanPreviewCleanupGuard(): void {
  orphanPreviewCleanupAttempted = false
  orphanPreviewCleanupInFlight = false
}

// ============================================================
// 页面刷新 / 关闭时立即停止本页面所拥有的临时预览
// ============================================================
function stopOwnedPreviewOnPageHide(): void {
  if (!aperturePreviewStartedByDialog.value) return
  const base = String(
    (import.meta.env.VITE_API_BASE_URL as string | undefined)
    || (import.meta.env.VITE_API_DOMAIN as string | undefined)
    || '',
  ).replace(/\/$/, '')
  const url = `${base}/api/camera_yzt/preview/stop`
  void fetch(url, {
    method: 'POST',
    keepalive: true,
    credentials: 'same-origin',
  }).catch(() => {
    // 页面关闭阶段不再输出错误
  })
}

// ============================================================
// 页面挂载时恢复摄像头状态（区分正式录制 / 临时预览 / 空闲）
// ============================================================
async function restoreCameraState(): Promise<void> {
  try {
    const resp = await http.get(
      '/api/camera_yzt/status',
      { params: { _t: Date.now() } },
    )
    const data: any = resp.data || {}
    const mode = getBackendSessionMode(data)
    const videoId = getStatusVideoId(data)
    if (mode === 'recording' && videoId) {
      applyCameraTimingFromStatus(data)
      return
    }
    if (mode === 'preview') {
      // 刷新后存在临时预览：不要让中间区域显示预览流，立即清理孤立预览。
      applyPreviewOnlyStatus(data, String(data.state || 'idle'))
      await cleanupOrphanPreviewAfterRefresh()
      return
    }
    // 空闲 / 未知：按正常 idle 处理
    applyCameraTimingFromStatus(data)
  } catch (error) {
    console.warn(
      '[DataInput][Camera] restore camera state failed:',
      error,
    )
  }
}

let apertureStopping = false
async function stopAperturePreview(): Promise<void> {
  if (apertureStopping) return
  apertureStopping = true
  try {
    // 记录本次是否由本弹窗拥有 preview 所有权（决定是否调 preview/stop）
    const shouldStopBackendPreview =
      aperturePreviewStartedByDialog.value

    // 释放光圈 consumer；syncSharedFramePolling 自动按主画面 consumer
    // 是否仍活跃决定是否继续轮询。
    aperturePreviewActive.value = false
    apertureFrameConsumerActive.value = false
    syncSharedFramePolling({
      clearWhenStopped: true,
    })

    if (!shouldStopBackendPreview) {
      // 正式录制复用模式：只释放前端，不调 preview/stop
      aperturePreviewMode.value = 'unknown'
      return
    }

    try {
      await http.post('/api/camera_yzt/preview/stop')
    } catch (e) {
      console.warn('[DataInput][aperture] preview/stop failed:', e)
      Message.warning('摄像头预览停止失败，请稍后在系统设置中检查摄像头状态')
    } finally {
      aperturePreviewStartedByDialog.value = false
      aperturePreviewMode.value = 'unknown'
    }
  } finally {
    apertureStopping = false
  }
}

function closeApertureDialog(): void {
  // 先关闭弹窗可见性，再异步停止预览
  // 减少状态轮询再次把活动消费者识别为合法 preview
  apertureDialogVisible.value = false
  void stopAperturePreview()
}

function onApertureFrameError(): void {
  // 委托 composable 统一处理：清空错误帧 + 重连状态
  onSharedFrameImgError()
}

async function handleApertureLedPing(): Promise<void> {
  apertureLedLoading.value = true
  try {
    const resp = await pingLed()
    apertureLedStatus.value = 'online'
    const url = resp.data?.pi_base_url || ''
    apertureLedStatusMessage.value = `树莓派 ${url} 可达`
  } catch (err: any) {
    apertureLedStatus.value = 'offline'
    apertureLedStatusMessage.value = err?.message || '树莓派光圈服务不可达'
  } finally {
    apertureLedLoading.value = false
  }
}

async function runApertureLedAction(
  name: string,
  fn: () => Promise<unknown>,
  successText: string,
): Promise<void> {
  apertureLedActionLoading.value = name
  try {
    await fn()
    Message.success(successText)
  } catch (err: any) {
    Message.error(err?.message || String(err))
  } finally {
    apertureLedActionLoading.value = ''
  }
}

function applyApertureColorPreset(preset: { r: number; g: number; b: number }): void {
  apertureLedColor.r = preset.r
  apertureLedColor.g = preset.g
  apertureLedColor.b = preset.b
}

async function handleApertureLedStart(): Promise<void> {
  await runApertureLedAction('start', () => startLed(), '光圈已开启')
}
async function handleApertureLedOff(): Promise<void> {
  await runApertureLedAction('off', () => offLed(), '光圈已关闭')
}
async function handleApertureApplyColor(): Promise<void> {
  await runApertureLedAction(
    'color',
    () => setLedColor({
      r: Number(apertureLedColor.r) || 0,
      g: Number(apertureLedColor.g) || 0,
      b: Number(apertureLedColor.b) || 0,
    }),
    '光圈颜色已更新',
  )
}
async function handleApertureApplyBrightness(): Promise<void> {
  await runApertureLedAction(
    'brightness',
    () => setLedBrightness({ brightness: Number(apertureLedBrightness.value) || 0 }),
    '光圈亮度已更新',
  )
}

// ============================================================
// 生命周期
// ============================================================
const { fixedScreenStyle } = useFixedScreen(1920, 1080)
// 页面刷新：先恢复后端摄像头状态（区分正式录制 / 临时预览 / 空闲），
// 避免把临时预览流误识别为主录制。然后启动状态轮询。
// 首次状态轮询跳过立即拉取（避免重复请求），后续每 1.5s 继续轮询。
onMounted(async () => {
  window.addEventListener('pagehide', stopOwnedPreviewOnPageHide)
  await loadCameraSettings()
  await restoreCameraState()
  // 后端可能已正式 recording 中：上报按钮可设为 recording
  if (activeSessionMode.value === 'recording') {
    reportState.value = 'recording'
  } else {
    // 非 recording 状态（idle / preview / unknown）：
    // 若 localStorage 仍存留上次正式报告的 video_id，可继续轮询。
    let savedId = ''
    try {
      savedId = String(
        localStorage.getItem(LAST_REPORT_VIDEO_ID_KEY) || '',
      ).trim()
    } catch {
      savedId = ''
    }
    if (savedId) {
      reportVideoId.value = savedId
      startReportStatusPolling(savedId)
    }
  }
  // 轮询恢复：避免重复请求
  startCameraStatusPolling(false)
  // 后端回到 idle 后重置孤立预览 guard，允许未来新的异常预览清理
  watch(activeSessionMode, (mode) => {
    if (mode === 'idle') {
      resetOrphanPreviewCleanupGuard()
    }
  })
})

// 组件销毁时禁止调 /api/camera_yzt/stop，禁止调 stopCameraCapture()。
// 仅清理前端轮询；只有光圈弹窗自己启动的临时 preview 才允许 stop preview 接口。
onBeforeUnmount(() => {
  // 移除 pagehide 监听
  window.removeEventListener('pagehide', stopOwnedPreviewOnPageHide)

  // 清理所有轮询：state / formal recording consumer / shared jpeg
  stopCameraStatusPolling()
  stopRemainingTick()
  stopRecordedVideoPolling()
  stopReportStatusPolling()

  // 释放两个 consumer，再统一停掉共享轮询
  cameraFrameConsumerActive.value = false
  apertureFrameConsumerActive.value = false
  stopSharedFramePolling({ clearFrame: true })

  // 仅当本页面拥有临时 preview 时调用 preview/stop。
  // 禁止在卸载时调用 /api/camera_yzt/stop 或 stopCameraCapture。
  if (aperturePreviewStartedByDialog.value) {
    http
      .post('/api/camera_yzt/preview/stop')
      .catch((error) => {
        console.warn('[DataInput][aperture] preview/stop on unmount failed:', error)
      })
    aperturePreviewActive.value = false
    aperturePreviewStartedByDialog.value = false
  }
})
</script>

<style lang="scss" scoped>
/* ============================================================
 * DataInput 根容器：唯一标志（用于 :deep 跨组件覆盖隔离，绝不影响 Dashboard）
 * ============================================================ */
.dashboard-viewport.data-input-page {
  /* 占位：用于 :deep 选择器锚点 */
}

/* 页面使用视口原生尺寸，不再通过 1920x1080 固定画布缩放。 */
.dashboard-viewport {
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  position: relative;
  background: #000;
}
.dashboard-fixed {
  position: absolute;
  width: 1920px;
  height: 1080px;
  overflow: hidden;
  transform-origin: top left;
}
.dashboard-background {
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: url('@/assets/images/page_bg.png') no-repeat center center;
  background-size: cover;
  position: relative;
  display: flex;
  flex-direction: column;
}
.dashboard-background::before {
  content: '';
  position: absolute;
  inset: 0;
  background: rgb(0 0 0 / 40%);
  pointer-events: none;
  z-index: 0;
}
.dashboard-background > * {
  position: relative;
  z-index: 1;
}

/* ============================================================
 * 跨组件覆盖：让中间栏在 DataInput 页面占满整个 Layout 主体
 * （不能修改 Layout.vue；用 :deep + data-input-page 隔离）
 * ============================================================ */
.dashboard-viewport.data-input-page :deep(.layout-main.no-left) {
  grid-template-columns: minmax(0, 1fr);
}
.dashboard-viewport.data-input-page :deep(.layout-main.no-left > .main-right) {
  display: none;
}
.dashboard-viewport.data-input-page :deep(.layout-main.no-left > .main-middle) {
  width: 100%;
  min-width: 0;
  grid-column: 1;
}

/* ============================================================
 * 中间 LayoutPanel / 视频区 / 控制按钮（中间面板完整铺满）
 * ============================================================ */
.center-panel-wrapper {
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.center-panel-wrapper :deep(.panel-body) {
  flex: 1 1 0;
  width: 100%;
  height: auto;
  min-width: 0;
  min-height: 0;
  display: flex;
  overflow: hidden;
  padding: 8px;
  box-sizing: border-box;
}
.center-panel {
  flex: 1 1 0;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* 视频区：横向铺满 Layout 主体 */
.video-area {
  flex: 1 1 0;
  width: 100%;
  min-width: 0;
  min-height: 0;
  display: flex;
  overflow: hidden;
}
.video-wrapper {
  flex: 1 1 0;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  display: flex;
  align-items: stretch;
  justify-content: stretch;
  overflow: hidden;
  box-sizing: border-box;
  background: #000;
}

/* 跨组件覆盖 VideoWall 根与 .stage / .statusbar */
.video-wrapper :deep(.video-wall) {
  flex: 1 1 0;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-self: stretch;
  overflow: hidden;
  box-sizing: border-box;
}
.video-wrapper :deep(.video-wall .stage) {
  flex: 1 1 0;
  width: 100%;
  height: auto;
  min-width: 0;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  box-sizing: border-box;
}
.video-wrapper :deep(.video-wall .statusbar) {
  flex: 0 0 auto;
  width: 100%;
}

/* 实时摄像头画面：16:9，object-fit: contain 黑边 */
.camera-stream-container,
.recorded-video-wrap {
  position: relative;
  flex: 1 1 0;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  align-self: stretch;
  overflow: hidden;
  box-sizing: border-box;
  background: #000;
}
.camera-stream-img,
.recorded-video {
  display: block;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  object-fit: contain;
  object-position: center center;
  background: #000;
}
.camera-statusbar {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  z-index: 2;
  pointer-events: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  padding: 8px;
  background: rgb(26, 26, 46, 70%);
  border-top: 1px solid rgb(255, 255, 255, 10%);
  .status {
    display: flex;
    gap: 8px;
    .k {
      color: rgb(180, 190, 220);
      flex-shrink: 0;
      min-width: 80px;
    }
    .v {
      color: rgb(220, 240, 255);
      font-family: SarasaMonoSC, Menlo, Monaco, Consolas, monospace;
      word-break: break-all;
    }
  }
  .remaining-value {
    color: rgb(255, 200, 80);
    font-weight: 700;
  }
}

/* 录制完成 MP4：与实时流同一 video-wrapper 显示 */
.recorded-video-meta {
  flex: 0 0 auto;
  min-height: 32px;
  padding: 7px 10px;
  color: rgb(220 240 255);
  background: rgb(26 26 46 / 70%);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 空状态文字 */
.video-status-text {
  color: rgb(180 190 220 / 75%);
  font-size: 16px;
  letter-spacing: 4px;
  text-align: center;
  &.error {
    color: rgb(255, 100, 100);
  }
}

/* 控制按钮组合 */
.control-buttons {
  flex: 0 0 auto;
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 12px 16px;
  flex-wrap: wrap;
}
.camera-group {
  display: flex;
  align-items: center;
  gap: 0;
}
.ctrl-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 12px 22px;
  font-size: 16px;
  font-weight: 600;
  color: rgb(230, 240, 255);
  background: rgb(8, 42, 92, 70%);
  border: 1px solid rgb(120, 200, 255, 45%);
  border-radius: 12px 0 0 12px;
  cursor: pointer;
  transition: all 0.2s ease;
  font-family: AlibabaPuHuiTi, "Noto Sans SC", sans-serif;
  letter-spacing: 2px;
  &:hover:not(:disabled) {
    background: rgb(13, 70, 138, 80%);
    border-color: rgb(120, 220, 255, 70%);
  }
  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
}
.ctrl-btn.settings-btn {
  padding: 12px 14px;
  border-radius: 0 12px 12px 0;
  border-left: none;
  margin-left: 1px;
}
.ctrl-btn.report-view-btn {
  border-radius: 12px;
  margin-left: 12px;
  background: linear-gradient(135deg, #0c63a8 0%, #1c92d2 100%);
  border-color: rgb(120 200 255 / 55%);
  color: #fff;
  &:hover:not(:disabled) {
    background: linear-gradient(135deg, #1476c3 0%, #2da6e5 100%);
    border-color: rgb(160 220 255 / 80%);
    box-shadow: 0 0 14px rgb(120 200 255 / 50%);
  }
  &:disabled {
    opacity: 0.55;
    cursor: not-allowed;
    color: rgb(200 220 240 / 70%);
  }
}
.camera-btn {
  background: linear-gradient(135deg, #005bea 0%, #00c6fb 100%);
  border-color: rgb(120 200 255 / 65%);
  &:hover:not(:disabled) {
    background: linear-gradient(135deg, #0064ff 0%, #1ad0ff 100%);
  }
}

/* ============================================================
 * 光圈设置弹窗样式
 * ============================================================ */
.aperture-dialog {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  color: #1d2129;
}
.aperture-preview {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: #000;
  border-radius: 8px;
  position: relative;
  min-height: 240px;
  overflow: hidden;
  .aperture-preview-state {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    color: rgb(180, 190, 220);
    gap: 8px;
    i {
      font-size: 32px;
    }
    &.error {
      color: rgb(255, 100, 100);
    }
  }
  .aperture-frame-img {
    width: 100%;
    height: auto;
    object-fit: contain;
  }
  .aperture-preview-tip {
    position: absolute;
    bottom: 8px;
    left: 50%;
    transform: translateX(-50%);
    padding: 4px 10px;
    background: rgb(0, 0, 0, 50%);
    color: rgb(220, 240, 255);
    border-radius: 4px;
    font-size: 11px;
    white-space: nowrap;
  }
}
.aperture-controls {
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow-y: auto;
  max-height: 480px;
}
.aperture-mode-tip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 12px;
  background: rgb(245, 245, 245);
  color: #4e5969;
  i {
    color: #165dff;
  }
  &.aperture-mode-recording-reuse {
    background: rgb(255 247 230);
    color: rgb(199, 137, 0);
    i {
      color: rgb(199, 137, 0);
    }
  }
}
.aperture-section {
  padding: 12px;
  border: 1px solid rgb(232, 232, 232);
  border-radius: 6px;
  background: #fafbfc;
  .aperture-section-title {
    font-weight: 600;
    color: #1d2129;
    margin-bottom: 6px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .aperture-section-desc {
    font-size: 12px;
    color: #86909c;
    margin-bottom: 8px;
  }
}
.aperture-color-presets {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  .aperture-color-preset {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 10px;
    background: #fff;
    border: 1px solid rgb(232, 232, 232);
    border-radius: 4px;
    cursor: pointer;
    font-size: 12px;
    color: #1d2129;
    transition: all 0.15s ease;
    &:hover {
      border-color: #165dff;
      color: #165dff;
    }
    .aperture-color-dot {
      width: 12px;
      height: 12px;
      border-radius: 50%;
      border: 1px solid rgb(0, 0, 0, 10%);
    }
  }
}
.aperture-rgb-row {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
  align-items: center;
}
.aperture-color-preview {
  width: 100%;
  height: 32px;
  border-radius: 4px;
  border: 1px solid rgb(232, 232, 232);
  margin-bottom: 10px;
}
.aperture-dialog-footer {
  grid-column: 1 / -1;
  display: flex;
  justify-content: flex-end;
  padding-top: 8px;
  border-top: 1px solid rgb(232, 232, 232);
}

@media (width <= 768px) {
  .control-buttons {
    flex-direction: column;
    gap: 8px;
  }
  .aperture-dialog {
    grid-template-columns: 1fr;
  }
}
</style>
