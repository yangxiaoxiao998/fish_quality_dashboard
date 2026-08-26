<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { http, toErrorMessage } from '@/api/http'
import { captureCalibrationFrame, getCalibrationConfig, runAlgorithmCalibration, saveCalibrationConfig } from '@/api/algorithm'
import { normalizeReportUrl } from '@/utils/reportPath'
import { formatChinaDateTime } from '@/utils/time'
import { logUserAction } from '@/utils/operationLogger'

interface CalibrationFormState {
  enabled: boolean
  camera_id: string
  calibration_id: string
  source_image_path: string
  source_image_url: string
  belt_mask_path: string
  belt_mask_url: string
  rectified_preview_path: string
  rectified_preview_url: string
  transform_matrix: number[][]
  vertical_distance: number | null
  pixel_length: number | null
  mm_per_pixel: number | null
  image_width: number | null
  image_height: number | null
  note: string
  created_at: string
  updated_at: string
  calibration_json: string
  calibration_url: string
}

const WARNING_LINE_PATTERNS = [
  'FutureWarning:',
  'UserWarning:',
  'DeprecationWarning:',
  'PendingDeprecationWarning:',
  'warnings.warn(',
  'Importing from timm.models.layers is deprecated',
  'torch.meshgrid',
  'huggingface_hub',
  'resume_download is deprecated',
  'TRANSFORMERS_CACHE',
  'HF_HUB_OFFLINE',
]

function isClientTimeoutError(err: any): boolean {
  if (!err) return false
  if (err?.code === 'ECONNABORTED' || err?.code === 'ECONNRESET') return true
  const msg = String(err?.message || '')
  return /timeout of \d+ms exceeded/i.test(msg) || /\b15000ms\b/.test(msg)
}

function cleanCalibrationErrorMessage(message: string): string {
  const text = String(message || '')
  if (!text) return ''
  const lines = text.split(/\r?\n/)
  const filtered = lines.filter(
    (line) => !WARNING_LINE_PATTERNS.some((p) => line.includes(p)),
  )
  const result = filtered.join('\n').trim()
  return result
}

const loading = ref(false)
const actionLoading = ref(false)
const realLengthMm = ref<number | null>(null)
const realLengthSaving = ref(false)
const status = ref<any | null>(null)
const lastResult = ref<any | null>(null)
const cameraWidth = ref(960)
const cameraHeight = ref(540)
const cameraFps = ref(25)
const marginConfig = reactive({
  side_margin_ratio: 0.13,
  vertical_margin_ratio: 0.05,
  tracking_start_ratio: 0.4,
  crop_save_ratio: 0.7,
})
const marginSaving = ref(false)

const calibrationForm = reactive<CalibrationFormState>({
  enabled: false,
  camera_id: 'default',
  calibration_id: '',
  source_image_path: '',
  source_image_url: '',
  belt_mask_path: '',
  belt_mask_url: '',
  rectified_preview_path: '',
  rectified_preview_url: '',
  transform_matrix: [],
  vertical_distance: null,
  pixel_length: null,
  mm_per_pixel: null,
  image_width: null,
  image_height: null,
  note: '',
  created_at: '',
  updated_at: '',
  calibration_json: '',
  calibration_url: '',
})

const statusText = computed(() => {
  if (calibrationForm.enabled) return '已标定'
  if (status.value?.error) return '异常'
  return '未标定'
})

const statusColor = computed(() => {
  if (calibrationForm.enabled) return 'green'
  if (status.value?.error) return 'red'
  return 'orange'
})

function imgUrl(v: any): string {
  return normalizeReportUrl(String(v || ''))
}

function normalizeStaticUrl(value: string | undefined | null): string {
  return normalizeReportUrl(String(value || ''))
}

interface CalibrationPreviewItem {
  key: string
  title: string
  tag: string
  path: string
  url: string
}

const calibrationPreviewVisible = ref(false)
const calibrationPreviewIndex = ref(0)

const calibrationPreviewImages = computed<CalibrationPreviewItem[]>(() => {
  return [
    {
      key: 'source',
      title: '原始标定图',
      tag: 'source',
      path: calibrationForm.source_image_path,
      url: normalizeStaticUrl(
        calibrationForm.source_image_url ||
          calibrationForm.source_image_path,
      ),
    },
    {
      key: 'rectified',
      title: '矫正预览图',
      tag: 'rectified',
      path: calibrationForm.rectified_preview_path,
      url: normalizeStaticUrl(
        calibrationForm.rectified_preview_url ||
          calibrationForm.rectified_preview_path,
      ),
    },
    {
      key: 'belt-mask',
      title: '皮带 / 掩膜图',
      tag: 'belt-mask',
      path: calibrationForm.belt_mask_path,
      url: normalizeStaticUrl(
        calibrationForm.belt_mask_url || calibrationForm.belt_mask_path,
      ),
    },
  ].filter((item) => !!item.url)
})

const currentCalibrationPreview = computed<CalibrationPreviewItem | null>(() => {
  return calibrationPreviewImages.value[calibrationPreviewIndex.value] || null
})

function openCalibrationImagePreviewByKey(key: string) {
  const index = calibrationPreviewImages.value.findIndex(
    (item) => item.key === key,
  )
  calibrationPreviewIndex.value = index >= 0 ? index : 0
  calibrationPreviewVisible.value = true
}

function closeCalibrationImagePreview() {
  calibrationPreviewVisible.value = false
}

function showPrevCalibrationImage() {
  const total = calibrationPreviewImages.value.length
  if (total <= 1) return
  calibrationPreviewIndex.value =
    (calibrationPreviewIndex.value - 1 + total) % total
}

function showNextCalibrationImage() {
  const total = calibrationPreviewImages.value.length
  if (total <= 1) return
  calibrationPreviewIndex.value = (calibrationPreviewIndex.value + 1) % total
}

function normalizeMatrix(value: any): number[][] {
  if (!value) return []
  if (Array.isArray(value) && value.length === 3 && Array.isArray(value[0])) {
    return value as number[][]
  }
  if (Array.isArray(value) && value.length === 9) {
    return [
      value.slice(0, 3),
      value.slice(3, 6),
      value.slice(6, 9),
    ] as number[][]
  }
  return []
}

function toNumberOrNull(value: any): number | null {
  if (value === null || value === undefined || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

function applyCalibrationData(payload: any) {
  if (!payload) return
  const raw =
    payload && typeof payload === 'object' && (payload.data || payload.calibration)
      ? payload.data || payload.calibration
      : payload

  status.value = payload

  calibrationForm.enabled = Boolean(
    raw?.enabled ?? payload?.exists ?? payload?.calibrated ?? false,
  )
  calibrationForm.camera_id = raw?.camera_id || payload?.camera_id || 'default'
  calibrationForm.calibration_id = raw?.calibration_id || ''

  calibrationForm.source_image_path = raw?.source_image_path || ''
  calibrationForm.belt_mask_path = raw?.belt_mask_path || ''
  calibrationForm.rectified_preview_path = raw?.rectified_preview_path || ''

  calibrationForm.source_image_url = normalizeReportUrl(
    raw?.source_image_url || raw?.source_image_path || '',
  )
  calibrationForm.belt_mask_url = normalizeReportUrl(
    raw?.belt_mask_url || raw?.belt_mask_path || '',
  )
  calibrationForm.rectified_preview_url = normalizeReportUrl(
    raw?.rectified_preview_url ||
      raw?.adjusted_preview_url ||
      raw?.rectified_preview_path ||
      raw?.adjusted_preview_path ||
      '',
  )

  calibrationForm.transform_matrix = normalizeMatrix(
    raw?.transform_matrix || raw?.perspective_matrix || raw?.matrix || raw?.homography,
  )
  calibrationForm.vertical_distance = toNumberOrNull(
    raw?.vertical_distance ?? raw?.verticalDistance,
  )
  calibrationForm.pixel_length = toNumberOrNull(raw?.pixel_length ?? raw?.pixelLength)
  calibrationForm.mm_per_pixel = toNumberOrNull(raw?.mm_per_pixel ?? raw?.mmPerPixel)
  calibrationForm.image_width = toNumberOrNull(raw?.image_width)
  calibrationForm.image_height = toNumberOrNull(raw?.image_height)
  calibrationForm.note = String(raw?.note || '')
  calibrationForm.created_at = String(raw?.created_at || '')
  calibrationForm.updated_at = String(
    raw?.updated_at || raw?.created_at || payload?.updated_at || '',
  )

  calibrationForm.calibration_json = String(
    payload?.calibration_json || raw?.calibration_json || '',
  )
  calibrationForm.calibration_url = String(
    payload?.calibration_url || raw?.calibration_url || '',
  )
}

async function loadCameraSettings() {
  try {
    const resp = await http.get('/api/camera_yzt/settings')
    const settings = resp.data || {}
    cameraWidth.value = Number(settings.width) || 960
    cameraHeight.value = Number(settings.height) || 540
    cameraFps.value = Number(settings.fps) || 25
  } catch (err) {
    // 摄像头设置读取失败时使用与后端一致的默认分辨率，保证页面仍可操作。
    cameraWidth.value = 960
    cameraHeight.value = 540
    cameraFps.value = 25
  }
}

async function loadMarginConfig(): Promise<void> {
  try {
    const resp = await http.get('/api/calibration/margin-config')
    for (const key of Object.keys(marginConfig) as Array<keyof typeof marginConfig>) {
      if (resp.data?.[key] !== undefined) marginConfig[key] = Number(resp.data[key])
    }
  } catch (err) {
    console.warn('[VisualCalibration] load margin config failed:', err)
  }
}

async function saveMarginConfig(): Promise<void> {
  marginSaving.value = true
  try {
    await http.post('/api/calibration/margin-config', { ...marginConfig })
    Message.success('检测区域参数已保存，后续算法将使用最新配置')
  } catch (err) {
    Message.error(toErrorMessage(err) || '检测区域参数保存失败')
  } finally {
    marginSaving.value = false
  }
}

async function loadStatus(silent = false) {
  if (!silent) {
    logUserAction({
      module: '视觉矫正',
      action: 'refresh_calibration_status',
      message: '刷新视觉矫正状态',
    })
  }
  loading.value = true
  try {
    const resp = await http.get('/api/algorithm/calibration/status')
    const payload = resp.data || {}
    applyCalibrationData(payload)
  } catch (err) {
    Message.error('加载标定状态失败：' + toErrorMessage(err))
    status.value = { exists: false, error: toErrorMessage(err) }
  } finally {
    loading.value = false
  }
}

// 摄像头抓拍错误 → 用户友好文案（隐藏 RTMP / IP / MediaMTX / fish_camera 等技术细节）
function friendlyCaptureError(errCode?: string, fallback?: string): string {
  switch (errCode) {
    case 'CAMERA_DEVICE_NOT_CONFIGURED':
      // 主流程已经不再依赖 FISH_PI_API_BASE 等环境变量；这条分支仅作为兜底。
      return '摄像头连接失败，请检查设备电源和网络连接。'
    case 'CAMERA_DEVICE_START_FAILED':
      return '摄像头连接失败，请检查设备电源和网络连接。'
    case 'CAMERA_STREAM_TIMEOUT':
    case 'FISH_CAMERA_STREAM_NOT_READY':
      return '摄像头画面获取超时，请稍后重试。'
    case 'FFMPEG_NOT_FOUND':
      return '服务器未安装 ffmpeg，无法拍摄标定帧，请联系管理员。'
    case 'CALIBRATION_SAVE_FAILED':
      return '标定帧保存失败，请稍后重试。'
    case 'CALIBRATION_CAPTURE_FAILED':
      return '摄像头画面获取失败，请稍后重试。'
    case 'CAPTURE_TIMEOUT':
      return '抓帧超时，请稍后重试。'
    case 'CAPTURE_FAILED':
      return '抓帧失败，请检查摄像头画面是否正常。'
    default:
      return fallback || '抓拍失败，请稍后重试。'
  }
}

async function captureCalibration() {
  logUserAction({
    module: '视觉矫正',
    action: 'capture_calibration',
    message: '点击抓拍标定',
  })
  actionLoading.value = true
  try {
    // 抓拍前重新读取，确保刚在摄像头设置页保存的分辨率立即生效。
    await loadCameraSettings()
    // 走 api 封装：内置 5 分钟 timeout，避免 http.ts 全局 15000ms 超时。
    // 后端会复用 camera_yzt 链路自动启动树莓派、通知推流、抓帧、停止。
    const resp = await captureCalibrationFrame({
      timeout: 30,
      stop_after_capture: true,
      width: cameraWidth.value,
      height: cameraHeight.value,
      fps: cameraFps.value,
    })
    lastResult.value = resp.data || {}
    applyCalibrationData(lastResult.value)
    Message.success('标定图像已抓拍')
    await loadStatus(true)
  } catch (err: any) {
    // 把 axios 超时类错误翻译成用户友好提示，不显示 AxiosError / 15000ms 等原文
    if (isClientTimeoutError(err)) {
      console.warn('[VisualCalibration] capture-frame timeout:', err)
      Message.error(
        '摄像头连接或标定耗时较长，请稍后重试；如多次失败，请检查设备网络和摄像头画面。',
      )
      return
    }
    const detail =
      err?.response?.data?.detail && typeof err.response.data.detail === 'object'
        ? err.response.data.detail
        : null
    const errCode =
      err?.response?.data?.error_code || (detail && detail.error_code)
    const errMsg =
      err?.response?.data?.error_message ||
      (detail && detail.error_message) ||
      err?.response?.data?.error ||
      toErrorMessage(err)
    // debug 字段仅 console.warn，不在 UI 暴露技术细节
    if (detail && detail.debug_message) {
      console.warn(
        '[VisualCalibration] capture-frame debug:',
        detail.debug_message,
      )
    }
    Message.error(friendlyCaptureError(errCode, errMsg))
  } finally {
    actionLoading.value = false
  }
}

async function runCalibration() {
  logUserAction({
    module: '视觉矫正',
    action: 'run_calibration',
    message: '点击执行视觉矫正',
  })
  actionLoading.value = true
  try {
    const sourceImage =
      lastResult.value?.source_image_path ||
      calibrationForm.source_image_path ||
      lastResult.value?.source_image_url ||
      calibrationForm.source_image_url ||
      lastResult.value?.source_image ||
      lastResult.value?.image_path
    if (!sourceImage) {
      Message.warning(
        '请先抓拍或上传标定图片，确认预览图出现后再执行视觉矫正。',
      )
      return
    }
    // 走 runAlgorithmCalibration api 封装：内置 30 分钟 timeout，避免
    // http.ts 全局 15000ms 超时；adjust_calibration CLI 单次跑可超过 15 秒。
    const resp = await runAlgorithmCalibration({
      source_image: sourceImage,
      camera_id: 'default',
      device: 'cuda',
      overwrite: true,
      real_length_mm: Number(realLengthMm.value),
    })
    lastResult.value = { ...(lastResult.value || {}), ...(resp.data || {}) }
    if (resp.data?.ok === false) {
      Message.error(
        '执行视觉矫正失败：' + (resp.data?.error_message || '未知错误'),
      )
    } else {
      Message.success('视觉矫正已完成，标定参数已更新')
    }
    applyCalibrationData(lastResult.value)
    // 算法执行成功后立刻刷新 status，确保页面回填最新的 transform_matrix / 图片 / 参数
    await loadStatus(true)
  } catch (err: any) {
    // 把 axios 超时类错误翻译成用户友好提示，不显示 AxiosError / 15000ms 等原文
    if (isClientTimeoutError(err)) {
      console.warn('[VisualCalibration] run-calibration timeout:', err)
      Message.error(
        '摄像头连接或标定耗时较长，请稍后重试；如多次失败，请检查设备网络和摄像头画面。',
      )
      return
    }
    const detail = err?.response?.data?.detail || {}
    const cleaned = cleanCalibrationErrorMessage(detail.error_message || '')
    const fallbackMsg =
      '视觉矫正算法进程异常退出，但日志中主要是依赖库 warning，没有明确 Traceback。'
    const msg = cleaned || detail.error_message || fallbackMsg
    const warningLines = detail.warning_lines || 0
    const summary =
      msg +
      (warningLines ? `\n（已过滤 ${warningLines} 条依赖库 warning）` : '') +
      (detail.log_path ? `\n日志路径：${detail.log_path}` : '') +
      (detail.returncode != null ? `\n返回码：${detail.returncode}` : '')
    Message.error({
      content: summary.slice(0, 1500),
      duration: 8000,
    } as any)
  } finally {
    actionLoading.value = false
  }
}

async function clearCalibration() {
  logUserAction({
    module: '视觉矫正',
    action: 'clear_calibration',
    message: '点击清除当前标定',
  })
  Message.warning(
    '当前项目未提供清除标定接口，请在后端确认后再执行。',
  )
}

function handleUpload() {
  logUserAction({
    module: '视觉矫正',
    action: 'upload_calibration_image',
    message: '点击上传标定图片',
  })
  Message.info('上传标定图片入口待后端上传接口确认后接入。')
}

function formatMatrixCell(cell: any): string {
  const n = Number(cell)
  if (!Number.isFinite(n)) return '--'
  if (n === 0) return '0'
  if (Math.abs(n) < 1e-4 || Math.abs(n) >= 1e4) {
    return n.toExponential(6)
  }
  return n.toFixed(6)
}

onMounted(() => {
  loadStatus(true)
  loadCameraSettings()
  loadMarginConfig()
  getCalibrationConfig().then((resp) => { realLengthMm.value = resp.data.real_length_mm })
})

async function saveRealLength() {
  const value = Number(realLengthMm.value)
  if (!Number.isFinite(value) || value <= 0) {
    Message.warning('请输入大于 0 的传送带真实长度')
    return
  }
  realLengthSaving.value = true
  try {
    const resp = await saveCalibrationConfig(value)
    realLengthMm.value = resp.data.real_length_mm
    Message.success('传送带真实长度已保存')
  } catch (err) {
    Message.error('保存传送带真实长度失败：' + toErrorMessage(err))
  } finally {
    realLengthSaving.value = false
  }
}
</script>

<template>
  <div class="calibration-page">
    <div class="page-header">
      <div>
        <h2>视觉矫正配置</h2>
        <p>管理相机标定、透视矫正与当前标定状态。</p>
      </div>
      <a-button :loading="loading" @click="loadStatus(false)">刷新状态</a-button>
    </div>

    <div class="card-grid">
      <a-card :bordered="false" class="setting-card">
        <template #title>当前标定状态</template>
        <div class="status-grid">
          <div class="status-item">
            <span>camera_id</span>
            <strong>{{ calibrationForm.camera_id || 'default' }}</strong>
          </div>
          <div class="status-item">
            <span>状态</span>
            <a-tag :color="statusColor">{{ statusText }}</a-tag>
          </div>
          <div class="status-item">
            <span>标定 ID</span>
            <code>{{ calibrationForm.calibration_id || '--' }}</code>
          </div>
          <div class="status-item">
            <span>标定时间</span>
            <strong>
              {{
                formatChinaDateTime(
                  calibrationForm.updated_at || calibrationForm.created_at,
                ) || '--'
              }}
            </strong>
          </div>
          <div class="status-item full">
            <span>标定文件</span>
            <code>{{ calibrationForm.calibration_json || '--' }}</code>
          </div>
          <div class="status-item">
            <span>变换矩阵</span>
            <code>
              {{
                calibrationForm.transform_matrix.length
                  ? `${calibrationForm.transform_matrix.length}×${calibrationForm.transform_matrix[0]?.length ?? 0}`
                  : '--'
              }}
            </code>
          </div>
          <div class="status-item">
            <span>垂直距离 / 像素长度</span>
            <code>
              {{
                calibrationForm.vertical_distance != null
                  ? calibrationForm.vertical_distance
                  : '--'
              }}
              px /
              {{
                calibrationForm.pixel_length != null
                  ? calibrationForm.pixel_length
                  : '--'
              }}
              px
            </code>
          </div>
          <div class="status-item">
            <span>mm / pixel</span>
            <code>
              {{
                calibrationForm.mm_per_pixel != null
                  ? calibrationForm.mm_per_pixel.toFixed(6)
                  : '--'
              }}
            </code>
          </div>
          <div class="status-item">
            <span>分辨率</span>
            <code>
              {{
                cameraWidth && cameraHeight
                  ? `${cameraWidth} × ${cameraHeight}`
                  : '--'
              }}
            </code>
          </div>
          <div class="status-item full">
            <span>备注</span>
            <code>{{ calibrationForm.note || '--' }}</code>
          </div>
        </div>
      </a-card>

      <a-card :bordered="false" class="setting-card">
        <template #title>操作区</template>
        <a-space wrap>
          <a-button
            type="primary"
            :loading="actionLoading"
            @click="captureCalibration"
          >
            从当前摄像头抓拍标定
          </a-button>
          <a-button @click="handleUpload">上传标定图片</a-button>
          <a-button
            status="success"
            :loading="actionLoading"
            @click="runCalibration"
          >
            执行视觉矫正
          </a-button>
          <a-button status="danger" @click="clearCalibration">
            清除当前标定
          </a-button>
          <a-button :loading="loading" @click="loadStatus(false)">
            刷新状态
          </a-button>
        </a-space>
        <a-space wrap class="calibration-length-row">
          <span class="calibration-input-label">请输入传送带长度</span>
          <a-input-number
            v-model="realLengthMm"
            :min="0.01"
            :precision="2"
            :disabled="actionLoading"
            placeholder="传送带长边真实长度"
          />
          <span>毫米</span>
          <a-button
            type="primary"
            :loading="realLengthSaving"
            :disabled="actionLoading"
            @click="saveRealLength"
          >确定</a-button>
        </a-space>
        <div class="calibration-margin-title">请输入检测区域 ratio</div>
        <a-space wrap class="calibration-margin-row">
          <a-input-number v-model="marginConfig.side_margin_ratio" :min="0" :max="2" :precision="4" placeholder="左右边缘比例" />
          <a-input-number v-model="marginConfig.vertical_margin_ratio" :min="0" :max="2" :precision="4" placeholder="上下边缘比例" />
          <a-input-number v-model="marginConfig.tracking_start_ratio" :min="0" :max="1" :precision="4" placeholder="跟踪开始比例" />
          <a-input-number v-model="marginConfig.crop_save_ratio" :min="0" :max="1" :precision="4" placeholder="裁剪保存比例" />
          <a-button type="primary" :loading="marginSaving" @click="saveMarginConfig">确定</a-button>
        </a-space>
        <div class="calibration-margin-hint">左右边缘比例、上下边缘比例、跟踪开始比例、裁剪保存比例</div>
      </a-card>
    </div>

    <a-card :bordered="false" class="setting-card">
      <template #title>标定图像预览</template>
      <div class="calibration-image-grid">
        <div class="calibration-image-card">
          <div class="calibration-image-head">
            <span class="calibration-image-title">原始标定图</span>
            <span class="calibration-image-tag">source</span>
          </div>
          <div
            v-if="calibrationForm.source_image_path"
            class="calibration-thumb-box"
            title="双击打开大图"
            @dblclick="openCalibrationImagePreviewByKey('source')"
          >
            <img
              :src="
                normalizeStaticUrl(
                  calibrationForm.source_image_url ||
                    calibrationForm.source_image_path,
                )
              "
              alt="原始标定图"
              class="calibration-thumb-img"
            />
          </div>
          <a-empty v-else description="暂无原始标定图" />
          <div class="calibration-image-path">
            {{ calibrationForm.source_image_path || '--' }}
          </div>
        </div>

        <div class="calibration-image-card">
          <div class="calibration-image-head">
            <span class="calibration-image-title">皮带 / 掩膜图</span>
            <span class="calibration-image-tag">belt-mask</span>
          </div>
          <div
            v-if="calibrationForm.belt_mask_path"
            class="calibration-thumb-box"
            title="双击打开大图"
            @dblclick="openCalibrationImagePreviewByKey('belt-mask')"
          >
            <img
              :src="
                normalizeStaticUrl(
                  calibrationForm.belt_mask_url ||
                    calibrationForm.belt_mask_path,
                )
              "
              alt="皮带 / 掩膜图"
              class="calibration-thumb-img"
            />
          </div>
          <a-empty v-else description="暂无皮带 / 掩膜图" />
          <div class="calibration-image-path">
            {{ calibrationForm.belt_mask_path || '--' }}
          </div>
        </div>

        <div class="calibration-image-card">
          <div class="calibration-image-head">
            <span class="calibration-image-title">矫正预览图</span>
            <span class="calibration-image-tag">rectified</span>
          </div>
          <div
            v-if="calibrationForm.rectified_preview_path"
            class="calibration-thumb-box"
            title="双击打开大图"
            @dblclick="openCalibrationImagePreviewByKey('rectified')"
          >
            <img
              :src="
                normalizeStaticUrl(
                  calibrationForm.rectified_preview_url ||
                    calibrationForm.rectified_preview_path,
                )
              "
              alt="矫正预览图"
              class="calibration-thumb-img"
            />
          </div>
          <a-empty v-else description="暂无矫正预览图" />
          <div class="calibration-image-path">
            {{ calibrationForm.rectified_preview_path || '--' }}
          </div>
        </div>

      </div>
    </a-card>

    <a-card :bordered="false" class="setting-card">
      <template #title>透视变换矩阵 (3 × 3)</template>
      <table v-if="calibrationForm.transform_matrix.length === 3" class="matrix-table">
        <tbody>
          <tr v-for="(row, i) in calibrationForm.transform_matrix" :key="i">
            <td v-for="(cell, j) in row" :key="j">
              {{ formatMatrixCell(cell) }}
            </td>
          </tr>
        </tbody>
      </table>
      <a-empty v-else description="暂无透视矩阵" />
    </a-card>

    <a-card :bordered="false" class="setting-card">
      <template #title>最近一次执行返回</template>
      <a-empty
        v-if="!lastResult"
        description="暂无最近执行结果，先抓拍并执行视觉矫正。"
      />
      <pre v-else class="json-preview">{{
        JSON.stringify(lastResult, null, 2)
      }}</pre>
    </a-card>

    <a-modal
      v-model:visible="calibrationPreviewVisible"
      :title="currentCalibrationPreview?.title || '标定图像预览'"
      width="min(920px, 88vw)"
      :footer="false"
      unmount-on-close
      @cancel="closeCalibrationImagePreview"
    >
      <div class="fixed-image-preview-card">
        <div class="fixed-image-preview-meta">
          <span class="fixed-image-preview-tag">
            {{ currentCalibrationPreview?.tag || '--' }}
          </span>
          <span class="fixed-image-preview-path">
            {{ currentCalibrationPreview?.path || '--' }}
          </span>
        </div>

        <div class="fixed-image-stage">
          <button
            v-if="calibrationPreviewImages.length > 1"
            class="image-inner-nav image-inner-nav-left"
            type="button"
            aria-label="上一张"
            @click.stop="showPrevCalibrationImage"
          >
            &lt;
          </button>

          <img
            v-if="currentCalibrationPreview?.url"
            :src="currentCalibrationPreview.url"
            :alt="currentCalibrationPreview.title"
            class="fixed-image-preview-img"
          />
          <a-empty v-else description="暂无图片" />

          <button
            v-if="calibrationPreviewImages.length > 1"
            class="image-inner-nav image-inner-nav-right"
            type="button"
            aria-label="下一张"
            @click.stop="showNextCalibrationImage"
          >
            &gt;
          </button>
        </div>

        <div class="fixed-image-preview-footer">
          <span>
            {{ calibrationPreviewIndex + 1 }} /
            {{ calibrationPreviewImages.length }}
          </span>
          <a-button @click="closeCalibrationImagePreview">关闭</a-button>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<style scoped>
.calibration-page {
  min-height: 100%;
  padding: 16px;
  box-sizing: border-box;
  background: #f5f7fb;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.page-header,
.setting-card {
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 6px 18px rgb(27 78 155 / 8%);
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 20px;
}
.page-header h2 {
  margin: 0 0 6px;
  font-size: 18px;
  color: #1d2129;
}
.page-header p {
  margin: 0;
  color: #86909c;
}
.calibration-input-label,
.calibration-margin-title,
.calibration-margin-hint {
  color: #4e5969;
  font-size: 13px;
}
.calibration-margin-title { margin-top: 12px; margin-bottom: 8px; font-weight: 600; }
.calibration-margin-hint { margin-top: 6px; color: #86909c; }
.card-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
}
.status-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.status-item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  background: #f7fbff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  min-width: 0;
}
.status-item.full {
  grid-column: 1 / -1;
}
.status-item span {
  color: #86909c;
  white-space: nowrap;
}
.status-item strong,
.status-item code {
  color: #1d2129;
  font-weight: 600;
  word-break: break-all;
  text-align: right;
  min-width: 0;
}
.preview-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.preview-cell {
  min-width: 0;
}
.preview-title {
  margin-bottom: 8px;
  color: #4e5969;
  font-weight: 600;
}
.preview-path {
  margin-top: 6px;
  font-size: 12px;
  color: #86909c;
  word-break: break-all;
}
.calibration-preview-img {
  width: 100%;
  height: 220px;
  object-fit: contain;
  background: #061226;
  border-radius: 8px;
  border: 1px solid rgb(47 123 255 / 35%);
}
.calibration-image-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  width: 100%;
  max-width: 100%;
  min-width: 0;
}
.calibration-image-card {
  min-width: 0;
  border: 1px solid #e5e6eb;
  border-radius: 12px;
  background: #fff;
  padding: 12px;
  box-sizing: border-box;
}
.calibration-image-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}
.calibration-image-title {
  font-size: 14px;
  font-weight: 600;
  color: #1d2129;
}
.calibration-image-tag {
  flex: 0 0 auto;
  padding: 2px 8px;
  border-radius: 999px;
  background: #eef4ff;
  color: #165dff;
  font-size: 12px;
}
.calibration-thumb-box {
  width: 100%;
  height: 180px;
  border-radius: 10px;
  background: #f7f8fa;
  border: 1px dashed #c9cdd4;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  cursor: zoom-in;
  user-select: none;
}
.calibration-thumb-box:hover {
  border-color: #165dff;
  background: #f0f5ff;
}
.calibration-thumb-img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  display: block;
}
.calibration-image-path {
  margin-top: 8px;
  color: #86909c;
  font-size: 12px;
  line-height: 1.5;
  word-break: break-all;
}
.calibration-preview-modal {
  width: 100%;
}
.fixed-image-preview-card {
  width: 100%;
  box-sizing: border-box;
}
.fixed-image-preview-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  margin-bottom: 12px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #f7f8fa;
}
.fixed-image-preview-tag {
  flex: 0 0 auto;
  padding: 2px 8px;
  border-radius: 999px;
  background: #eef4ff;
  color: #165dff;
  font-size: 12px;
  font-weight: 600;
}
.fixed-image-preview-path {
  min-width: 0;
  color: #4e5969;
  font-size: 12px;
  word-break: break-all;
}
.fixed-image-stage {
  position: relative;
  width: 100%;
  height: clamp(420px, 62vh, 640px);
  border-radius: 12px;
  background: #0b1220;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}
.fixed-image-preview-img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  display: block;
  user-select: none;
}
.fixed-image-preview-footer {
  margin-top: 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #4e5969;
  font-size: 13px;
}
.image-inner-nav {
  position: absolute;
  top: 50%;
  z-index: 5;
  transform: translateY(-50%);
  width: 42px;
  height: 72px;
  border: none;
  border-radius: 8px;
  background: rgb(0, 0, 0, 38%);
  color: #fff;
  font-size: 34px;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}
.image-inner-nav:hover {
  background: rgb(0, 0, 0, 58%);
}
.image-inner-nav-left {
  left: 12px;
}
.image-inner-nav-right {
  right: 12px;
}

@media (width <= 768px) {
  .fixed-image-stage {
    height: 58vh;
  }
  .image-inner-nav {
    width: 36px;
    height: 60px;
    font-size: 28px;
  }
  .image-inner-nav-left {
    left: 8px;
  }
  .image-inner-nav-right {
    right: 8px;
  }
}
.matrix-table {
  border-collapse: collapse;
  width: 100%;
  max-width: 720px;
  margin: 0 auto;
  font-family: 'JetBrains Mono', Menlo, Consolas, monospace;
  font-size: 13px;
}
.matrix-table td {
  border: 1px solid #dce8ff;
  padding: 10px 14px;
  text-align: center;
  background: #f7faff;
  color: #1d2129;
}
.json-preview {
  max-height: 280px;
  overflow: auto;
  padding: 12px;
  background: #061226;
  color: #12d6df;
  border-radius: 8px;
}

@media (width <= 1200px) {
  .card-grid,
  .preview-grid,
  .calibration-image-grid {
    grid-template-columns: 1fr;
  }
}
</style>
