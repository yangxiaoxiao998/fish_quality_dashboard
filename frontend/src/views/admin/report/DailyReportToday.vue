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
        title="文字板块"
        subtitle="以文字和指标卡片形式汇总当日检测结果。"
      >
        <div class="block-subtitle">当日检测概览</div>
        <ReportMetricsGrid :rows="overviewRows" />

        <div class="block-subtitle">当日核心指标</div>
        <ReportMetricsGrid :rows="metricsRows" />

        <div class="block-subtitle">当日异常与破损分析</div>
        <a-empty
          v-if="!hasAbnormalData"
          description="暂无异常或破损统计数据"
        />
        <ReportMetricsGrid v-else :rows="abnormalRows" />
      </ReportSection>

      <!-- 图表板块 -->
      <ReportSection
        id="chart-block"
        title="图表板块"
        subtitle="以统计图表形式展示鱼数、体征与破损率分布。"
      >
        <a-empty
          v-if="!hasChartData"
          description="暂无可用于绘图的数据"
        />
        <div v-else class="charts-grid">
          <a-card :bordered="false" class="chart-card">
            <template #title><span class="chart-title">鱼体状态分布</span></template>
            <div ref="statusPieRef" class="chart-canvas chart-canvas-tall"></div>
          </a-card>
          <a-card :bordered="false" class="chart-card">
            <template #title>
              <span class="chart-title">鱼数量趋势（{{ charts.fish_count_trend[0]?.bucket_kind === 'day' ? '按天' : '按小时' }}）</span>
            </template>
            <div ref="trendRef" class="chart-canvas chart-canvas-tall"></div>
          </a-card>
          <a-card :bordered="false" class="chart-card">
            <template #title><span class="chart-title">体长分布 (cm)</span></template>
            <div ref="lengthDistRef" class="chart-canvas"></div>
          </a-card>
          <a-card :bordered="false" class="chart-card">
            <template #title><span class="chart-title">无尾体长分布 (cm)</span></template>
            <div ref="noTailDistRef" class="chart-canvas"></div>
          </a-card>
          <a-card :bordered="false" class="chart-card">
            <template #title><span class="chart-title">体高分布 (cm)</span></template>
            <div ref="heightDistRef" class="chart-canvas"></div>
          </a-card>
          <a-card :bordered="false" class="chart-card">
            <template #title><span class="chart-title">体重分布 (g)</span></template>
            <div ref="weightDistRef" class="chart-canvas"></div>
          </a-card>
          <a-card :bordered="false" class="chart-card">
            <template #title><span class="chart-title">肥满度分布</span></template>
            <div ref="conditionDistRef" class="chart-canvas"></div>
          </a-card>
          <a-card v-if="hasDamageData" :bordered="false" class="chart-card">
            <template #title><span class="chart-title">破损率分布 (×100%)</span></template>
            <div ref="damageDistRef" class="chart-canvas"></div>
          </a-card>
        </div>
      </ReportSection>

      <!-- Excel 板块（视频任务表格） -->
      <ReportSection
        id="excel-block"
        title="Excel 板块"
        subtitle="以表格形式展示当日所有视频任务数据。"
      >
        <a-empty v-if="!videoTasks.length" description="当日暂无视频任务" />
        <div v-else class="table-scroll">
          <a-table
            :data="videoTasks"
            :pagination="false"
            row-key="video_id"
            stripe
            :scroll="{ x: 1100 }"
          >
            <a-table-column title="视频编号" data-index="video_id" :width="220" />
            <a-table-column title="检测时间" :width="170">
              <template #cell="{ record }">{{ formatChinaDateTime(record.created_at) }}</template>
            </a-table-column>
            <a-table-column title="状态" data-index="status" :width="110" />
            <a-table-column title="总鱼数" data-index="total_fish" :width="90" />
            <a-table-column title="完整" data-index="complete_fish" :width="80" />
            <a-table-column title="不完整" data-index="incomplete_fish" :width="90" />
            <a-table-column title="失败" data-index="failed_fish" :width="80" />
            <a-table-column title="破损" data-index="damaged_fish" :width="80" />
          </a-table>
        </div>
      </ReportSection>

      <!-- 视频板块 -->
      <ReportSection
        id="video-block"
        title="视频板块"
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
        title="查看PDF报表"
        subtitle="生成并预览当天鱼品质检测日报 PDF / Word。"
      >
        <div class="daily-pdf-actions">
          <a-button
            type="primary"
            :loading="dailyPdfGenerating"
            @click="generateDailyPdfReport"
          >
            生成{{ reportDate }} 日报
          </a-button>
          <a-button
            v-if="dailyReportInfo?.report_pdf_url"
            :href="withVersion(dailyReportInfo.report_pdf_url)"
            target="_blank"
            @click="logUserAction({ module: '数据报表', action: 'open_daily_pdf', targetType: 'report_date', targetId: reportDate, message: '打开日报 PDF' })"
          >
            新窗口打开 PDF
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
          description="日报尚未生成。请等待数据加载完成后点击上方按钮生成。"
        />

        <div v-if="dailyReportInfo?.exists_via === 'history'" class="daily-pdf-history-hint">
          检测到历史报告 <code>{{ dailyReportInfo.report_id }}</code>，已自动复用。
        </div>
      </ReportSection>
    </main>

    <aside class="report-aside">
      <ReportToc :items="tocItems" />
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
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
const reportDate = ref<string>(todayStr)

const tocItems: ReportTocItem[] = [
  { id: 'text-block', index: '1', title: '文字板块' },
  { id: 'chart-block', index: '2', title: '图表板块' },
  { id: 'excel-block', index: '3', title: 'Excel板块' },
  { id: 'video-block', index: '4', title: '视频板块' },
  { id: 'daily-pdf-report', index: '5', title: '查看PDF报表' },
]


const reportSubtitle = computed(() => `日期：${reportDate.value}  当日数据汇总`)

const { loading, summary, charts, loadAll } = useReportData(
  () => ({ report_date: reportDate.value })
)

const videoTasks = ref<any[]>([])

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
  } catch (err) {
    Message.error(toErrorMessage(err))
  }
}


const overviewRows = computed(() => [
  { key: 'videos', title: '视频任务数', value: summary.value.total_video, unit: '个' },
  { key: 'fishes', title: '检测总鱼数', value: summary.value.total_fish, unit: '条' },
  { key: 'valid', title: '完整鱼数量', value: summary.value.valid_fish, unit: '条' },
  { key: 'invalid', title: '不完整鱼数量', value: summary.value.abnormal_fish, unit: '条' },
  { key: 'failed', title: '失败鱼数量', value: 0, unit: '条' },
  { key: 'damaged', title: '破损鱼数量', value: summary.value.surface_damage_count, unit: '条' },
])

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
    { key: 'damagedCount', title: '破损鱼数量', value: s.surface_damage_count, unit: '条' },
    { key: 'damagedRatio', title: '破损鱼比例', value: ratePct(s.surface_damage_rate) },
    { key: 'completeRate', title: '指标完整率', value: ratePct(s.metric_complete_rate) },
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
const trendRef = ref<HTMLElement>()
const lengthDistRef = ref<HTMLElement>()
const noTailDistRef = ref<HTMLElement>()
const heightDistRef = ref<HTMLElement>()
const weightDistRef = ref<HTMLElement>()
const conditionDistRef = ref<HTMLElement>()
const damageDistRef = ref<HTMLElement>()

useReportCharts(summary, charts, {
  statusPie: statusPieRef,
  trend: trendRef,
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
  router.push({
    path: `/admin/data-report/video/${encodeURIComponent(videoId)}`,
    query: { video_id: videoId },
  })
}

function goDashboard(videoId: string) {
  router.push({
    path: '/',
    query: { mode: 'result', video_id: videoId },
  })
}

function handleDateChange() {
  loadAll()
  loadVideoTasks()
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

function withVersion(url: string): string {
  if (!url) return ''
  return url
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
        page: 1,
        page_size: 5,
      },
    })
    const items = Array.isArray(resp.data?.items) ? resp.data.items : []
    const todayReport = items.find((it: any) => {
      return (
        String(it.report_date || '').slice(0, 10) === reportDate.value &&
        String(it.status || '').toLowerCase() === 'generated'
      )
    })
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
    }
  } catch (err) {
    console.warn('[daily-pdf] history query failed:', toErrorMessage(err))
  }
}

async function generateDailyPdfReport() {
  if (!reportDate.value) {
    Message.error('日期无效')
    return
  }
  dailyPdfGenerating.value = true
  logUserAction({
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
    )
    const data = resp.data || {}
    const pdfRel = data.file_path
    const docxRel = data.file_docx_path
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
    Message.success('日报已生成')
  } catch (err: any) {
    const msg = toErrorMessage(err)
    if (msg.includes('无检测数据')) {
      Message.warning('当前日期暂无检测数据，无法生成日报')
    } else {
      Message.error('生成日报失败：' + msg)
    }
  } finally {
    dailyPdfGenerating.value = false
  }
}

onMounted(async () => {
  await loadAll()
  await loadVideoTasks()
  await checkExistingDailyPdf()
  if (route.hash) {
    const id = route.hash.replace('#', '')
    setTimeout(() => {
      const el = document.getElementById(id)
      el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      router.replace({ query: route.query, hash: `#${id}` })
    }, 80)
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
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: 12px;
}
.chart-card {
  box-sizing: border-box;
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
}
.chart-title {
  font-size: 14px;
  font-weight: 600;
  color: #1d2129;
}
.chart-canvas {
  width: 100%;
  height: 220px;
}
.chart-canvas-tall {
  height: 240px;
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
