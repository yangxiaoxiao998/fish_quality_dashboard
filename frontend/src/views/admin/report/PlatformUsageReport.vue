<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { Message } from '@arco-design/web-vue'
import { http, toErrorMessage } from '@/api/http'
import { formatChinaDateTime } from '@/utils/time'

type RangeKey = '7d' | '30d' | 'all'

interface SummaryData {
  total_video_tasks: number
  upload_video_count: number
  camera_video_count: number
  done_task_count: number
  failed_task_count: number
  no_fish_task_count: number
  success_rate: number
  failure_rate: number
  total_fish: number
  avg_fish_per_video: number
  total_video_duration: number
  avg_processing_duration: number
  avg_fps: number
  camera_start_count: number
}

interface ChartBucket {
  date: string
  count?: number
  avg_seconds?: number
}

interface NameCount {
  label: string
  count: number
}

interface FailureItem {
  reason: string
  count?: number
  video_id?: string | number | null
  stage?: string | null
  created_at?: string | null
}

interface ChartsData {
  daily_video_task_trend: ChartBucket[]
  daily_fish_trend: ChartBucket[]
  source_type_distribution: NameCount[]
  task_status_distribution: NameCount[]
  daily_failed_task_trend: ChartBucket[]
  failure_reason_rank: FailureItem[]
  avg_processing_duration_trend: ChartBucket[]
}

const RANGE_OPTIONS: { key: RangeKey; label: string }[] = [
  { key: '7d', label: '最近七天' },
  { key: '30d', label: '最近一个月' },
  { key: 'all', label: '全部使用' }
]

const range = ref<RangeKey>('7d')
const summary = ref<SummaryData | null>(null)
const charts = ref<ChartsData>({
  daily_video_task_trend: [],
  daily_fish_trend: [],
  source_type_distribution: [],
  task_status_distribution: [],
  daily_failed_task_trend: [],
  failure_reason_rank: [],
  avg_processing_duration_trend: []
})
const failureItems = ref<FailureItem[]>([])

const loading = ref(false)

const taskTrendChartRef = ref<HTMLElement>()
const fishTrendChartRef = ref<HTMLElement>()
const taskStatusChartRef = ref<HTMLElement>()
const durationTrendChartRef = ref<HTMLElement>()

let taskTrendChart: echarts.ECharts | null = null
let fishTrendChart: echarts.ECharts | null = null
let taskStatusChart: echarts.ECharts | null = null
let durationTrendChart: echarts.ECharts | null = null
const allCharts: echarts.ECharts[] = []

const metricCards = computed(() => {
  const s = summary.value
  if (!s) {
    return [
      { title: '视频任务总数', value: '--', unit: '个' },
      { title: '上传视频数量', value: '--', unit: '个' },
      { title: '摄像头视频数量', value: '--', unit: '个' },
      { title: '完成任务数量', value: '--', unit: '个' },
      { title: '失败任务数量', value: '--', unit: '个' },
      { title: '无鱼任务数量', value: '--', unit: '个' },
      { title: '任务成功率', value: '--', unit: '%' },
      { title: '任务失败率', value: '--', unit: '%' },
      { title: '总检测鱼数量', value: '--', unit: '条' },
      { title: '平均每个视频检测鱼数量', value: '--', unit: '条' },
      { title: '总视频时长', value: '--', unit: 's' },
      { title: '平均处理耗时', value: '--', unit: 's' },
      { title: '平均 FPS', value: '--', unit: '' },
      { title: '摄像头开启次数', value: '--', unit: '次' }
    ]
  }
  return [
    { title: '视频任务总数', value: s.total_video_tasks, unit: '个' },
    { title: '上传视频数量', value: s.upload_video_count, unit: '个' },
    { title: '摄像头视频数量', value: s.camera_video_count, unit: '个' },
    { title: '完成任务数量', value: s.done_task_count, unit: '个' },
    { title: '失败任务数量', value: s.failed_task_count, unit: '个' },
    { title: '无鱼任务数量', value: s.no_fish_task_count, unit: '个' },
    { title: '任务成功率', value: Number((s.success_rate * 100).toFixed(2)), unit: '%' },
    { title: '任务失败率', value: Number((s.failure_rate * 100).toFixed(2)), unit: '%' },
    { title: '总检测鱼数量', value: s.total_fish, unit: '条' },
    { title: '平均每个视频检测鱼数量', value: s.avg_fish_per_video, unit: '条' },
    { title: '总视频时长', value: s.total_video_duration, unit: 's' },
    { title: '平均处理耗时', value: s.avg_processing_duration, unit: 's' },
    { title: '平均 FPS', value: s.avg_fps, unit: '' },
    { title: '摄像头开启次数', value: s.camera_start_count, unit: '次' }
  ]
})

function fmtInt(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === '') return '--'
  const n = Number(v)
  if (Number.isNaN(n)) return String(v)
  return n.toLocaleString('zh-CN')
}

function formatDurationCn(seconds: number): string {
  const s = Number(seconds)
  if (!Number.isFinite(s) || s <= 0) return '0分钟'
  const totalMinutes = Math.round(s / 60)
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  if (hours <= 0) return `${minutes}分钟`
  if (minutes <= 0) return `${hours}小时`
  return `${hours}小时${minutes}分钟`
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    done: '已完成',
    failed: '失败',
    recording: '录制中',
    processing: '处理中',
    running: '运行中',
    queued: '排队中',
    interrupted: '已中断',
    partial: '部分完成',
    unknown: '未知',
  }
  return map[String(status || '').toLowerCase()] || status || '未知'
}

function buildTrendOption(
  buckets: ChartBucket[],
  label: string,
  color: [string, string]
): echarts.EChartsCoreOption {
  const xs = buckets.map((b) => b.date)
  const ys = buckets.map((b) => Number(b.count ?? 0))
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    grid: { top: 24, right: 16, bottom: 28, left: 40 },
    xAxis: {
      type: 'category',
      data: xs,
      axisLine: { lineStyle: { color: '#d9d9d9' } },
      axisLabel: { color: '#4e5969', fontSize: 11 }
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: '#e5e6eb' } },
      axisLabel: { color: '#86909c', fontSize: 11 }
    },
    series: [
      {
        name: label,
        type: 'line',
        smooth: true,
        symbol: 'circle',
        symbolSize: 6,
        data: ys,
        lineStyle: { color: color[0], width: 2 },
        itemStyle: { color: color[0] },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: color[1] },
            { offset: 1, color: 'rgba(255,255,255,0)' }
          ])
        }
      }
    ]
  }
}

function buildDurationOption(buckets: ChartBucket[]): echarts.EChartsCoreOption {
  const xs = buckets.map((b) => b.date)
  const ys = buckets.map((b) => Number(b.avg_seconds ?? 0))
  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const item = Array.isArray(params) ? params[0] : null
        return `${item?.axisValue || ''}<br/>平均处理耗时：${formatDurationCn(Number(item?.value || 0))}`
      },
    },
    grid: { top: 24, right: 16, bottom: 28, left: 72 },
    xAxis: {
      type: 'category',
      data: xs,
      axisLine: { lineStyle: { color: '#d9d9d9' } },
      axisLabel: { color: '#4e5969', fontSize: 11 }
    },
    yAxis: {
      type: 'value',
      name: '平均处理耗时',
      nameTextStyle: { color: '#86909c', fontSize: 11 },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: '#e5e6eb' } },
      axisLabel: { color: '#86909c', fontSize: 11, formatter: (value: number) => formatDurationCn(value) }
    },
    series: [
      {
        type: 'bar',
        data: ys,
        barMaxWidth: 28,
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: '#722ed1' },
            { offset: 1, color: '#b37feb' }
          ]),
          borderRadius: [4, 4, 0, 0]
        }
      }
    ]
  }
}

function buildPieOption(items: NameCount[]): echarts.EChartsCoreOption {
  const data = items.map((it) => ({ name: statusLabel(it.label), value: it.count }))
  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      formatter: (p: any) => `${p.name}：${p.value} 个（${Number(p.percent || 0).toFixed(1)}%）`,
    },
    legend: { bottom: 4, type: 'scroll', textStyle: { fontSize: 11, color: '#4e5969' } },
    series: [
      {
        type: 'pie',
        radius: ['38%', '65%'],
        center: ['50%', '50%'],
        data,
        label: { fontSize: 11, color: '#1d2129', formatter: '{b}' }
      }
    ]
  }
}

function safeArray<T>(v: any): T[] {
  return Array.isArray(v) ? v : []
}

function renderCharts() {
  const c = charts.value || {}
  if (taskTrendChart) {
    taskTrendChart.setOption(
      buildTrendOption(safeArray(c.daily_video_task_trend), '任务数', ['#1677ff', 'rgba(22,119,255,0.35)']),
      true
    )
  }
  if (fishTrendChart) {
    fishTrendChart.setOption(
      buildTrendOption(safeArray(c.daily_fish_trend), '鱼数量', ['#13c2c2', 'rgba(19,194,194,0.35)']),
      true
    )
  }
  if (taskStatusChart) {
    taskStatusChart.setOption(buildPieOption(safeArray(c.task_status_distribution)), true)
  }
  if (durationTrendChart) {
    durationTrendChart.setOption(buildDurationOption(safeArray(c.avg_processing_duration_trend)), true)
  }
}

function initCharts() {
  if (taskTrendChartRef.value) {
    taskTrendChart = echarts.init(taskTrendChartRef.value)
    allCharts.push(taskTrendChart)
  }
  if (fishTrendChartRef.value) {
    fishTrendChart = echarts.init(fishTrendChartRef.value)
    allCharts.push(fishTrendChart)
  }
  if (taskStatusChartRef.value) {
    taskStatusChart = echarts.init(taskStatusChartRef.value)
    allCharts.push(taskStatusChart)
  }
  if (durationTrendChartRef.value) {
    durationTrendChart = echarts.init(durationTrendChartRef.value)
    allCharts.push(durationTrendChart)
  }
}

function disposeCharts() {
  allCharts.forEach((c) => c && c.dispose())
  allCharts.length = 0
  taskTrendChart = null
  fishTrendChart = null
  taskStatusChart = null
  durationTrendChart = null
}

function handleResize() {
  allCharts.forEach((c) => c && c.resize())
}

async function loadSummary() {
  try {
    const resp = await http.get('/api/reports/platform-usage/summary', {
      params: { range: range.value }
    })
    summary.value = resp.data
  } catch (err) {
    Message.error(toErrorMessage(err))
  }
}

async function loadCharts() {
  try {
    const resp = await http.get('/api/reports/platform-usage/charts', {
      params: { range: range.value }
    })
    charts.value = resp.data
    await nextTick()
    renderCharts()
  } catch (err) {
    Message.error(toErrorMessage(err))
  }
}

async function loadFailures() {
  try {
    const resp = await http.get('/api/reports/platform-usage/failures', {
      params: { range: range.value }
    })
    failureItems.value = Array.isArray(resp.data?.items) ? resp.data.items : []
  } catch (err) {
    Message.error(toErrorMessage(err))
    failureItems.value = []
  }
}

async function refreshAll() {
  loading.value = true
  try {
    await Promise.all([loadSummary(), loadCharts(), loadFailures()])
  } finally {
    loading.value = false
  }
}

function handleRangeChange(key: RangeKey) {
  if (range.value === key) return
  range.value = key
  refreshAll()
}

onMounted(() => {
  initCharts()
  refreshAll()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  disposeCharts()
})
</script>

<template>
  <div class="report-page">
    <div class="report-page-inner platform-usage-report">
    <a-card :bordered="false" class="range-card">
      <a-radio-group
        :model-value="range"
        type="button"
        size="medium"
        @change="(v: any) => handleRangeChange(v as RangeKey)"
      >
        <a-radio
          v-for="opt in RANGE_OPTIONS"
          :key="opt.key"
          :value="opt.key"
        >
          {{ opt.label }}
        </a-radio>
      </a-radio-group>
    </a-card>

    <a-spin :loading="loading">
      <div class="metric-section">
        <div
          v-for="m in metricCards"
          :key="m.title"
          class="metric-card"
        >
          <div class="metric-title">{{ m.title }}</div>
          <div class="metric-value">
            {{ fmtInt(m.value) }}
            <span v-if="m.unit" class="metric-unit">{{ m.unit }}</span>
          </div>
        </div>
      </div>

      <a-row :gutter="[12, 12]" class="charts-section">
        <a-col :span="24">
          <a-card :bordered="false" class="chart-card">
            <template #title><span class="chart-title">每日视频任务数量趋势</span></template>
            <div ref="taskTrendChartRef" class="chart-container chart-container-tall" />
          </a-card>
        </a-col>
        <a-col :span="24">
          <a-card :bordered="false" class="chart-card">
            <template #title><span class="chart-title">每日检测鱼数量趋势</span></template>
            <div ref="fishTrendChartRef" class="chart-container chart-container-tall" />
          </a-card>
        </a-col>
        <a-col :xs="24" :sm="12" :md="12" :lg="12">
          <a-card :bordered="false" class="chart-card">
            <template #title><span class="chart-title">任务状态分布</span></template>
            <div ref="taskStatusChartRef" class="chart-container" />
          </a-card>
        </a-col>
        <a-col :span="24">
          <div class="failure-detail-card">
            <div class="chart-card-title">失败原因明细</div>
            <div class="failure-table-wrap">
              <table class="excel-like-table">
                <thead>
                  <tr>
                    <th>序号</th>
                    <th>视频编号</th>
                    <th>失败阶段</th>
                    <th>失败原因</th>
                    <th>发生时间</th>
                  </tr>
                </thead>
                <tbody v-if="failureItems.length > 0">
                  <tr v-for="(item, idx) in failureItems" :key="`${item.video_id || idx}-${item.reason}`">
                    <td>{{ idx + 1 }}</td>
                    <td>{{ item.video_id || '--' }}</td>
                    <td>{{ item.stage || '--' }}</td>
                    <td>{{ item.reason || '--' }}</td>
                    <td>{{ formatChinaDateTime(item.created_at) }}</td>
                  </tr>
                </tbody>
              </table>
              <a-empty v-if="failureItems.length === 0" description="暂无失败原因记录" />
            </div>
          </div>
        </a-col>
        <a-col :span="24">
          <a-card :bordered="false" class="chart-card">
            <template #title><span class="chart-title">平均处理耗时趋势</span></template>
            <div ref="durationTrendChartRef" class="chart-container chart-container-tall" />
          </a-card>
        </a-col>
      </a-row>
    </a-spin>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.report-page {
  height: 100%;
  min-height: 0;
  box-sizing: border-box;
  overflow: hidden auto;
  background: #f5f7fb;
}
.report-page-inner {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
  padding-bottom: 24px;
  box-sizing: border-box;
}
.platform-usage-report {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px 0;
}
.range-card {
  background: #fff;
  border-radius: 8px;
}
.metric-section {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 12px;

  @media (width <= 1600px) {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }

  @media (width <= 1200px) {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  @media (width <= 768px) {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .metric-card {
    padding: 14px 16px;
    border-radius: 12px;
    background: #fff;
    border: 1px solid #edf1f7;
    .metric-title {
      font-size: 12px;
      color: #6b7280;
    }
    .metric-value {
      margin-top: 6px;
      font-size: 22px;
      font-weight: 700;
      color: #1f2937;
      font-family: SarasaMonoSC, monospace;
      .metric-unit {
        font-size: 12px;
        margin-left: 4px;
        color: #6b7280;
        font-weight: 400;
      }
    }
  }
}
.charts-section {
  :deep(.chart-card) {
    background: #fff;
    border-radius: 12px;
    .chart-title {
      font-size: 14px;
      font-weight: 600;
      color: #1d2129;
    }
    .chart-container {
      width: 100%;
      height: 220px;
    }
    .chart-container-tall {
      height: 240px;
    }
    .chart-card-title {
      font-size: 14px;
      font-weight: 600;
      color: #1d2129;
      margin-bottom: 12px;
    }
  }
  :deep(.arco-card-body) {
    padding: 12px 16px 16px;
  }
}
.failure-detail-card {
  height: 420px;
  padding: 16px;
  box-sizing: border-box;
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 12px;
  box-shadow: 0 8px 24px rgb(32 91 179 / 8%);
  overflow: hidden;
}
.failure-table-wrap {
  height: 340px;
  overflow: auto;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
}
.excel-like-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.excel-like-table th,
.excel-like-table td {
  border: 1px solid #e5e6eb;
  padding: 8px 10px;
  text-align: left;
  white-space: nowrap;
}
.excel-like-table thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: #f2f6ff;
  color: #1d2129;
}
.excel-like-table tbody tr:nth-child(even) {
  background: #fafcff;
}
</style>