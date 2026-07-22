<script setup lang="ts">
// 数据报表 - 一级模块 workspace
//   - 默认打开：首页（方正卡片）
//   - 进入子页面：在顶部打开新 tab，可切换、可关闭
//   - 首页 tab 不可关闭
//   - 子页面通过 componentTabs 渲染在 AdminTabbedWorkspace 的内容区内
//     绝不外溢成旧全屏页面

import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AdminModuleHome from '@/components/admin/AdminModuleHome.vue'
import AdminTabbedWorkspace from '@/components/admin/AdminTabbedWorkspace.vue'
import { logUserAction } from '@/utils/operationLogger'

const FishQualityReport = defineAsyncComponent(() => import('./report/FishQualityReport.vue'))
const DailyReportHistory = defineAsyncComponent(() => import('./report/DailyReportHistory.vue'))

interface TabItem {
  key: string
  title: string
  closable?: boolean
}

const cards = [
  {
    key: 'fish-quality',
    title: '鱼品质检测报表',
    icon: '📊',
    desc: '查看当日数据、当日视频总览与日期范围统计',
    tone: 'primary' as const,
  },
  {
    key: 'history',
    title: '历史日报',
    icon: '📄',
    desc: '查看已生成的日报列表与 PDF 预览',
    tone: 'info' as const,
  },
]

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

function selectCard(key: string) {
  openTabByKey(key)
}

function openTabByKey(key: string) {
  if (key === 'home') {
    activeKey.value = 'home'
    return
  }
  const title = tabTitleMap[key]
  if (!title) return
  logUserAction({
    module: '数据报表',
    action: 'open_report_entry',
    targetType: 'page',
    targetId: key,
    message: `打开${title}`,
  })
  if (!openTabs.value.find((t) => t.key === key)) {
    openTabs.value.push({ key, title, closable: true })
  }
  activeKey.value = key
}

function closeTab(key: string) {
  if (key === 'home') return
  openTabs.value = openTabs.value.filter((t) => t.key !== key)
  if (activeKey.value === key) {
    activeKey.value = 'home'
  }
}

const allTabs = computed<TabItem[]>(() => [
  { key: 'home', title: '首页', closable: false },
  ...openTabs.value,
])

const route = useRoute()

// 由 query.open 或 query.type=daily-videos 自动打开对应 tab。
function autoOpenFromQuery() {
  const open = String(route.query.open || '').trim()
  if (open === 'fish-quality' || open === 'history') {
    openTabByKey(open)
    return
  }
  const type = String(route.query.type || '').trim()
  const hasVideoId = !!String(route.query.video_id || '').trim()
  // 任意带 type=daily / range / daily-videos 的跳转都自动打开鱼品质检测报表
  if (type === 'daily' || type === 'range' || type === 'daily-videos') {
    openTabByKey('fish-quality')
    return
  }
  // 仅带 video_id 但没有 type 时，也默认打开鱼品质检测报表
  if (hasVideoId && !open) {
    openTabByKey('fish-quality')
  }
}

onMounted(() => {
  autoOpenFromQuery()
})

watch(
  () => route.query,
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
      <AdminModuleHome
        v-if="activeKey === 'home'"
        title="数据报表"
        description="请选择需要查看的报表类型。点击卡片进入对应报表子页面。"
        :cards="cards"
        :columns="3"
        @select="selectCard"
      />
      <div v-else class="data-report-empty">加载中…</div>
    </AdminTabbedWorkspace>
  </div>
</template>

<style scoped>
.data-report-page {
  width: 100%;
  height: 100%;
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