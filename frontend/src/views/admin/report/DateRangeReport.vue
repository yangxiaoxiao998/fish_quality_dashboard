<template>
  <div class="report-page-layout">
    <main class="report-main">
      <div class="report-header-card">
        <div>
          <h2 class="report-page-title">日期范围鱼类检测统计报告</h2>
          <div class="report-page-subtitle">{{ rangeStart }} 至 {{ rangeEnd }} 跨日期数据汇总</div>
        </div>
        <div class="report-header-actions">
          <a-space>
            <a-range-picker
              v-model="dateRange"
              value-format="YYYY-MM-DD"
              style="width: 280px"
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
        subtitle="以文字和指标卡片形式汇总时间范围内的检测数据。"
      >
        <div class="block-subtitle">时间范围概览</div>
        <ReportMetricsGrid :rows="overviewRows" />

        <div class="block-subtitle">跨日期核心指标</div>
        <ReportMetricsGrid :rows="metricsRows" />

        <div class="block-subtitle">鱼体检测汇总</div>
        <ReportMetricsGrid :rows="fishSummaryRows" />
      </ReportSection>

      <!-- 图表板块 -->
      <ReportSection
        id="chart-block"
        title="图表板块"
        subtitle="以统计图表形式展示按日趋势与体征分布。"
      >
        <a-empty
          v-if="!hasChartData"
          description="暂无可用于绘图的跨日期数据"
        />
        <div v-else class="charts-grid">
          <a-card :bordered="false" class="chart-card">
            <template #title><span class="chart-title">每日视频任务数趋势</span></template>
            <div ref="trendRef" class="chart-canvas chart-canvas-tall"></div>
          </a-card>
          <a-card :bordered="false" class="chart-card">
            <template #title><span class="chart-title">体长分布 (cm)</span></template>
            <div ref="lengthDistRef" class="chart-canvas"></div>
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

      <!-- Excel 板块 -->
      <ReportSection
        id="excel-block"
        title="Excel 板块"
        subtitle="以表格形式展示时间范围内所有视频任务数据。"
      >
        <a-empty v-if="!tasks.length" description="时间范围内暂无视频任务" />
        <div v-else class="table-scroll">
          <a-table
            :data="tasks"
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
        subtitle="提供时间范围内 done 视频的单视频报表与结果大屏入口。"
      >
        <a-empty
          v-if="!entryLinks.length"
          description="当前范围内暂无可展示视频"
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
import { http } from '@/api/http'
import { Message } from '@arco-design/web-vue'
import { toErrorMessage } from '@/api/http'
import { useReportData } from '@/composables/useReportData'
import { useReportCharts } from '@/composables/useReportCharts'
import ReportToc from '@/components/report/ReportToc.vue'
import ReportSection from '@/components/report/ReportSection.vue'
import ReportMetricsGrid from '@/components/report/ReportMetricsGrid.vue'
import { formatChinaDateTime } from '@/utils/time'
import type { ReportTocItem } from '@/components/report/ReportToc.vue'

const route = useRoute()
const router = useRouter()

const today = dayjs().format('YYYY-MM-DD')
const weekAgo = dayjs().subtract(6, 'day').format('YYYY-MM-DD')

const dateRange = ref<string[]>([weekAgo, today])

const tocItems: ReportTocItem[] = [
  { id: 'text-block', index: '1', title: '文字板块' },
  { id: 'chart-block', index: '2', title: '图表板块' },
  { id: 'excel-block', index: '3', title: 'Excel板块' },
  { id: 'video-block', index: '4', title: '视频板块' },
]

const rangeStart = computed(() => dateRange.value?.[0] || weekAgo)
const rangeEnd = computed(() => dateRange.value?.[1] || today)
const coverDays = computed(() => {
  try {
    const a = dayjs(rangeStart.value)
    const b = dayjs(rangeEnd.value)
    return Math.max(1, b.diff(a, 'day') + 1)
  } catch {
    return 0
  }
})

const reportSubtitle = computed(
  () => `${rangeStart.value} 至 ${rangeEnd.value}  跨日期数据汇总`
)

const { loading, summary, charts, loadAll } = useReportData(
  () => ({ start_date: rangeStart.value, end_date: rangeEnd.value })
)

const tasks = ref<any[]>([])

async function loadTasks() {
  try {
    const resp = await http.get('/api/videos', { params: { page: 1, page_size: 100 } })
    const items: any[] = resp.data?.items || []
    tasks.value = items.filter((v) => {
      const ca = String(v?.created_at || '').slice(0, 10)
      return ca >= rangeStart.value && ca <= rangeEnd.value
    })
  } catch (err) {
    tasks.value = []
    Message.error(toErrorMessage(err))
  }
}


const overviewRows = computed(() => [
  { key: 'days', title: '覆盖天数', value: coverDays.value, unit: '天' },
  { key: 'videos', title: '视频任务总数', value: summary.value.total_video, unit: '个' },
  { key: 'fishes', title: '检测总鱼数', value: summary.value.total_fish, unit: '条' },
  { key: 'valid', title: '完整鱼总数', value: summary.value.valid_fish, unit: '条' },
  { key: 'invalid', title: '不完整鱼总数', value: summary.value.abnormal_fish, unit: '条' },
  { key: 'damaged', title: '破损鱼总数', value: summary.value.surface_damage_count, unit: '条' },
])

const metricsRows = computed(() => [
  { key: 'len', title: '平均体长', value: summary.value.avg_body_length, unit: 'cm' },
  { key: 'noTail', title: '平均无尾体长', value: summary.value.avg_body_length_without_tail, unit: 'cm' },
  { key: 'height', title: '平均体高', value: summary.value.avg_body_height, unit: 'cm' },
  { key: 'weight', title: '平均体重', value: summary.value.avg_body_weight, unit: 'g' },
  { key: 'cf', title: '平均肥满度', value: summary.value.avg_condition_factor },
])

const fishSummaryRows = computed(() => [
  { key: 'fishTotal', title: '鱼总数', value: summary.value.total_fish, unit: '条' },
  { key: 'fishValid', title: '完整鱼数', value: summary.value.valid_fish, unit: '条' },
  { key: 'fishInvalid', title: '不完整鱼数', value: summary.value.abnormal_fish, unit: '条' },
  { key: 'fishDamaged', title: '破损鱼数', value: summary.value.surface_damage_count, unit: '条' },
])

const trendRef = ref<HTMLElement>()
const lengthDistRef = ref<HTMLElement>()
const heightDistRef = ref<HTMLElement>()
const weightDistRef = ref<HTMLElement>()
const conditionDistRef = ref<HTMLElement>()
const damageDistRef = ref<HTMLElement>()

useReportCharts(summary, charts, {
  trend: trendRef,
  lengthDist: lengthDistRef,
  heightDist: heightDistRef,
  weightDist: weightDistRef,
  conditionDist: conditionDistRef,
  damageDist: damageDistRef,
})

const hasChartData = computed(() => (summary.value.total_fish || 0) > 0)
const hasDamageData = computed(
  () => (charts.value.surface_damage_distribution?.length || 0) > 0
)

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

const entryLinks = computed(() => {
  return tasks.value
    .filter((v) => (v.status || '').toLowerCase() === 'done')
    .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))
    .slice(0, 6)
})

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
  loadTasks()
}

function handleRefresh() {
  loadAll()
  loadTasks()
}

watch([() => rangeStart.value, () => rangeEnd.value], () => {
  loadAll()
  loadTasks()
})

onMounted(async () => {
  await loadAll()
  await loadTasks()
  if (route.hash) {
    const id = route.hash.replace('#', '')
    setTimeout(() => {
      const el = document.getElementById(id)
      el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      router.replace({ query: route.query, hash: `#${id}` })
    }, 80)
  }
})
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
  height: 280px;
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

@media (width <= 1200px) {
  .report-page-layout {
    grid-template-columns: 1fr;
  }
  .report-aside {
    display: none;
  }
}
</style>
