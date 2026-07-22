<script setup lang="ts">
// 系统设置 - 一级模块 workspace
//   - 默认打开：首页（统一方正卡片）
//   - 进入子页面：在顶部打开新 tab，可切换、可关闭
//   - 首页 tab 不可关闭

import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import AdminModuleHome from '@/components/admin/AdminModuleHome.vue'
import AdminTabbedWorkspace from '@/components/admin/AdminTabbedWorkspace.vue'
import CameraSettingsPage from './system/CameraSettingsPage.vue'
import DataBackup from './system/DataBackup.vue'
import SoftwareUpdate from './system/SoftwareUpdate.vue'
import VisualCalibration from './system/VisualCalibration.vue'

type SystemTabType = 'home' | 'visual-calibration' | 'data-backup' | 'software-update' | 'camera-settings'

interface SystemTab {
  key: string
  title: string
  type: SystemTabType
  closable: boolean
}

const tabTitleMap: Record<SystemTabType, string> = {
  home: '系统设置',
  'visual-calibration': '视觉矫正',
  'data-backup': '数据备份',
  'software-update': '软件更新',
  'camera-settings': '摄像头设置',
}

const tabs = ref<SystemTab[]>([
  { key: 'home', title: tabTitleMap.home, type: 'home', closable: false },
])
const activeTab = ref('home')

function openTab(type: SystemTabType) {
  const existing = tabs.value.find((t) => t.type === type)
  if (existing) {
    activeTab.value = existing.key
    return
  }
  const newTab: SystemTab = {
    key: type,
    title: tabTitleMap[type],
    type,
    closable: true,
  }
  tabs.value.push(newTab)
  activeTab.value = newTab.key
}

function onTabClose(key: string) {
  const idx = tabs.value.findIndex((t) => t.key === key)
  if (idx === -1) return
  const tab = tabs.value[idx]
  if (!tab || !tab.closable) return
  tabs.value.splice(idx, 1)
  if (activeTab.value === key) {
    const next = tabs.value[idx] || tabs.value[idx - 1] || tabs.value[0]
    if (next) activeTab.value = next.key
  }
}

const route = useRoute()
onMounted(() => {
  const want = String(route.query.tab || '')
  if (
    want === 'visual-calibration' ||
    want === 'data-backup' ||
    want === 'software-update' ||
    want === 'camera-settings'
  ) {
    openTab(want as SystemTabType)
  }
})

const allTabs = computed(() => tabs.value)

const componentTabs: Record<string, any> = {
  'visual-calibration': VisualCalibration,
  'data-backup': DataBackup,
  'software-update': SoftwareUpdate,
  'camera-settings': CameraSettingsPage,
}
</script>

<template>
  <div class="system-settings-page">
    <AdminTabbedWorkspace
      :tabs="allTabs"
      :active-key="activeTab"
      :component-tabs="componentTabs"
      @update:active-key="(k: string) => (activeTab = k)"
      @close="onTabClose"
    >
      <AdminModuleHome
        v-if="activeTab === 'home'"
        title="系统设置"
        description="管理视觉矫正、数据备份、软件更新与摄像头设置等后台运维功能"
        :cards="[
          { key: 'visual-calibration', title: '视觉矫正', icon: '📐', desc: '摄像头视角标定与视觉矫正配置', tone: 'primary' },
          { key: 'data-backup', title: '数据备份', icon: '💾', desc: '一键备份 data/ 全部数据', tone: 'success' },
          { key: 'software-update', title: '软件更新', icon: '🚀', desc: '检查并升级到最新版本', tone: 'warning' },
          { key: 'camera-settings', title: '摄像头设置', icon: '📷', desc: '摄像头连接、推流参数与设备状态配置', tone: 'info' }
        ]"
        :columns="3"
        @select="(k: string) => openTab(k as SystemTabType)"
      />
      <div v-else class="system-settings-empty">加载中…</div>
    </AdminTabbedWorkspace>
  </div>
</template>

<style scoped>
.system-settings-page {
  width: 100%;
  height: 100%;
  min-height: 0;
  box-sizing: border-box;
  background: #f5f7fb;
  overflow: hidden;
}
.system-settings-empty {
  padding: 32px;
  color: #86909c;
  font-size: 14px;
}
</style>