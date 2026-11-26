<template>
  <div class="admin-layout">
    <!-- 顶部Header -->
    <div class="admin-header">
      <div class="header-brand">
        <i class="fa-solid fa-fish logo-icon"></i>
        <span class="header-brand-name">鱼品质智能检测系统</span>
      </div>
      <div class="header-right">
        <button v-if="isAdministratorRole" class="nav-btn" @click="leaveAdministratorMode">
          退出管理员
        </button>
        <button 
          class="nav-btn" 
          :class="{ active: isDashboard }" 
          @click="goToDashboard"
        >
          <IconDesktop />
           返回首页
        </button>
        <button 
          class="nav-btn" 
          :class="{ active: isAdmin }" 
          @click="goToAdmin"
        >
          <IconSettings />
          数据管理
        </button>
      </div>
    </div>

    <!-- 左侧侧边栏 -->
    <div class="admin-sidebar">
      <div class="sidebar-menu">
        <div class="menu-group">
          <div class="menu-group-title">数据管理</div>
          <div
            class="menu-item"
            :class="{ active: currentRoute === 'AdminDataReport' }"
            @click="goToPage('data-report')"
          >
            <IconBarChart />
            <span>数据报表</span>
          </div>
          <div
            class="menu-item"
            :class="{ active: currentRoute === 'AdminReportOverview' }"
            @click="goToPage('report-overview')"
          >
            <IconBarChart />
            <span>报表总览</span>
          </div>
          <div
            v-if="isAdministratorRole"
            class="menu-item"
            :class="{ active: currentRoute === 'AdminSystemOps' || currentRoute === 'AdminPlatformUsageReport' || currentRoute === 'AdminOperationLogReport' }"
            @click="goToPage('system-ops')"
          >
            <IconBarChart />
            <span>系统运维</span>
          </div>
          <div
            v-if="isAdministratorRole"
            class="menu-item"
            :class="{ active: currentRoute === 'AdminSystemSettings' }"
            @click="goToPage('system-settings')"
          >
            <IconSettings />
            <span>系统设置</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 右侧内容区 -->
    <div class="admin-content">
      <router-view />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import {
  IconDesktop,
  IconSettings,
  IconBarChart
} from '@arco-design/web-vue/es/icon'
import { administratorRole, isAdministrator, requestTechnicalDebugAccess, setAdministrator } from '@/utils/technicalDebugAuth'

// 注：原代码使用了 <IconFish /> 但 arco-design/web-vue 并不导出该组件，
// 容易在生产构建时出现 Vue 解析告警。这里改用 FontAwesome 的 fa-fish。

// 另外，全局 FontAwesome 样式在 main.ts 里已经统一引入，
// 这里不再重复 import。

// 注：原代码使用了 <IconFish /> 但 arco-design/web-vue 并不导出该组件，
// 容易在生产构建时出现 Vue 解析告警。这里改用 FontAwesome 的 fa-fish。

// 另外，全局 FontAwesome 样式在 main.ts 里已经统一引入，
// 这里不再重复 import。

const router = useRouter()
const route = useRoute()

const currentRoute = computed(() => route.name as string)
const isDashboard = computed(() => route.path === '/dashboard')
const isAdmin = computed(() => route.path.startsWith('/admin'))
const isAdministratorRole = administratorRole

function leaveAdministratorMode(): void {
  setAdministrator(false)
  void router.replace('/')
}

function goToDashboard() {
  router.push('/')
}

function goToAdmin() {
  router.push('/admin/data-report')
}

function goToPage(page: string) {
  const target = `/admin/${page}`
  if (page === 'system-ops' || page === 'system-settings') {
    if (isAdministrator()) {
      router.push(target)
      return
    }
    requestTechnicalDebugAccess(target)
    return
  }
  router.push(target)
}
</script>

<style lang="scss">
.logo-icon {
  font-size: 24px;
  color: #1677ff;
}
</style>

<style lang="scss" scoped>
.admin-layout {
  width: 100%;
  height: 100vh;
  min-height: 0;
  background: #f7f8fa;
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.admin-header {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 60px;
  background: #fff;
  border-bottom: 1px solid #e5e6eb;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 24px;
  box-sizing: border-box;
  z-index: 100;
  box-shadow: 0 2px 8px rgb(0, 0, 0, 6%);
  .header-brand {
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 10px;
    color: #1677ff;
    .header-brand-name {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 18px;
      font-weight: 700;
    }
  }
  .header-right {
    display: flex;
    gap: 16px;
    .nav-btn {
      padding: 8px 20px;
      border-radius: 20px;
      border: 1px solid rgb(22, 119, 255, 30%);
      background: rgb(22, 119, 255, 8%);
      color: #4e5969;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 14px;
      transition: all 0.3s ease;
      &:hover {
        background: rgb(22, 119, 255, 15%);
        color: #1677ff;
      }
      &.active {
        background: linear-gradient(to right, #1677ff, #00a2ef);
        border-color: #1677ff;
        color: #fff;
        box-shadow: 0 0 4px #00a2ef;
      }
    }
  }
}
.admin-sidebar {
  position: fixed;
  top: 60px;
  left: 0;
  width: 200px;
  height: calc(100% - 60px);
  background: #fff;
  border-right: 1px solid #e5e6eb;
  z-index: 101;
  box-shadow: 2px 0 8px rgb(0, 0, 0, 4%);
  .sidebar-menu {
    padding: 12px 0;
    .menu-group {
      .menu-group-title {
        padding: 8px 20px;
        font-size: 14px;
        color: #86909c;
        font-weight: 500;
      }
      .menu-item {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 10px 20px;
        color: #1d2129;
        cursor: pointer;
        transition: all 0.2s ease;
         font-size: 16px;
        &:hover {
          background: #e6f7ff;
          color: #1677ff;
        }
        &.active {
          background: #e6f7ff;
          color: #1677ff;
          border-left: 3px solid #1677ff;
          padding-left: 17px;
        }
        .arco-icon {
          font-size: 18px;
        }
      }
    }
  }
}
.admin-content {
  flex: 1 1 auto;
  margin-left: 200px;
  padding-top: 60px;
  box-sizing: border-box;
  background: #f7f8fa;
  width: calc(100% - 200px);

  /* 不要使用 height:100vh + overflow:hidden，否则子页面的滚动与右侧目录 sticky 都会失效。 */
  min-height: calc(100vh - 60px);
  overflow: auto;
  display: block;
}
</style>
