<script setup lang="ts">
// 数据报表 - 一级模块 workspace
//   - 默认打开：home（DataReportVideoList 视频检测列表）
//   - 进入子页面：在顶部打开新 tab，可切换、可关闭
//   - home tab 不可关闭
//   - 子页面通过 componentTabs 渲染在 AdminTabbedWorkspace 的内容区内
//   - 路由 query 自动行为：
//       ?type=daily-videos / daily / range / video_id → 打开鱼品质检测报表 tab
//       ?open=fish-quality / ?open=history → 打开对应 tab
//       ?open_pdf=1 & video_id → 打开对应视频的 PDF tab

import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AdminTabbedWorkspace from '@/components/admin/AdminTabbedWorkspace.vue'
import DataReportVideoList from './report/DataReportVideoList.vue'

const FishQualityReport = defineAsyncComponent(
  () => import('./report/FishQualityReport.vue'),
)
const DailyReportHistory = defineAsyncComponent(
  () => import('./report/DailyReportHistory.vue'),
)
const VideoPdfReportTab = defineAsyncComponent(
  () => import('./report/VideoPdfReportTab.vue'),
)

interface TabItem {
  key: string
  title: string
  closable?: boolean
}

const route = useRoute()
const router = useRouter()

const openTabs = ref<TabItem[]>([])
const activeKey = ref<string>('home')

const componentTabs: Record<string, any> = {
  'fish-quality': FishQualityReport,
  history: DailyReportHistory,
}

const tabTitleMap: Record<string, string> = {
  'fish-quality': '鱼品质检测报表',
  history: '历史日报',
}

function openTabByKey(key: string) {
  if (key === 'home') {
    activeKey.value = 'home'
    return
  }
  const title = tabTitleMap[key]
  if (!title) return
  if (!openTabs.value.find((t) => t.key === key)) {
    openTabs.value.push({ key, title, closable: true })
  }
  activeKey.value = key
}

function openPdfReportTab(payload: { videoId: string }): void {
  const videoId = String(payload?.videoId || '').trim()
  if (!videoId) return
  // 同一 video_id 复用同一 PDF tab key，禁止重复创建
  const key = `video-pdf-${videoId}`
  if (!openTabs.value.find((t) => t.key === key)) {
    openTabs.value.push({
      key,
      title: `报表-${videoId}`,
      closable: true,
    })
  }
  activeKey.value = key
}

function closeTab(key: string) {
  if (key === 'home') return
  const idx = openTabs.value.findIndex((t) => t.key === key)
  const wasActive = activeKey.value === key
  openTabs.value = openTabs.value.filter((t) => t.key !== key)
  if (wasActive) {
    // 优先激活左侧相邻 tab；左侧不存在则激活右侧；都无则回到 home
    const remaining = openTabs.value
    if (!remaining.length) {
      activeKey.value = 'home'
      return
    }
    const leftIdx = idx - 1
    const next =
      (leftIdx >= 0 && remaining[leftIdx]) ||
      remaining[idx] ||
      remaining[0]
    activeKey.value = next ? next.key : 'home'
  }
}

const allTabs = computed<TabItem[]>(() => [
  { key: 'home', title: '数据报表', closable: false },
  ...openTabs.value,
])

function openCustomReport(payload: { videoIds: string[] }): void {
  const videoIds = Array.isArray(payload?.videoIds)
    ? payload.videoIds.filter(Boolean).join(',')
    : ''
  if (!videoIds) return
  void router.push({
    path: '/admin/report-overview',
    query: { tab: 'custom', video_ids: videoIds },
  })
}

function isHomeOnlyQuery(): boolean {
  const open = String(route.query.open || '').trim()
  const videoId = String(route.query.video_id || '').trim()
  const type = String(route.query.type || '').trim()
  const openPdf = String(route.query.open_pdf || '').trim()
  return !openPdf && !open && !type && !videoId
}

function autoOpenFromQuery() {
  const open = String(route.query.open || '').trim()
  const videoId = String(route.query.video_id || '').trim()
  const type = String(route.query.type || '').trim()
  const openPdf = String(route.query.open_pdf || '').trim()

  // 优先级 1：open_pdf=1 + video_id → 打开 PDF tab
  if (openPdf === '1' && videoId) {
    openPdfReportTab({ videoId })
    return
  }
  // 优先级 2：open=fish-quality / open=history → 打开对应 tab
  if (open === 'fish-quality' || open === 'history') {
    openTabByKey(open)
    return
  }
  // 优先级 3：type=daily / range / daily-videos 或有 video_id → 鱼品质检测报表
  if (type === 'daily' || type === 'range' || type === 'daily-videos' || videoId) {
    openTabByKey('fish-quality')
    return
  }
  // 优先级 4：无业务 query → 回到 home 列表
  if (isHomeOnlyQuery()) {
    activeKey.value = 'home'
    // 不主动清 openTabs，避免覆盖用户已打开的子 tab
    return
  }
}

onMounted(() => {
  autoOpenFromQuery()
})

watch(
  () => [route.query.open, route.query.open_pdf, route.query.type, route.query.video_id, route.query.date].join('|'),
  () => {
    autoOpenFromQuery()
  },
)
</script>

<template>
  <div class="data-report-page">
    <AdminTabbedWorkspace
      :tabs="allTabs"
      :active-key="activeKey"
      :component-tabs="componentTabs"
      @update:active-key="(k: string) => (activeKey = k)"
      @close="closeTab"
    >
      <DataReportVideoList
        v-if="activeKey === 'home'"
        @open-pdf-report="openPdfReportTab"
        @open-custom-report="openCustomReport"
      />
      <VideoPdfReportTab
        v-else-if="activeKey.startsWith('video-pdf-')"
        :video-id="activeKey.slice('video-pdf-'.length)"
        :instance-id="activeKey"
      />
      <div v-else class="data-report-empty">加载中…</div>
    </AdminTabbedWorkspace>
  </div>
</template>

<style scoped>
.data-report-page {
  width: 100%;
  max-width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  box-sizing: border-box;
  background: #f5f7fb;
  overflow: hidden;
}
.data-report-empty {
  padding: 32px;
  color: #86909c;
  font-size: 14px;
}
</style>
