<template>
  <div class="dashboard-viewport video-report-display-page">
    <div class="dashboard-fixed" :style="fixedScreenStyle">
  <div class="dashboard-background">
    <Layout>
      <!-- 左侧区域：4个ECharts图表 (20%) —— 视频检测报告不需要 -->
      <!-- 中间区域：视频检测报告（单中间列铺满） -->
      <template #middle>
        <LayoutPanel title="视频检测报告" class="video-report-panel">
          <div class="video-report-root">

            <!-- 顶部工具栏 -->
            <div class="video-report-toolbar">
              <div class="video-report-toolbar-left">
                <span class="video-report-toolbar-label">视频 ID</span>
                <span class="video-report-toolbar-value">
                  {{ currentVideoId || '—' }}
                </span>
                <span
                  class="video-report-state-chip"
                  :class="stateClass"
                >
                  {{ stateText }}
                </span>
              </div>
              <div class="video-report-toolbar-right">
                <button
                  type="button"
                  class="video-report-tab"
                  :class="{ active: reportMode === 'online' }"
                  @click="switchMode('online')"
                >
                  在线报告
                </button>
                <button
                  type="button"
                  class="video-report-tab"
                  :class="{ active: reportMode === 'pdf' }"
                  @click="switchMode('pdf')"
                  :disabled="!reportPdfUrl"
                >
                  PDF 原版
                </button>
                <a
                  v-if="reportPdfUrl"
                  :href="reportPdfUrl"
                  class="video-report-action"
                  download
                  @click="onAction('download_video_report_pdf')"
                >
                  下载 PDF
                </a>
                <a
                  v-if="reportDocxUrl"
                  :href="reportDocxUrl"
                  class="video-report-action"
                  download
                  @click="onAction('download_video_report_docx')"
                >
                  下载 Word
                </a>
                <button
                  type="button"
                  class="video-report-action"
                  @click="goDashboardResult"
                >
                  跳转大屏
                </button>
              </div>
            </div>

            <!-- 内容区 -->
            <div class="video-report-body">
              <!-- loading -->
              <div
                v-if="loading"
                class="video-report-state-empty"
              >
                正在加载视频检测报告...
              </div>

              <!-- 失败 -->
              <div
                v-else-if="reportError"
                class="video-report-state-empty error"
              >
                报告加载失败：{{ reportError }}
              </div>

              <!-- 暂无报告 -->
              <div
                v-else-if="!currentVideoId"
                class="video-report-state-empty"
              >
                暂无已生成的视频检测报告
              </div>

              <!-- 处理中（queued / processing / generating） -->
              <div
                v-else-if="reportMode === 'online' && (taskStatus === 'queued' || taskStatus === 'processing' || taskStatus === 'running' || taskStatus === 'generating')"
                class="video-report-state-empty"
              >
                <i class="fa-solid fa-spinner fa-spin" />
                <span>{{ stateText }}...</span>
              </div>

              <!-- 在线报告 -->
              <div
                v-else-if="reportMode === 'online'"
                class="online-report-scroll"
              >
                <EmbeddedVideoReport
                  :key="currentVideoId"
                  :video-id="currentVideoId"
                  :task-status="taskStatus"
                  embedded
                />
              </div>

              <!-- PDF 模式 -->
              <div
                v-else
                class="pdf-viewer-wrap"
              >
                <iframe
                  v-if="reportPdfUrl && versionedPdfUrl"
                  :src="versionedPdfUrl"
                  class="pdf-viewer"
                  title="视频检测报告PDF预览"
                />
                <div
                  v-else
                  class="video-report-state-empty"
                >
                  PDF 文件尚未生成，可先查看在线报告或下载 Word。
                </div>
              </div>
            </div>
          </div>
        </LayoutPanel>
      </template>
    </Layout>
  </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import Layout from '@/layout/Layout.vue'
import { LayoutPanel } from '@/layout'
import EmbeddedVideoReport from '@/components/report/EmbeddedVideoReport.vue'
import { http, toErrorMessage } from '@/api/http'
import { normalizeReportUrl } from '@/utils/reportPath'
import { logUserAction } from '@/utils/operationLogger'
import { useFixedScreen } from '@/utils/useFixedScreen'

const route = useRoute()
const router = useRouter()
const { fixedScreenStyle } = useFixedScreen(1920, 1080)

// 状态
const currentVideoId = ref<string>('')
const loading = ref<boolean>(false)
const taskStatus = ref<string>('')
const reportError = ref<string>('')
const reportPdfUrl = ref<string>('')
const reportDocxUrl = ref<string>('')
const reportMode = ref<'online' | 'pdf'>('online')
let statusPollTimer: number | null = null

const mediaVersion = ref<number>(Date.now())
const versionedPdfUrl = computed(() => {
  if (!reportPdfUrl.value) return ''
  const sep = reportPdfUrl.value.includes('?') ? '&' : '?'
  return `${reportPdfUrl.value}${sep}_v=${mediaVersion.value}`
})

const stateText = computed(() => {
  const s = taskStatus.value.toLowerCase()
  if (s === 'queued') return '等待算法处理'
  if (s === 'processing' || s === 'running') return '算法处理中'
  if (s === 'generating') return '报告生成中'
  if (s === 'failed' || s === 'interrupted' || s === 'incomplete') return '检测失败'
  if (s === 'done') return reportPdfUrl.value || reportDocxUrl.value ? '报告已就绪' : '报告已就绪'
  return s ? s : ''
})

const stateClass = computed(() => {
  const s = taskStatus.value.toLowerCase()
  if (!s && !reportError.value && !currentVideoId.value) return 'video-report-state-chip-idle'
  if (s === 'queued') return 'video-report-state-chip-pending'
  if (s === 'processing' || s === 'running' || s === 'generating') return 'video-report-state-chip-progress'
  if (s === 'failed' || s === 'interrupted' || s === 'incomplete') return 'video-report-state-chip-failed'
  if (s === 'done') return 'video-report-state-chip-ready'
  if (reportError.value) return 'video-report-state-chip-failed'
  return 'video-report-state-chip-idle'
})

// ============================================================
// 操作日志（失败不阻断页面）
// ============================================================
function onAction(action: string): void {
  void logUserAction({
    module: '视频检测报告',
    action,
    targetType: 'video',
    targetId: currentVideoId.value || '',
    message: `视频检测报告操作：${action}`,
  })
}

function switchMode(mode: 'online' | 'pdf'): void {
  reportMode.value = mode
  if (mode === 'pdf') {
    onAction('preview_video_report_pdf')
  }
}

// ============================================================
// video_id 解析优先级：route.params.videoId > route.query.video_id
// ============================================================
function resolveInitialVideoId(): string {
  const paramVal = String(route.params.videoId || '').trim()
  if (paramVal) return paramVal
  const queryVal = String(route.query.video_id || '').trim()
  if (queryVal) return queryVal
  return ''
}

async function fetchLatestReportVideoId(): Promise<string> {
  try {
    const resp = await http.get('/api/reports/history', {
      params: {
        report_type: 'video_fish_quality',
        status: 'generated',
        page: 1,
        page_size: 1
      }
    })
    const data: any = resp.data || {}
    const items: any[] = Array.isArray(data.items) ? data.items : []
    for (const it of items) {
      const vid = String(it.video_id || '').trim()
      if (vid) return vid
    }
  } catch (err) {
    console.warn('[VideoDetectionReport] 查询最近报告失败：', err)
  }
  return ''
}

async function loadReportFor(videoId: string): Promise<void> {
  if (!videoId) return
  loading.value = true
  reportError.value = ''
  reportPdfUrl.value = ''
  reportDocxUrl.value = ''
  mediaVersion.value = Date.now()
  try {
    const resp = await http.get(
      `/api/algorithm/videos/${encodeURIComponent(videoId)}/result`
    )
    const payload: any = (resp.data && (resp.data.data || resp.data)) || {}
    taskStatus.value = String(payload.status || '').toLowerCase()
    const pdf = normalizeReportUrl(
      payload.report_pdf_url || payload.report_pdf
    )
    const docx = normalizeReportUrl(
      payload.report_docx_url || payload.report_docx
    )
    reportPdfUrl.value = pdf
    reportDocxUrl.value = docx

    // done 但 PDF/DOCX 都没有：兜底发起一次视频报告生成（同一 video 只生成一次）
    if (
      taskStatus.value === 'done' &&
      !pdf &&
      !docx
    ) {
      taskStatus.value = 'generating'
      try {
        await http.post('/api/reports/fish-quality/video-pdf/generate', {
          video_id: videoId
        })
      } catch (err) {
        console.warn('[VideoDetectionReport] 兜底生成视频报告失败：', err)
      }
      return
    }

    if (
      taskStatus.value === 'failed'
      || taskStatus.value === 'interrupted'
      || taskStatus.value === 'incomplete'
    ) {
      reportError.value = String(
        payload.error_message || payload.last_error || '视频检测失败'
      )
    }
  } catch (err: any) {
    const status = err?.response?.status
    if (status === 404) {
      taskStatus.value = 'queued'
      reportError.value = '正在等待算法处理结果...'
    } else {
      reportError.value = toErrorMessage(err)
    }
  } finally {
    loading.value = false
  }
}

async function init(): Promise<void> {
  loading.value = true
  try {
    let videoId = resolveInitialVideoId()
    if (!videoId) {
      videoId = await fetchLatestReportVideoId()
    }
    currentVideoId.value = videoId
    if (videoId) {
      await loadReportFor(videoId)
      startStatusPolling(videoId)
    } else {
      loading.value = false
    }
  } finally {
    loading.value = false
  }
}

function startStatusPolling(videoId: string): void {
  stopStatusPolling()
  if (!videoId) return
  statusPollTimer = window.setInterval(async () => {
    await loadReportFor(videoId)
    const s = taskStatus.value
    if (
      s === 'done' ||
      s === 'failed' ||
      s === 'interrupted' ||
      s === 'incomplete'
    ) {
      stopStatusPolling()
    }
  }, 2000)
}

function stopStatusPolling(): void {
  if (statusPollTimer !== null) {
    clearInterval(statusPollTimer)
    statusPollTimer = null
  }
}

// 跳转大屏
function goDashboardResult(): void {
  if (!currentVideoId.value) {
    router.push('/dashboard')
    return
  }
  onAction('open_dashboard_result')
  router.push({
    path: '/dashboard',
    query: {
      mode: 'result',
      video_id: currentVideoId.value
    }
  })
}

onMounted(() => {
  void init()
})

watch(
  () => [String(route.params.videoId || ''), String(route.query.video_id || '')],
  async ([newParam, newQuery]) => {
    stopStatusPolling()
    reportError.value = ''
    reportPdfUrl.value = ''
    reportDocxUrl.value = ''
    reportMode.value = 'online'
    const videoId = (newParam || newQuery || '').trim()
    if (videoId) {
      currentVideoId.value = videoId
      await loadReportFor(videoId)
      startStatusPolling(videoId)
    } else {
      currentVideoId.value = ''
      await init()
    }
  }
)

onBeforeUnmount(() => {
  stopStatusPolling()
})
</script>

<style lang="scss" scoped>
/* 本页面用 video-report-display-page 命名空间隔离 :deep 选择器。 */
.dashboard-viewport.video-report-display-page {
  /* 占位 */
}

/* 使用浏览器视口原生尺寸，不再缩放固定设计画布。 */
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
 * 跨组件覆盖：单列铺满 Layout 主体（不能修改 Layout.vue）
 * 与其它 dashboard 页面命名空间隔离
 * ============================================================ */
.video-report-display-page :deep(.layout-main.no-left) {
  grid-template-columns: minmax(0, 1fr);
}
.video-report-display-page :deep(.layout-main.no-left > .main-right) {
  display: none;
}
.video-report-display-page :deep(.layout-main.no-left > .main-middle) {
  width: 100%;
  min-width: 0;
  grid-column: 1;
}
.video-report-panel {
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
}
.video-report-panel :deep(.panel-body) {
  height: calc(100% - 65px);
  min-height: 0;
  overflow: hidden;
}

/* ============================================================
 * 报告页面：根 + 内容
 * ============================================================ */
.video-report-root {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  color: rgb(220 240 255);
  font-family: AlibabaPuHuiTi, "Noto Sans SC", sans-serif;
  background: rgb(5 25 60 / 35%);
  border-radius: 8px;
  overflow: hidden;
}

/* 顶部工具栏 */
.video-report-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 18px;
  border-bottom: 1px solid rgb(80 170 255 / 35%);
  background: rgb(5 25 60 / 82%);
  flex-wrap: wrap;
  gap: 12px;
  flex-shrink: 0;
}
.video-report-toolbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.video-report-toolbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.video-report-toolbar-label {
  color: rgb(180 210 240 / 80%);
  font-size: 13px;
  letter-spacing: 1px;
}
.video-report-toolbar-value {
  color: #fff;
  font-family: SarasaMonoSC, Menlo, Monaco, Consolas, monospace;
  font-size: 14px;
  word-break: break-all;
}
.video-report-state-chip {
  display: inline-block;
  padding: 4px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 1px;
  border: 1px solid rgb(255 255 255 / 20%);
}
.video-report-state-chip-idle {
  background: rgb(80 100 130 / 60%);
  color: rgb(220 230 240);
}
.video-report-state-chip-pending {
  background: rgb(160 130 50 / 60%);
  color: rgb(255 240 200);
  border-color: rgb(255 215 102 / 60%);
}
.video-report-state-chip-progress {
  background: rgb(40 90 160 / 70%);
  color: #fff;
  border-color: rgb(120 200 255 / 70%);
}
.video-report-state-chip-ready {
  background: rgb(20 150 100 / 70%);
  color: #fff;
  border-color: rgb(60 230 160 / 70%);
}
.video-report-state-chip-failed {
  background: rgb(180 40 60 / 70%);
  color: #fff;
  border-color: rgb(255 130 130 / 70%);
}

/* Tab / Action 按钮（科技蓝统一风格） */
.video-report-tab,
.video-report-action {
  display: inline-flex;
  align-items: center;
  height: 34px;
  padding: 0 16px;
  border-radius: 6px;
  border: 1px solid rgb(120 200 255 / 45%);
  background: rgb(8 42 92 / 60%);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 2px;
  cursor: pointer;
  text-decoration: none;
  transition: background 0.2s ease, border-color 0.2s ease;
}
.video-report-tab:hover,
.video-report-action:hover {
  background: rgb(13 70 138 / 80%);
  border-color: rgb(120 220 255 / 70%);
  color: #fff;
}
.video-report-tab.active {
  background: linear-gradient(135deg, #005bea 0%, #00c6fb 100%);
  border-color: rgb(120 200 255 / 80%);
}
.video-report-tab:disabled,
.video-report-action:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* 内容区 */
.video-report-body {
  flex: 1;
  min-height: 0;
  display: flex;
  background: #020c1c;
  overflow: hidden;
}

/* 在线报告容器 */
.online-report-scroll {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  background: rgb(5 25 60 / 30%);
  padding: 8px;
}

/* PDF iframe 容器 */
.pdf-viewer-wrap {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  align-items: stretch;
  justify-content: stretch;
  padding: 12px;
  background: #020c1c;
}
.pdf-viewer {
  width: 100%;
  height: 100%;
  min-height: 0;
  border: 1px solid rgb(80 170 255 / 55%);
  border-radius: 6px;
  background: rgb(15 40 70 / 80%);
  box-shadow: 0 0 18px rgb(0 120 255 / 30%);
}

/* 空 / loading / failed */
.video-report-state-empty {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: rgb(180 210 240 / 75%);
  font-size: 16px;
  letter-spacing: 4px;
  padding: 24px;
  text-align: center;
  &.error {
    color: rgb(255 120 120);
  }
  i {
    font-size: 32px;
  }
}

@media (width <= 768px) {
  .video-report-toolbar {
    flex-direction: column;
    align-items: flex-start;
  }
  .video-report-toolbar-right {
    width: 100%;
    overflow-x: auto;
  }
}
</style>
