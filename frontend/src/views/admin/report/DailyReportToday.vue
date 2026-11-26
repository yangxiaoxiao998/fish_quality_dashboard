<template>
  <div class="report-page-layout">
    <main class="report-main">
      <div class="report-header-card">
        <div>
          <h2 class="report-page-title">当日鱼类检测数据报告</h2>
          <div class="report-page-subtitle">日期：{{ reportDate }} 当日数据汇总</div>
        </div>
        <div class="report-header-actions">
          <a-space>
            <a-date-picker
              v-model="reportDate"
              value-format="YYYY-MM-DD"
              style="width: 160px"
              @change="handleDateChange"
            />
            <a-button type="primary" :loading="loading" @click="handleRefresh">刷新</a-button>
          </a-space>
        </div>
      </div>

      <!-- 文字板块 -->
      <ReportSection
        id="text-block"
        title="一、文字板块"
        subtitle="以文字和指标卡片形式汇总当日检测结果。"
      >
        <div class="block-subtitle">当日检测概览</div>
        <ReportMetricsGrid :rows="overviewRows" />

        <div class="block-subtitle">当日核心指标</div>
        <ReportMetricsGrid :rows="metricsRows" />

        <!-- <div class="block-subtitle">当日异常与破损分析</div>
        <a-empty
          v-if="!hasAbnormalData"
          description="暂无异常或破损统计数据"
        />
        <ReportMetricsGrid v-else :rows="abnormalRows" /> -->
      </ReportSection>

      <!-- 图表板块 -->
      <ReportSection
        id="chart-block"
        title="二、可视化统计分析"
        subtitle="以统计图表形式展示鱼数、体征与破损率分布。"
      >
        <a-empty
          v-if="!hasChartData"
          description="暂无可用于绘图的数据"
        />
        <div v-else class="charts-grid">
           <div class="chart-card">
             <div class="chart-title">鱼体状态分布</div>
             <div ref="statusPieRef" class="chart-canvas chart-canvas-tall"></div>
           </div>
           <div class="chart-card">
             <div class="chart-title">体长分布 (cm)</div>
             <div ref="lengthDistRef" class="chart-canvas"></div>
           </div>
           <div class="chart-card">
             <div class="chart-title">无尾体长分布 (cm)</div>
             <div ref="noTailDistRef" class="chart-canvas"></div>
           </div>
           <div class="chart-card">
             <div class="chart-title">体高分布 (cm)</div>
             <div ref="heightDistRef" class="chart-canvas"></div>
           </div>
           <div class="chart-card">
             <div class="chart-title">体重分布 (g)</div>
             <div ref="weightDistRef" class="chart-canvas"></div>
           </div>
           <div class="chart-card">
             <div class="chart-title">肥满度分布</div>
             <div ref="conditionDistRef" class="chart-canvas"></div>
           </div>
           <div v-if="hasDamageData" class="chart-card">
             <div class="chart-title">破损率分布 (×100%)</div>
             <div ref="damageDistRef" class="chart-canvas"></div>
           </div>
        </div>
      </ReportSection>

      <!-- Excel 板块（视频任务表格） -->
      <ReportSection
        id="excel-block"
        title="三、视频数据表格"
        subtitle="以表格形式展示当日所有视频任务数据。"
      >
        <a-empty v-if="!videoTasks.length" description="当日暂无视频任务" />
         <div v-else class="table-scroll">
           <a-table
             :data="pagedVideoTasks"
             :pagination="videoPagination"
            row-key="video_id"
            stripe
            :scroll="{ x: 1100 }"
          >
            <template #columns>
              <a-table-column title="视频编号" data-index="video_id" :width="220" />
              <a-table-column title="检测时间" :width="170">
                <template #cell="{ record }">{{ formatChinaDateTime(record.created_at) }}</template>
              </a-table-column>
              <a-table-column title="状态" data-index="status" :width="110" />
               <a-table-column title="总鱼数" :width="90"><template #cell="{ record }">{{ displayValue(record.total_fish) }}</template></a-table-column>
               <a-table-column title="完整" :width="80"><template #cell="{ record }">{{ displayValue(record.complete_fish) }}</template></a-table-column>
               <a-table-column title="不完整" :width="90"><template #cell="{ record }">{{ displayValue(record.incomplete_fish) }}</template></a-table-column>
               <a-table-column title="失败" :width="80"><template #cell="{ record }">{{ displayValue(record.failed_fish) }}</template></a-table-column>
               <a-table-column title="破损" :width="80"><template #cell="{ record }">{{ displayValue(record.damaged_fish) }}</template></a-table-column>
              <a-table-column title="视频时长" :width="120">
                <template #cell="{ record }">{{ formatDuration(record.video_duration_seconds) }}</template>
              </a-table-column>
              <a-table-column title="处理耗时" :width="120">
                <template #cell="{ record }">{{ formatDuration(getProcessingDuration(record)) }}</template>
              </a-table-column>
            </template>
          </a-table>
        </div>
      </ReportSection>

      <!-- 视频板块 -->
      <ReportSection
        id="video-block"
        title="四、视频对比展示"
        subtitle="提供当日 done 视频的单视频报表与结果大屏入口。"
      >
        <a-empty
          v-if="!entryLinks.length"
          description="当日暂无可展示视频任务"
        />
        <div v-else class="entry-grid">
          <a-card
            v-for="v in entryLinks"
            :key="v.video_id"
            :bordered="false"
            class="entry-card"
          >
            <template #title>
              <span class="entry-title">{{ v.video_id }}</span>
            </template>
            <div class="entry-meta">
              <a-tag :color="statusColor(v.status)">{{ v.status }}</a-tag>
              <span>总鱼 {{ v.total_fish ?? '--' }}</span>
              <span>完整 {{ v.complete_fish ?? '--' }}</span>
              <span>破损 {{ v.damaged_fish ?? '--' }}</span>
            </div>
            <div class="entry-actions">
              <a-button
                type="primary"
                size="small"
                @click="goReport(v.video_id)"
              >
                查看报表
              </a-button>
              <a-button
                type="outline"
                size="small"
                @click="goDashboard(v.video_id)"
              >
                跳转大屏
              </a-button>
            </div>
          </a-card>
        </div>
      </ReportSection>

      <ReportSection
        id="daily-pdf-report"
        title="五、查看PDF报表"
        subtitle="生成并预览当天鱼品质检测日报 PDF / Word。"
      >
        <div class="daily-pdf-actions">
          <a-button
            type="primary"
            :loading="dailyPdfGenerating"
            @click="() => generateDailyPdfReport()"
          >
            重新生成{{ reportDate }} 日报
          </a-button>
          <a-button
            v-if="dailyReportInfo?.report_pdf_url"
            :href="withVersion(dailyReportInfo.report_pdf_url)"
            download
            @click="logUserAction({ module: '数据报表', action: 'download_daily_pdf', targetType: 'report_date', targetId: reportDate, message: '下载日报 PDF' })"
          >
            下载 PDF
          </a-button>
          <a-button
            v-if="dailyReportInfo?.report_docx_url"
            :href="withVersion(dailyReportInfo.report_docx_url)"
            download
            @click="logUserAction({ module: '数据报表', action: 'download_daily_word', targetType: 'report_date', targetId: reportDate, message: '下载日报 Word' })"
          >
            下载 Word
          </a-button>
        </div>

        <div v-if="dailyReportInfo?.report_pdf_url" class="daily-pdf-preview-wrap">
          <iframe
            class="daily-pdf-preview"
            :src="withVersion(dailyReportInfo.report_pdf_url)"
            title="日报 PDF 预览"
          />
        </div>
        <a-empty
          v-else
           description="日报正在自动生成，生成后将在此处显示。"
        />

      </ReportSection>
    </main>

    <aside class="report-aside">
      <ReportToc :items="tocItems" separator="、" />
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import dayjs from 'dayjs'
import { useRoute, useRouter } from 'vue-router'
import { http, toErrorMessage } from '@/api/http'
import { Message } from '@arco-design/web-vue'
import { useReportData } from '@/composables/useReportData'
import { useReportCharts } from '@/composables/useReportCharts'
import ReportToc from '@/components/report/ReportToc.vue'
import ReportSection from '@/components/report/ReportSection.vue'
import ReportMetricsGrid from '@/components/report/ReportMetricsGrid.vue'
import { formatChinaDateTime } from '@/utils/time'
import { logUserAction } from '@/utils/operationLogger'
import type { ReportTocItem } from '@/components/report/ReportToc.vue'

const route = useRoute()
const router = useRouter()

const todayStr = dayjs().format('YYYY-MM-DD')
const initialRouteDate = String(route.query.date || '').slice(0, 10)
const reportDate = ref<string>(/^\d{4}-\d{2}-\d{2}$/.test(initialRouteDate) ? initialRouteDate : todayStr)

const tocItems: ReportTocItem[] = [
  { id: 'text-block', index: '一', title: '文字板块' },
  { id: 'chart-block', index: '二', title: '可视化统计分析' },
  { id: 'excel-block', index: '三', title: '视频数据表格' },
  { id: 'video-block', index: '四', title: '视频对比展示' },
  { id: 'daily-pdf-report', index: '五', title: '查看PDF报表' },
]


const reportSubtitle = computed(() => `日期：${reportDate.value}  当日数据汇总`)

const { loading, summary, charts, loadAll } = useReportData(
  () => ({ report_date: reportDate.value })
)

const videoTasks = ref<any[]>([])
const videoPage = ref(1)
const videoPageSize = ref(10)
const pagedVideoTasks = computed(() => {
  const start = (videoPage.value - 1) * videoPageSize.value
  return videoTasks.value.slice(start, start + videoPageSize.value)
})
const videoPagination = computed(() => ({
  current: videoPage.value,
  pageSize: videoPageSize.value,
  total: videoTasks.value.length,
  showTotal: true,
  showPageSize: true,
  pageSizeOptions: [10, 20, 50],
  onChange: (page: number) => { videoPage.value = page },
  onPageSizeChange: (size: number) => { videoPageSize.value = size; videoPage.value = 1 },
}))

function displayValue(value: unknown): string | number {
  return value === null || value === undefined || value === '' ? '--' : (value as string | number)
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '--'
  const n = Number(seconds)
  if (!Number.isFinite(n) || n < 0) return '--'
  const total = Math.round(n)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return h ? `${h}小时${m}分${s}秒` : m ? `${m}分${s}秒` : `${s}秒`
}

function getProcessingDuration(record: any): number | null | undefined {
  return record?.processing_duration_seconds ?? record?.duration_seconds
}

async function loadVideoTasks() {
  try {
    const resp = await http.get('/api/videos', {
      params: { page: 1, page_size: 100 },
    })
    const items: any[] = resp.data?.items || []
    videoTasks.value = items.filter((v) => {
      const ca = String(v?.created_at || '')
      return ca.startsWith(reportDate.value) ||
        (ca.startsWith(reportDate.value.slice(0, 10)) && reportDate.value.length === 10)
    })
    videoPage.value = 1
  } catch (err) {
    Message.error(toErrorMessage(err))
  }
}


const overviewRows = computed(() => [
  { key: 'videos', title: '视频任务数', value: summary.value.total_video, unit: '个' },
  { key: 'videoDuration', title: '总视频时长', value: formatDurationPrecise(summary.value.total_video_duration), unit: '' },
  { key: 'fishes', title: '检测总鱼数', value: summary.value.total_fish, unit: '条' },
  { key: 'valid', title: '完整鱼数量', value: summary.value.valid_fish, unit: '条' },
  { key: 'invalid', title: '不完整鱼数量', value: summary.value.incomplete_fish, unit: '条' },
  { key: 'failed', title: '失败鱼数量', value: 0, unit: '条' },
  { key: 'damaged', title: '破损鱼数量', value: summary.value.surface_damage_count, unit: '条' },
])

function formatDurationPrecise(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '--'
  const n = Number(seconds)
  if (!Number.isFinite(n) || n < 0) return '--'
  if (n < 60) return `${n.toFixed(2)}秒`
  const total = Math.round(n)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return h ? `${h}小时${m}分${s}秒` : `${m}分${s}秒`
}

const metricsRows = computed(() => [
   { key: 'len', title: '平均体长', value: summary.value.avg_body_length, unit: 'cm' },
   { key: 'noTail', title: '平均无尾体长', value: summary.value.avg_body_length_without_tail, unit: 'cm' },
  { key: 'height', title: '平均体高', value: summary.value.avg_body_height, unit: 'cm' },
  { key: 'weight', title: '平均体重', value: summary.value.avg_body_weight, unit: 'g' },
   { key: 'cf', title: '平均肥满度', value: summary.value.avg_condition_factor },
])

function ratePct(v: any): string {
  if (v === null || v === undefined) return '--'
  const n = Number(v)
  if (!Number.isFinite(n)) return '--'
  return `${(n * 100).toFixed(2)}%`
}

const abnormalRows = computed(() => {
  const s = summary.value
  return [
    { key: 'invalidCount', title: '异常鱼数量', value: s.abnormal_fish, unit: '条' },
    { key: 'invalidRatio', title: '异常鱼比例', value: ratePct(s.abnormal_fish_rate) },
    // { key: 'damagedCount', title: '破损鱼数量', value: s.surface_damage_count, unit: '条' },
    // { key: 'damagedRatio', title: '破损鱼比例', value: ratePct(s.surface_damage_rate) },
    // { key: 'completeRate', title: '指标完整率', value: ratePct(s.metric_complete_rate) },
  ]
})

const hasChartData = computed(
  () => (summary.value.total_fish || 0) > 0
)
const hasAbnormalData = computed(
  () => (summary.value.abnormal_fish || 0) + (summary.value.surface_damage_count || 0) > 0
)
const hasDamageData = computed(
  () => (summary.value.surface_damage_count || 0) > 0
)

const statusPieRef = ref<HTMLElement>()
const lengthDistRef = ref<HTMLElement>()
const noTailDistRef = ref<HTMLElement>()
const heightDistRef = ref<HTMLElement>()
const weightDistRef = ref<HTMLElement>()
const conditionDistRef = ref<HTMLElement>()
const damageDistRef = ref<HTMLElement>()

useReportCharts(summary, charts, {
  statusPie: statusPieRef,
  lengthDist: lengthDistRef,
  noTailDist: noTailDistRef,
  heightDist: heightDistRef,
  weightDist: weightDistRef,
  conditionDist: conditionDistRef,
  damageDist: damageDistRef,
})

const entryLinks = computed(() => {
  return videoTasks.value
    .filter((v) => (v.status || '').toLowerCase() === 'done')
    .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))
    .slice(0, 6)
})

function statusColor(s: string): string {
  switch ((s || '').toLowerCase()) {
    case 'done':
      return 'green'
    case 'failed':
      return 'red'
    case 'no_fish_detected':
      return 'orange'
    case 'recording':
    case 'processing':
      return 'blue'
    default:
      return 'gray'
  }
}

function goReport(videoId: string) {
  const task = videoTasks.value.find((v) => String(v.video_id) === videoId)
  if (String(task?.status || '').toLowerCase() !== 'done') {
    Message.warning('该任务仍在运行中，请稍后查看报表')
    return
  }
  router.push({
    path: '/admin/data-report',
    query: {
      type: 'daily-videos',
      video_id: videoId,
      date: reportDate.value,
    },
  })
}

function goDashboard(videoId: string) {
  const task = videoTasks.value.find((v) => String(v.video_id) === videoId)
  if (String(task?.status || '').toLowerCase() !== 'done') {
    Message.warning('该任务仍在运行中，请稍后查看结果大屏')
    return
  }
  router.push({
    path: '/dashboard',
    query: { mode: 'result', video_id: videoId },
  })
}

function handleDateChange() {
  videoPage.value = 1
  loadAll()
  loadVideoTasks()
  checkExistingDailyPdf()
}

 function handleRefresh() {
  loadAll()
  loadVideoTasks()
  checkExistingDailyPdf()
}

// -------------------- 当日日报 --------------------
const dailyPdfGenerating = ref<boolean>(false)
const dailyReportInfo = ref<{
  report_id?: number | string
  report_pdf_url?: string
  report_docx_url?: string
  exists_via?: 'history' | 'generated'
} | null>(null)
const dailyReportMediaVersion = ref<number>(Date.now())
let autoRefreshTimer: ReturnType<typeof window.setInterval> | null = null

function withVersion(url: string): string {
  if (!url) return ''
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}_v=${dailyReportMediaVersion.value}`
}

async function checkExistingDailyPdf() {
  if (!reportDate.value) {
    dailyReportInfo.value = null
    return
  }
  try {
    const resp = await http.get('/api/reports/history', {
      params: {
        start_date: reportDate.value,
        end_date: reportDate.value,
        report_type: 'daily_fish_quality',
        status: 'generated',
        page: 1,
        page_size: 1,
      },
    })
    const items = Array.isArray(resp.data?.items) ? resp.data.items : []
    const todayReport = items[0]
    if (todayReport) {
      const base: any = {
        report_id: todayReport.report_id,
        exists_via: 'history' as const,
      }
      const fp = todayReport.file_path
      if (fp) {
        const rel = String(fp).replace(/^data\//, '')
        base.report_pdf_url = '/static/' + rel
      }
      dailyReportInfo.value = base
    } else {
      dailyReportInfo.value = null
      if (summary.value.total_fish > 0 && !dailyPdfGenerating.value) {
        await generateDailyPdfReport(true)
      }
    }
  } catch (err) {
    console.warn('[daily-pdf] history query failed:', toErrorMessage(err))
  }
}

async function generateDailyPdfReport(silent = false) {
  if (!reportDate.value) {
    Message.error('日期无效')
    return
  }
  dailyPdfGenerating.value = true
  if (!silent) logUserAction({
    module: '数据报表',
    action: 'generate_daily_report',
    targetType: 'report_date',
    targetId: reportDate.value,
    message: '点击生成日报',
  })
  try {
    const resp = await http.post(
      '/api/reports/fish-quality/daily-pdf/generate',
      { report_date: reportDate.value },
      { timeout: 180000 },
    )
    const data = resp.data || {}
    const pdfRel = data.file_path
     const docxRel = data.docx_path || data.file_docx_path
    dailyReportInfo.value = {
      report_id: data.report_id,
      exists_via: 'generated',
      report_pdf_url: pdfRel
        ? '/static/' + String(pdfRel).replace(/^data\//, '')
        : '',
      report_docx_url: docxRel
        ? '/static/' + String(docxRel).replace(/^data\//, '')
        : '',
    }
    dailyReportMediaVersion.value = Date.now()
    if (!silent) Message.success('日报已生成')
  } catch (err: any) {
    const msg = toErrorMessage(err)
    if (msg.includes('无检测数据')) {
      if (!silent) Message.warning('当前日期暂无检测数据，无法生成日报')
    } else {
      if (!silent) Message.error('生成日报失败：' + msg)
    }
  } finally {
    dailyPdfGenerating.value = false
  }
}

onMounted(async () => {
  await loadAll()
  await loadVideoTasks()
  await checkExistingDailyPdf()
  autoRefreshTimer = window.setInterval(async () => {
    await Promise.all([loadAll(), loadVideoTasks(), checkExistingDailyPdf()])
    dailyReportMediaVersion.value = Date.now()
  }, 10000)
  if (route.hash) {
    const id = route.hash.replace('#', '')
    setTimeout(() => {
      const el = document.getElementById(id)
      el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      router.replace({ query: route.query, hash: `#${id}` })
    }, 80)
  }
})

onBeforeUnmount(() => {
  if (autoRefreshTimer !== null) {
    window.clearInterval(autoRefreshTimer)
    autoRefreshTimer = null
  }
})

watch(
  () => reportDate.value,
  () => {
    loadAll()
    loadVideoTasks()
    checkExistingDailyPdf()
  }
)
</script>

<style scoped>
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
  width: 100%;
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
  z-index: 5;
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
.report-header-actions {
  flex-shrink: 0;
}
.charts-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  min-width: 0;
}
.chart-card {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  padding: 8px 10px 10px;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.charts-grid > .chart-card:first-child {
  grid-column: 1 / -1;
}
.chart-title {
  width: 100%;
  margin-bottom: 4px;
  text-align: center;
  font-size: 14px;
  font-weight: 600;
  color: #1d2129;
}
.chart-canvas {
  width: min(100%, 520px);
  height: 240px;
  min-width: 0;
  max-width: 100%;
  margin: 0 auto;
  overflow: hidden;
}
.chart-canvas-tall {
  height: 280px;
}

@media (width <= 760px) {
  .charts-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .charts-grid > .chart-card:first-child {
    grid-column: auto;
  }
}
.table-scroll {
  width: 100%;
  overflow-x: auto;
}
.entry-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
}
.entry-card {
  box-sizing: border-box;
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
}
.entry-title {
  font-size: 13px;
  font-weight: 600;
  color: #1d2129;
  font-family: SarasaMonoSC, monospace;
}
.entry-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  margin-bottom: 10px;
  font-size: 13px;
  color: #4e5969;
}
.entry-actions {
  display: flex;
  gap: 8px;
}
.block-subtitle {
  margin: 16px 0 10px;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
  color: #1d2129;
}
.block-subtitle:first-child {
  margin-top: 0;
}
.daily-pdf-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}
.daily-pdf-preview-wrap {
  width: 100%;
  height: 760px;
  background: #f7f8fa;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  overflow: hidden;
}
.daily-pdf-preview {
  width: 100%;
  height: 100%;
  border: 0;
  background: #fff;
}
.daily-pdf-history-hint {
  margin-top: 12px;
  padding: 10px 12px;
  background: #f0f7ff;
  border-radius: 6px;
  color: #4e5969;
  font-size: 12px;
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
