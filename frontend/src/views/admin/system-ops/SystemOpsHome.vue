<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import AdminModuleHome from '@/components/admin/AdminModuleHome.vue'
import AdminTabbedWorkspace from '@/components/admin/AdminTabbedWorkspace.vue'
import { logUserAction } from '@/utils/operationLogger'

const PlatformUsageReport = defineAsyncComponent(
  () => import('@/views/admin/report/PlatformUsageReport.vue'),
)
const OperationLogReport = defineAsyncComponent(
  () => import('@/views/admin/report/OperationLogReport.vue'),
)
const AlgorithmTaskMonitor = defineAsyncComponent(
  () => import('@/views/admin/system-ops/AlgorithmTaskMonitor.vue'),
)

const route = useRoute()

const cards = [
  {
    key: 'platform-usage',
    title: '平台使用分析',
    icon: '📈',
    desc: '查看平台使用量、任务状态与运行趋势',
    tone: 'primary' as const,
  },
  {
    key: 'operation-log',
    title: '操作日志',
    icon: '📝',
    desc: '查看系统操作记录与用户行为日志',
    tone: 'info' as const,
  },
  {
    key: 'algorithm-tasks',
    title: '算法任务监控',
    icon: '🛠️',
    desc: '查看算法队列、处理阶段、耗时、错误日志和结果文件状态（仅运维）',
    tone: 'warning' as const,
  },
]

interface TabItem {
  key: string
  title: string
  closable?: boolean
}

const componentTabs: Record<string, any> = {
  'platform-usage': PlatformUsageReport,
  'operation-log': OperationLogReport,
  'algorithm-tasks': AlgorithmTaskMonitor,
}

const tabTitleMap: Record<string, string> = {
  'platform-usage': '平台使用分析',
  'operation-log': '操作日志',
  'algorithm-tasks': '算法任务监控',
}

const tabActionMap: Record<string, string> = {
  'platform-usage': 'open_platform_usage',
  'operation-log': 'open_operation_log',
  'algorithm-tasks': 'open_algorithm_tasks',
}

const openTabs = ref<TabItem[]>([])
const activeKey = ref<string>('home')

const allTabs = computed<TabItem[]>(() => [
  { key: 'home', title: '首页', closable: false },
  ...openTabs.value,
])

function selectCard(key: string) {
  openTab(key)
}

function openTab(key: string) {
  if (key === 'home') {
    activeKey.value = 'home'
    return
  }
  const title = tabTitleMap[key]
  const action = tabActionMap[key]
  if (!title || !action) return
  logUserAction({
    module: '系统运维',
    action,
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

onMounted(() => {
  const want = String(route.query.open || '').trim()
  if (
    want === 'platform-usage' ||
    want === 'operation-log' ||
    want === 'algorithm-tasks'
  ) {
    openTab(want)
  }
})
</script>

<template>
  <div class="system-ops-page">
    <AdminTabbedWorkspace
      :tabs="allTabs"
      :active-key="activeKey"
      :component-tabs="componentTabs"
      @update:active-key="(k: string) => (activeKey = k)"
      @close="closeTab"
    >
      <AdminModuleHome
        v-if="activeKey === 'home'"
        title="系统运维"
        description="查看平台运行状态、使用统计与系统操作记录"
        :cards="cards"
        :columns="3"
        @select="selectCard"
      />
      <div v-else class="system-ops-empty">加载中…</div>
    </AdminTabbedWorkspace>
  </div>
</template>

<style scoped>
.system-ops-page {
  width: 100%;
  height: 100%;
  min-height: 0;
  box-sizing: border-box;
  background: #f5f7fb;
  overflow: hidden;
}
.system-ops-empty {
  padding: 32px;
  color: #86909c;
  font-size: 14px;
}
</style>