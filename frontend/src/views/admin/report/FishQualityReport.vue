<template>
  <div class="fish-quality-report">
    <div class="fish-quality-header">
      <h2 class="fish-quality-title">鱼品质检测报表</h2>
      <div class="fish-quality-subtitle">汇总分析：当日视频总览 / 当日数据 / 日期范围</div>
    </div>

    <a-card :bordered="false" class="tabs-card">
      <a-tabs v-model:active-key="activeKey" type="line" size="medium" @change="handleTabChange">
        <a-tab-pane key="daily-videos" title="当日视频总览" />
        <a-tab-pane key="daily" title="当日数据" />
        <a-tab-pane key="range" title="日期范围" />
      </a-tabs>
    </a-card>

    <div class="tab-pane-wrap">
      <DailyReportVideoOverview
        v-if="activeKey === 'daily-videos'"
        :key="String(route.query.video_id || 'default')"
      />
      <DailyReportToday v-else-if="activeKey === 'daily'" />
      <DateRangeReport v-else-if="activeKey === 'range'" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import DailyReportToday from './DailyReportToday.vue'
import DailyReportVideoOverview from './DailyReportVideoOverview.vue'
import DateRangeReport from './DateRangeReport.vue'

const route = useRoute()
const router = useRouter()

type ReportType = 'daily-videos' | 'daily' | 'range'

const validTypes = new Set<ReportType>(['daily', 'daily-videos', 'range'])

const activeKey = ref<ReportType>('daily-videos')

function readTypeFromQuery(): ReportType {
  const t = String(route.query.type || '').trim()
  if (validTypes.has(t as ReportType)) return t as ReportType
  return 'daily-videos'
}

function handleTabChange(key: string | number) {
  const k = String(key) as ReportType
  if (!validTypes.has(k)) return
  activeKey.value = k
  router.replace({
    path: route.path,
    query: { ...route.query, type: k },
    hash: '',
  })
}

watch(
  () => route.query.type,
  (val) => {
    if (typeof val !== 'string') return
    if (validTypes.has(val as ReportType) && val !== activeKey.value) {
      activeKey.value = val as ReportType
    }
  }
)

activeKey.value = readTypeFromQuery()
</script>

<style scoped>
.fish-quality-report {
  min-height: 100%;
  height: 100%;
  box-sizing: border-box;
  padding: 16px;
  background: #f5f7fb;
}
.fish-quality-header {
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  padding: 16px 20px;
  box-shadow: 0 2px 8px rgb(0 0 0 / 6%);
}
.fish-quality-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  line-height: 1.5;
  color: #1d2129;
}
.fish-quality-subtitle {
  margin-top: 4px;
  font-size: 13px;
  line-height: 1.5;
  color: #86909c;
}
.tabs-card {
  margin-top: 16px;
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  padding: 4px 12px 0;
  box-shadow: 0 2px 8px rgb(0 0 0 / 6%);
}
.tab-pane-wrap {
  margin-top: 16px;
  min-width: 0;
}
</style>
