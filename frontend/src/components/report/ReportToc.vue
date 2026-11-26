<template>
  <div class="report-toc">
    <div class="report-toc-card">
      <div class="toc-title">{{ title || '报告目录' }}</div>
      <ul class="toc-list">
        <li v-for="item in items" :key="item.id">
          <a
            :href="`#${item.id}`"
            class="toc-item"
            :class="{ active: activeId === item.id }"
            @click.prevent="onClick(item.id)"
          >
            <span class="toc-index">{{ item.index }}{{ separator }}</span>
            <span class="toc-text">{{ item.title }}</span>
          </a>
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

export interface ReportTocItem {
  id: string
  index: string
  title: string
}

const props = withDefaults(
  defineProps<{
    items: ReportTocItem[]
    title?: string
    /** 编号与标题之间的分隔符；汇总页默认 ".", 单视频报告传 "、" */
    separator?: string
  }>(),
  { separator: '.' }
)

const route = useRoute()
const router = useRouter()

const activeId = ref<string>(props.items[0]?.id || '')
let observer: IntersectionObserver | null = null

function scrollTo(id: string, updateHash = true) {
  if (typeof window === 'undefined' || typeof document === 'undefined') return
  const el = document.getElementById(id)
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  activeId.value = id
  if (updateHash && route.hash !== `#${id}`) {
    try {
      router.replace({ query: route.query, hash: `#${id}` })
    } catch (_) {
      /* ignore */
    }
  }
}

function onClick(id: string) {
  scrollTo(id, true)
}

function setupObserver() {
  if (typeof window === 'undefined' || !('IntersectionObserver' in window)) return
  if (observer) observer.disconnect()
  observer = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((e) => e.isIntersecting)
        .map((e: IntersectionObserverEntry) => String(e.target.id))
        .filter((id) => id.length > 0)
      if (visible.length > 0) {
        const last = visible[visible.length - 1]
        if (last) activeId.value = last
      }
    },
    { rootMargin: '-30% 0px -50% 0px' }
  )
  for (const it of props.items) {
    const el = document.getElementById(it.id)
    if (el) observer.observe(el)
  }
}

async function refreshObserver() {
  await nextTick()
  setupObserver()
}

onMounted(() => {
  refreshObserver()
  // 兜底：子页面 echarts / 表格等异步 DOM 准备好后再绑一次
  window.setTimeout(refreshObserver, 80)

  if (route.hash) {
    const id = route.hash.replace('#', '')
    window.setTimeout(() => {
      scrollTo(id, false)
    }, 120)
  }
})

onBeforeUnmount(() => {
  if (observer) observer.disconnect()
})

watch(
  () => props.items.map((i) => i.id).join('|'),
  () => {
    refreshObserver()
  }
)
</script>

<style scoped>
/* 朴素后台风格：sticky 在右侧容器内，宽度 100% */
.report-toc {
  position: sticky;
  top: 88px;
  width: 100%;
}
.report-toc-card {
  box-sizing: border-box;
  width: 100%;
  padding: 14px 16px;
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgb(0 0 0 / 6%);
}
.toc-title {
  margin: 0 0 10px;
  font-size: 16px;
  font-weight: 600;
  color: #1d2129;
}
.toc-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.toc-list li {
  margin: 0;
  padding: 0;
}
.toc-item {
  display: block;
  padding: 5px 0;
  font-size: 16px;
  line-height: 1.6;
  color: #4e5969;
  text-decoration: none;
  background: transparent;
  border-left: none;
  font-weight: 400;
  cursor: pointer;
  transition: color 0.15s ease;
}
.toc-item:hover {
  color: #1677ff;
  background: transparent;
}
.toc-item.active {
  color: #4e5969;
  background: transparent;
  border-left: none;
  font-weight: 400;
}
.toc-index {
  margin-right: 6px;
  font-weight: 400;
}
.toc-text {
  font-weight: 400;
}
</style>
