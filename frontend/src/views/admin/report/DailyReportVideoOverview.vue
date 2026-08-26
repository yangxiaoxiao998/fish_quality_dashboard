<template>
  <div class="report-page-layout">
    <main class="report-main">
      <div class="report-header-card">
        <div>
          <h2 class="report-page-title">当日视频任务总览报告</h2>
          <div class="report-page-subtitle">
            默认展示距离当前时间最近录制的视频检测报告。
          </div>
        </div>
        <a-button
          type="primary"
          :loading="loading"
          @click="handleRefresh"
        >
          刷新
        </a-button>
      </div>

      <ReportSection
        id="video-search-block"
        title="视频检索入口"
        subtitle="选择日期和视频，查看对应单视频检测报告。"
      >
        <div class="video-select-row">
          <a-date-picker
            v-model="reportDate"
            value-format="YYYY-MM-DD"
            style="width: 160px"
            @change="handleDateChange"
          />

          <a-select
            v-model="selectedVideoId"
            placeholder="请选择视频"
            allow-search
            allow-clear
            filter-option
            :loading="loading"
            :max-tag-count="4"
            style="width: 520px"
            popup-class="video-select-dropdown-4"
            @change="handleSelectedVideoChange"
          >
            <a-option
              v-for="v in selectableVideos"
              :key="v.video_id"
              :value="v.video_id"
            >
              {{ videoOptionLabel(v) }}
            </a-option>
          </a-select>

          <a-button @click="showSelectedVideoReport">
            查看单视频报告
          </a-button>
        </div>

        <div
          v-if="selectedVideoInfo"
          class="selected-video-info"
        >
          当前展示视频：<code>{{ selectedVideoInfo.video_id }}</code>
          ｜录制开始时间：{{ formatVideoStartTime(selectedVideoInfo) }}
          ｜视频时长：{{ formatDuration(selectedVideoInfo.video_duration_seconds) }}
          ｜处理耗时：{{ formatDuration(selectedVideoInfo.processing_duration_seconds) }}
        </div>

        <a-empty
          v-if="!loading && !selectableVideos.length"
          description="当前日期暂无视频任务"
        />
      </ReportSection>

      <section
        v-if="selectedReportVideoId"
        id="embedded-video-report"
        class="embedded-video-report-wrap"
      >
        <div class="embedded-report-header">
          <div>
            <h3 class="embedded-report-title">单视频检测报告</h3>
            <div class="embedded-report-subtitle">
              当前视频：<code>{{ selectedReportVideoId }}</code>
            </div>
          </div>
        </div>

        <EmbeddedVideoReport
          :video-id="selectedReportVideoId"
          :task-status="selectedVideoInfo?.status"
          embedded
        />
      </section>
    </main>

    <aside class="report-aside">
      <ReportToc :items="videoReportTocItems" separator="、" />
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import dayjs from 'dayjs'
import { useRoute, useRouter } from 'vue-router'
import { http } from '@/api/http'
import { Message } from '@arco-design/web-vue'
import { toErrorMessage } from '@/api/http'
import ReportSection from '@/components/report/ReportSection.vue'
import EmbeddedVideoReport from '@/components/report/EmbeddedVideoReport.vue'
import ReportToc from '@/components/report/ReportToc.vue'
import { formatChinaDateTime } from '@/utils/time'
import type { ReportTocItem } from '@/components/report/ReportToc.vue'

const route = useRoute()
const router = useRouter()

const todayStr = dayjs().format('YYYY-MM-DD')
const reportDate = ref<string>(todayStr)

// route.query.video_id 来自视频历史记录"查看报表"跳转。
// 一旦存在，必须锁定为 video_id 模式：
//   - selectedReportVideoId 优先用 video_id
//   - 不再 fallback 到"距离当前最近视频"逻辑
//   - loadTasks 必须能在 tasks 列表里查到这个 video_id（即使它不在 report_date 当日）
const routeVideoId = computed(() =>
  String(route.query.video_id || '').trim()
)
const isVideoReportMode = computed(() => !!routeVideoId.value)

const videoReportTocItems: ReportTocItem[] = [
  { id: 'embedded-text-block', index: '一', title: '文字板块' },
  { id: 'embedded-chart-block', index: '二', title: '可视化统计分析' },
  { id: 'embedded-video-block', index: '三', title: '视频对比展示' },
  { id: 'embedded-fish-detail', index: '四', title: '鱼体检测明细' },
  { id: 'embedded-pdf-report', index: '五', title: '查看PDF报表' },
]  

const loading = ref(false)
const tasks = ref<any[]>([])
const selectedVideoId = ref<string>('')
const selectedReportVideoId = ref<string>('')

const selectableVideos = computed(() => {
  return tasks.value
    .filter((v) => String(v.video_id || '').trim())
    .slice()
    .sort((a, b) => {
      const ams = parseTimeMs(getVideoStartTime(a))
      const bms = parseTimeMs(getVideoStartTime(b))
      if (ams > 0 && bms > 0 && ams !== bms) return bms - ams
      if (ams > 0 && bms > 0 && ams === bms) return 0
      const aDone = String(a.status || '').toLowerCase() === 'done' ? 1 : 0
      const bDone = String(b.status || '').toLowerCase() === 'done' ? 1 : 0
      if (aDone !== bDone) return bDone - aDone
      return String(b.created_at || '').localeCompare(String(a.created_at || ''))
    })
})

const selectedVideoInfo = computed(() => {
  if (!selectedReportVideoId.value) return null
  return tasks.value.find(
    (v) => String(v.video_id || '') === selectedReportVideoId.value
  ) || { video_id: selectedReportVideoId.value }
})

function getVideoStartTime(v: any): string {
  return String(
    v.record_start_time ||
    v.recording_start_time ||
    v.started_at ||
    v.start_time ||
    v.video_start_time ||
    v.created_at ||
    v.created_time ||
    ''
  )
}

function getDatePart(value: any): string {
  const s = String(value || '').trim()
  if (!s) return ''
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10)
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return ''
  return dayjs(d).format('YYYY-MM-DD')
}

function isVideoInReportDate(v: any, date: string): boolean {
  return getDatePart(getVideoStartTime(v)) === date
}

function parseTimeMs(value: string): number {
  const t = Date.parse(value)
  return Number.isFinite(t) ? t : 0
}

function formatVideoStartTime(v: any): string {
  const raw = getVideoStartTime(v)
  if (!raw) return '--'
  const d = dayjs(raw)
  if (!d.isValid()) return raw
  return formatChinaDateTime(d.toDate())
}

function formatDuration(seconds: number | null | undefined): string {
  const n = Number(seconds)
  if (!Number.isFinite(n) || n < 0) return '--'
  const total = Math.round(n)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return h ? `${h}小时${m}分${s}秒` : m ? `${m}分${s}秒` : `${s}秒`
}

function videoOptionLabel(v: any): string {
  const start = formatVideoStartTime(v)
  const status = v?.status || '--'
  return `${v?.video_id || '--'} ｜ ${start} ｜ ${status}`
}

function findNearestVideoToNow(items: any[]): any | null {
  const now = Date.now()
  const valid = items
    .filter((v) => String(v.video_id || '').trim())
    .map((v) => {
      const ms = parseTimeMs(getVideoStartTime(v))
      return {
        video: v,
        ms,
        diff: ms > 0 ? Math.abs(ms - now) : Number.MAX_SAFE_INTEGER,
      }
    })
    .sort((a, b) => {
      if (a.diff !== b.diff) return a.diff - b.diff
      return b.ms - a.ms
    })
  return valid[0]?.video || null
}

function applyInitialVideoSelection() {
  // 已经统一交给 syncSelectedVideoAfterDateChange 处理
  syncSelectedVideoAfterDateChange()
}

// 请求序号：每次 loadTasks 调用自增；await 后如果当前请求已不是最新，
// 旧结果必须丢弃，避免在切换 video_id 时旧请求覆盖新视频。
let loadTasksSeq = 0

async function loadTasks() {
  const seq = ++loadTasksSeq
  // 切换 video_id 时立刻清空旧数据，避免旧视频残留
  tasks.value = []
  selectedVideoId.value = ''
  selectedReportVideoId.value = ''
  loading.value = true
  try {
    const date = String(reportDate.value || '')
    let items: any[] = []
    // 优先按日期过滤：/api/reports/fish-quality/videos?report_date=YYYY-MM-DD
    try {
      const daily = await http.get(
        '/api/reports/fish-quality/videos',
        { params: { report_date: date } }
      )
      // 已被新请求取代则丢弃
      if (seq !== loadTasksSeq) return
      const data = daily.data?.data || daily.data || {}
      if (Array.isArray(data.items)) items = data.items
      else if (Array.isArray(data.videos)) items = data.videos
      else if (Array.isArray(data)) items = data
    } catch (_) {
      const fallback = await http.get('/api/videos', {
        params: { page: 1, page_size: 100 },
      })
      if (seq !== loadTasksSeq) return
      const all: any[] = fallback.data?.data?.items || fallback.data?.items || []
      items = all
    }
    // 即使后端已经按日期过滤，前端再保险过滤一次，避免混入其它日期。
    items = items.filter((v) => isVideoInReportDate(v, date))

    // video_id 模式：目标 video 不一定在 report_date 当日（跨天录制 / 手动选历史 video），
    // 因此如果当日的 tasks 列表里没找到目标 video_id，单独再按 keyword 查一次兜底。
    if (isVideoReportMode.value && routeVideoId.value) {
      const target = routeVideoId.value
      const inDay = items.some((v) => String(v.video_id || '') === target)
      if (!inDay) {
        try {
          const r = await http.get('/api/videos', {
            params: { keyword: target, page: 1, page_size: 1 },
          })
          if (seq !== loadTasksSeq) return
          const arr: any[] =
            r.data?.data?.items || r.data?.items || []
          const found = arr.find(
            (v) => String(v.video_id || '') === target
          )
          if (found) items = [found, ...items]
        } catch (_) {
          /* tolerate */
        }
      }
    }

    // 再次检查 seq，防止请求途中发生新 loadTasks
    if (seq !== loadTasksSeq) return

    tasks.value = items
    syncSelectedVideoAfterDateChange()
  } catch (err) {
    if (seq !== loadTasksSeq) return
    tasks.value = []
    selectedVideoId.value = ''
    selectedReportVideoId.value = ''
    Message.error(toErrorMessage(err))
  } finally {
    if (seq === loadTasksSeq) {
      loading.value = false
    }
  }
}

function syncSelectedVideoAfterDateChange() {
  const current = String(selectedReportVideoId.value || '')

  // video_id 模式（从视频历史"查看报表"跳转）：
  // 必须锁定到 route.query.video_id，不允许 fallback 到"最近视频"。
  if (isVideoReportMode.value) {
    const target = routeVideoId.value
    selectedVideoId.value = target
    selectedReportVideoId.value = target
    return
  }

  const exists = tasks.value.some(
    (v) => String(v.video_id || '') === current,
  )
  if (current && exists) {
    selectedVideoId.value = current
    // selectedVideoInfo 是 computed，会随 tasks/selectedReportVideoId 自动重算
    return
  }
  const nearest = findNearestVideoToNow(tasks.value)
  if (nearest) {
    const vid = String(nearest.video_id)
    selectedVideoId.value = vid
    selectedReportVideoId.value = vid
    router.replace({
      path: route.path,
      query: {
        ...route.query,
        type: 'daily-videos',
        video_id: vid,
        date: reportDate.value,
      },
      hash: '',
    })
  } else {
    selectedVideoId.value = ''
    selectedReportVideoId.value = ''
  }
}

function handleDateChange() {
  loadTasks()
}

function handleRefresh() {
  loadTasks()
}

function handleSelectedVideoChange(value: string) {
  const vid = String(value || '').trim()
  if (!vid) return
  selectedVideoId.value = vid
  selectedReportVideoId.value = vid
  router.replace({
    path: route.path,
    query: {
      ...route.query,
      type: 'daily-videos',
      video_id: vid,
      date: reportDate.value,
    },
    hash: '',
  })
}

function showSelectedVideoReport() {
  const vid = String(selectedVideoId.value || '').trim()
  if (!vid) {
    Message.warning('请先选择视频')
    return
  }
  const task = tasks.value.find((v) => String(v.video_id) === vid)
  if (String(task?.status || '').toLowerCase() !== 'done') {
    Message.warning('该任务仍在运行中，请稍后查看视频报告')
    return
  }
  selectedReportVideoId.value = vid
  router.replace({
    path: route.path,
    query: {
      ...route.query,
      type: 'daily-videos',
      video_id: vid,
      date: reportDate.value,
    },
    hash: '',
  })
  nextTick(() => {
    document.getElementById('embedded-video-report')?.scrollIntoView({
      behavior: 'smooth',
      block: 'start',
    })
  })
}

onMounted(async () => {
  await loadTasks()
  if (route.hash) {
    const id = route.hash.replace('#', '')
    setTimeout(() => {
      const el = document.getElementById(id)
      el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 80)
  }
})

watch(
  () => route.query.video_id,
  (v) => {
    const id = String(v || '').trim()
    if (id) {
      // 切换到不同 video_id：必须清空旧数据再加载新数据，
      // 避免 EmbeddedVideoReport 短暂展示上一视频的统计与鱼列表。
      // loadTasks 内部会重新自增请求序号并清理 tasks。
      loadTasks()
    }
  }
)
</script>

<style lang="scss" scoped>
.report-page-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 220px;
  gap: 16px;
  align-items: start;
  width: 100%;
  box-sizing: border-box;
  padding: 16px;
  background: #f5f7fb;
}
.report-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.report-aside {
  position: sticky;
  top: 76px;
  align-self: start;
  width: 220px;
  min-width: 220px;
  max-height: calc(100vh - 92px);
  overflow-y: auto;
}
.report-header-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  box-sizing: border-box;
  padding: 16px 20px;
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgb(0 0 0 / 6%);
}
.report-page-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  line-height: 1.5;
  color: #1d2129;
}
.report-page-subtitle {
  margin-top: 4px;
  font-size: 13px;
  line-height: 1.5;
  color: #86909c;
}
.video-select-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
}
.selected-video-info {
  margin-top: 12px;
  font-size: 13px;
  color: #4e5969;
  word-break: break-all;
}
.embedded-video-report-wrap {
  margin-top: 4px;
}
.embedded-report-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  box-sizing: border-box;
  padding: 12px 16px;
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgb(0 0 0 / 6%);
}
.embedded-report-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #1d2129;
}
.embedded-report-subtitle {
  margin-top: 4px;
  font-size: 13px;
  color: #86909c;
}

@media (width <= 1200px) {
  .report-page-layout {
    grid-template-columns: 1fr;
  }
  .report-aside {
    display: none;
  }
}
</style>

<style>
.video-select-dropdown-4 .arco-select-dropdown-list,
.video-select-dropdown-4 .arco-select-dropdown-list-wrapper,
.video-select-dropdown-4 .arco-virtual-list,
.video-select-dropdown-4 .arco-virtual-list-container {
  max-height: 160px !important;
  overflow-y: auto !important;
}
</style>
