<script setup lang="ts">
// 通用后台模块浏览器式 tab 工作区
// 用于：
//   - 数据报表
//   - 系统运维
//   - 系统设置
//
// 行为：
//   - 首页 tab 不可关闭
//   - 子页面 tab 可切换、可关闭
//   - 支持每个 tab 独立传入 component + componentProps
//   - 关闭非当前 tab：仅移除该 tab，不改变当前激活 tab
//   - 关闭当前 tab：优先激活左侧相邻 tab；左侧不存在则激活右侧；再无则回到 home

import { computed, ref, watch } from 'vue'
import type { Component } from 'vue'

interface TabItem {
  key: string
  title: string
  closable?: boolean
  component?: Component | null
  componentProps?: Record<string, unknown>
}

const props = withDefaults(
  defineProps<{
    tabs: TabItem[]
    activeKey: string
    componentTabs?: Record<string, Component | null>
  }>(),
  { componentTabs: () => ({}) },
)

const emit = defineEmits<{
  (e: 'update:activeKey', key: string): void
  (e: 'close', key: string): void
}>()

const internalTabs = ref<TabItem[]>([])

watch(
  () => props.tabs,
  (next) => {
    internalTabs.value = Array.isArray(next) ? next.slice() : []
  },
  { immediate: true, deep: true },
)

const visibleTabs = computed(() => internalTabs.value)

const activeTab = computed<TabItem | null>(() => {
  return visibleTabs.value.find((t) => t.key === props.activeKey) || null
})

const activeComponent = computed<Component | null>(() => {
  const tab = activeTab.value
  if (tab?.component) return tab.component
  return props.componentTabs?.[props.activeKey] || null
})

const activeComponentProps = computed<Record<string, unknown>>(() => {
  return activeTab.value?.componentProps || {}
})

function activate(key: string) {
  emit('update:activeKey', key)
}

function closeTab(key: string) {
  const idx = internalTabs.value.findIndex((t) => t.key === key)
  if (idx < 0) return
  const isActive = props.activeKey === key

  // 计算关闭后要激活的下一个 tab
  let nextKey: string | null = null
  if (isActive) {
    const remaining = internalTabs.value.filter((t) => t.key !== key)
    if (remaining.length) {
      const leftCandidate = idx - 1 >= 0 ? remaining[idx - 1] : undefined
      const rightCandidate = idx < remaining.length ? remaining[idx] : undefined
      const next = leftCandidate || rightCandidate || remaining[0]
      nextKey = next ? next.key : 'home'
    } else {
      nextKey = 'home'
    }
  }

  // 先 emit close：父组件必须真正从 openTabs 删除该 key 并设置新的 activeKey
  emit('close', key)
  // 子组件内部列表同步删除（仅用于显示渲染，prop watcher 会再次同步）
  internalTabs.value = internalTabs.value.filter((t) => t.key !== key)
  // emit update:activeKey：通知父组件切换 activeKey
  if (isActive && nextKey) {
    emit('update:activeKey', nextKey)
  }
}
</script>

<template>
  <div class="admin-workspace">
    <div class="admin-browser-tabs">
      <div
        v-for="tab in visibleTabs"
        :key="tab.key"
        class="admin-browser-tab"
        :class="{ active: tab.key === activeKey }"
        @click="activate(tab.key)"
      >
        <span class="tab-title">{{ tab.title }}</span>
        <button
          v-if="tab.closable !== false && tab.key !== 'home'"
          class="tab-close"
          aria-label="关闭标签"
          @click.stop="closeTab(tab.key)"
        >
          ×
        </button>
      </div>
    </div>
    <div class="admin-tab-content">
      <component
        :is="activeComponent"
        v-if="activeComponent"
        :key="activeTab?.key || activeKey"
        v-bind="activeComponentProps"
      />
      <slot v-else />
    </div>
  </div>
</template>

<style scoped>
.admin-workspace {
  width: 100%;
  max-width: 100%;
  height: 100%;
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: #f5f7fb;
  overflow: hidden;
  box-sizing: border-box;
}
.admin-browser-tabs {
  flex: 0 0 auto;
  display: flex;
  align-items: flex-end;
  gap: 6px;
  padding: 10px 14px 0;
  background: #eef4ff;
  border-bottom: 1px solid #dce8ff;
  overflow-x: auto;
  z-index: 10;
  position: relative;
}
.admin-browser-tab {
  height: 38px;
  padding: 0 14px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: 1px solid #dce8ff;
  border-bottom: 0;
  border-radius: 10px 10px 0 0;
  background: #f7faff;
  color: #4e5969;
  cursor: pointer;
  font: inherit;
  font-size: 13px;
  white-space: nowrap;
  transition: background 0.15s ease, color 0.15s ease;
}
.admin-browser-tab:hover {
  color: #165dff;
}
.admin-browser-tab.active {
  background: #fff;
  color: #165dff;
  font-weight: 600;
}
.tab-title {
  line-height: 1;
}
.tab-close {
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #86909c;
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  padding: 0;
}
.tab-close:hover {
  background: rgb(245 34 45 / 12%);
  color: #f5222d;
}
.admin-tab-content {
  flex: 1 1 auto;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: auto;

  /* 透出 .admin-workspace 的 #f5f7fb 背景，避免与首页 admin-module-home 出现白色断层。
     子页面若需要白底展示表单/图表，自行在自己的根容器上设置 background: #fff 即可。 */
  background: transparent;
  box-sizing: border-box;
}
.admin-tab-content > .data-report-page {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  overflow: hidden;
}
.admin-tab-content > * {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  min-height: 0;
  box-sizing: border-box;
}
</style>
