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
//   - 子页面内容由调用方通过 componentTabs / activeComponent 传入，
//     渲染在 admin-tab-content 内，绝不外溢。

import { computed, ref, watch } from 'vue'
import type { Component } from 'vue'

interface TabItem {
  key: string
  title: string
  closable?: boolean
  component?: Component | null
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

const activeComponent = computed<Component | null>(() => {
  const tab = internalTabs.value.find((t) => t.key === props.activeKey)
  if (tab?.component) return tab.component
  return props.componentTabs?.[props.activeKey] || null
})

function activate(key: string) {
  emit('update:activeKey', key)
}

function closeTab(key: string) {
  emit('close', key)
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
      <component :is="activeComponent" v-if="activeComponent" />
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
.admin-tab-content > * {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  min-height: 0;
  box-sizing: border-box;
}
</style>