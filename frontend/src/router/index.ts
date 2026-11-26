import { createRouter, createWebHistory } from 'vue-router'
import Dashboard from '@/views/Dashboard.vue'
import ScreenHome from '@/views/ScreenHome.vue'
import VideoDetectionReport from '@/views/VideoDetectionReport.vue'
import AdminLayout from '@/layout/AdminLayout.vue'
import {
  isAdministrator,
  requestTechnicalDebugAccess,
} from '@/utils/technicalDebugAuth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      // 开屏首页：默认入口
      path: '/',
      name: 'ScreenHome',
      component: ScreenHome
    },
    {
      // 原 Dashboard 保留路由
      path: '/dashboard',
      name: 'Dashboard',
      component: Dashboard
    },
    {
      // 兼容入口：旧链接 / 书签重定向到 /dashboard
      path: '/legacy-dashboard',
      redirect: '/dashboard'
    },
    {
      // 视频检测报告大屏（单中间列铺满，独立于 AdminLayout）
      path: '/video-report/:videoId?',
      name: 'VideoDetectionReport',
      component: VideoDetectionReport,
      meta: { title: '视频检测报告' }
    },
    {
      path: '/admin',
      component: AdminLayout,
      children: [
        {
          path: '',
          redirect: '/admin/data-report'
        },
        {
          path: 'data-report',
          name: 'AdminDataReport',
          component: () => import('@/views/admin/DataReport.vue'),
          meta: { title: '数据报表' }
        },
        {
          path: 'report-overview',
          name: 'AdminReportOverview',
          component: () => import('@/views/admin/ReportOverview.vue'),
          meta: { title: '报表总览' },
        },
        {
          // 视频单报告：旧路由 ?video_id=xxx 自动跳到鱼品质检测报表
          path: 'data-report/video/:videoId',
          redirect: (to) => ({
            path: '/admin/data-report',
            query: {
              type: 'daily-videos',
              video_id: String(to.params.videoId || ''),
            },
          }),
        },
        {
          // legacy 子页面路由，全部统一收敛到 DataReport workspace
          path: 'data-report/fish-quality',
          redirect: '/admin/data-report'
        },
        {
          path: 'data-report/history',
          redirect: '/admin/data-report'
        },
        {
          // 视频历史：旧入口重定向到 /admin/data-report
          path: 'video-history',
          redirect: '/admin/data-report',
        },
        {
          path: 'system-ops',
          component: () => import('@/views/admin/system-ops/SystemOpsHome.vue'),
          meta: { title: '系统运维' }
        },
        {
          // legacy 子页面路由，全部统一收敛到 SystemOpsHome workspace
          path: 'system-ops/platform-usage',
          redirect: '/admin/system-ops'
        },
        {
          path: 'system-ops/operation-log',
          redirect: '/admin/system-ops'
        },
        {
          path: 'system-settings',
          name: 'AdminSystemSettings',
          component: () => import('@/views/admin/SystemSettings.vue'),
          meta: {
            title: '系统设置'
          }
        },
        // legacy 重定向（保留旧链接 / 外链可用）
        {
          path: 'data-report/daily',
          redirect: '/admin/data-report/fish-quality?type=daily'
        },
        {
          path: 'data-report/daily-videos',
          redirect: '/admin/data-report/fish-quality?type=daily-videos'
        },
        {
          path: 'data-report/range',
          redirect: '/admin/data-report/fish-quality?type=range'
        },
        {
          path: 'data-search',
          redirect: '/admin/data-report'
        },
        {
          path: 'data-report/platform-usage',
          redirect: '/admin/system-ops/platform-usage'
        },
        {
          path: 'data-report/operation-log',
          redirect: '/admin/system-ops/operation-log'
        },
        {
          path: 'original-images',
          redirect: '/admin/data-report'
        },
        {
          path: 'corrected-images',
          redirect: '/admin/data-report'
        }
      ]
    }
  ]
})

router.beforeEach((to, from, next) => {
  if (
    (to.path === '/admin/system-ops' || to.path === '/admin/system-settings') &&
    !isAdministrator()
  ) {
    requestTechnicalDebugAccess(to.fullPath)
    next(false)
    return
  }
  if (from.path === '/' && to.path.startsWith('/admin')) {
    // Keep video state when going to admin pages
  } else if (from.path.startsWith('/admin') && to.path === '/') {
    // Keep video state when going back to dashboard
  }
  next()
})

export default router
