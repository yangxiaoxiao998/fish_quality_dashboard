// frontend/src/utils/useFixedScreen.ts
//
// 给数据大屏（Dashboard.vue）提供 1920×1080 固定比例缩放：
//   - 浏览器窗口 resize 不影响内部布局
//   - 80% / 90% / 100% / 110% 浏览器缩放下，大屏内部相对位置不乱
//   - 不影响 /admin 页面（admin 仍正常滚动 + 流式布局）
//
// 用法：
//   <script setup lang="ts">
//   import { useFixedScreen } from '@/utils/useFixedScreen'
//   const { fixedScreenStyle, updateFixedScreen, triggerResize } = useFixedScreen(1920, 1080)
//   </script>
//
//   <template>
//     <div class="dashboard-viewport">
//       <div class="dashboard-fixed" :style="fixedScreenStyle">
//         ... 原始大屏内容（按 1920×1080 设计）
//       </div>
//     </div>
//   </template>
//
//   <style scoped>
//   .dashboard-viewport {
//     width: 100vw; height: 100vh; overflow: hidden;
//     position: relative; background: #000;
//   }
//   .dashboard-fixed {
//     position: absolute;
//     width: 1920px; height: 1080px;
//     overflow: hidden;
//   }
//   </style>

import { onMounted, onUnmounted, reactive, type CSSProperties } from 'vue'

export function useFixedScreen(width: number = 1920, height: number = 1080) {
  // 初始占位；onMounted 后会立刻被 update() 覆盖
  const fixedScreenStyle = reactive<CSSProperties>({
    width: `${width}px`,
    height: `${height}px`,
    transform: 'scale(1)',
    transformOrigin: 'top left',
    position: 'absolute',
    left: '0px',
    top: '0px',
  })

  function update() {
    if (typeof window === 'undefined') return
    const winW = window.innerWidth
    const winH = window.innerHeight
    if (winW <= 0 || winH <= 0) return
    const scale = Math.min(winW / width, winH / height)
    const left = (winW - width * scale) / 2
    const top = (winH - height * scale) / 2
    fixedScreenStyle.width = `${width}px`
    fixedScreenStyle.height = `${height}px`
    fixedScreenStyle.transform = `scale(${scale})`
    fixedScreenStyle.left = `${Math.max(0, left)}px`
    fixedScreenStyle.top = `${Math.max(0, top)}px`
  }

  function triggerResize() {
    update()
    // 给 ECharts 等异步布局一个机会 resize
    setTimeout(() => {
      window.dispatchEvent(new Event('resize'))
    }, 50)
  }

  onMounted(() => {
    update()
    window.addEventListener('resize', update)
    // 初始多触发几次 resize，让图表组件拿到正确尺寸
    setTimeout(update, 100)
    setTimeout(update, 400)
  })

  onUnmounted(() => {
    window.removeEventListener('resize', update)
  })

  return { fixedScreenStyle, updateFixedScreen: update, triggerResize }
}
