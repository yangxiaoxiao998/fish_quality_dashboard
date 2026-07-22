import { createRouter, createWebHistory } from 'vue-router'
import Dashboard from '@/views/Dashboard.vue'
import AdminLayout from '@/layout/AdminLayout.vue'
// DataSearch 入口已被合并到 /admin/data-report（鱼体检测明细章节），保留文件供 legacy import。
import VideoHistory from '@/views/admin/VideoHistory.vue'
import { clearVideoState } from '@/store/video'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'Dashboard',
      component: Dashboard
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
          component: () => import('@/views/admin/DataReport.vue'),
          meta: { title: '数据报表' }
        },
        {
          // 视频单报告：?video_id=xxx 跳到 5 章节单 video 报告。
          path: 'data-report/video/:videoId',
          name: 'AdminVideoReport',
          component: () => import('@/views/admin/VideoReport.vue'),
          meta: { title: '鱼类检测结果分析报告' }
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
          path: 'video-history',
          name: 'AdminVideoHistory',
          component: () => import('@/views/admin/VideoHistory.vue'),
          meta: {
            title: '视频历史记录'
          }
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
  if (from.path === '/' && to.path.startsWith('/admin')) {
    // Keep video state when going to admin pages
  } else if (from.path.startsWith('/admin') && to.path === '/') {
    // Keep video state when going back to dashboard
  }
  next()
})

export default router
