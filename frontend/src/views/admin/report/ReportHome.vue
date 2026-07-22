<script setup lang="ts">
// 数据报表首页：四个一级入口，全部走 router.push
// 鱼品质检测报表是一个独立入口，进入子页后才有三个分析视角。
// 其它三个入口（历史日报 / 平台使用分析 / 操作日志）也独立路由。

import { useRouter } from 'vue-router'
import { logUserAction } from '@/utils/operationLogger'

const router = useRouter()

interface CardItem {
  title: string
  icon: string
  desc: string
  path: string
  tone: 'primary' | 'success' | 'warning' | 'info' | 'calibration'
}

const cards: CardItem[] = [
  {
    title: '鱼品质检测报表',
    icon: '📊',
    desc: '查看当日数据、当日视频总览与日期范围统计',
    path: '/admin/data-report/fish-quality',
    tone: 'primary',
  },
  {
    title: '历史日报',
    icon: '📄',
    desc: '查看已生成的日报列表与 PDF 预览',
    path: '/admin/data-report/history',
    tone: 'info',
  },
]  

function onClickCard(c: CardItem) {
  if (!c.path) return
  logUserAction({
    module: '数据报表',
    action: 'open_report_entry',
    targetType: 'page',
    targetId: c.path,
    message: `打开${c.title}`,
  })
  router.push(c.path)
}
</script>

<template>
  <div class="report-home">
    <div class="home-header">
      <h2>数据报表</h2>
      <p>请选择需要查看的报表类型。点击卡片进入对应报表子页面。</p>
    </div>

    <div class="report-card-grid">
      <button
        v-for="card in cards"
        :key="card.title"
        :class="['report-type-card', `tone-${card.tone}`]"
        @click="onClickCard(card)"
      >
        <span class="card-icon">{{ card.icon }}</span>
        <span class="card-title">{{ card.title }}</span>
        <span class="card-desc">{{ card.desc }}</span>
        <span class="card-arrow">›</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.report-home {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 24px;
  box-sizing: border-box;
  background: #f5f7fb;
  min-height: 100%;
}
.home-header {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.home-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  color: #1d2129;
}
.home-header p {
  margin: 0;
  color: #86909c;
  font-size: 13px;
}
.report-card-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.report-type-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: flex-start;
  text-align: left;
  padding: 20px 56px 20px 20px;
  border: 1px solid #e5e6eb;
  border-radius: 14px;
  background: #fff;
  color: #1d2129;
  cursor: pointer;
  font: inherit;
  box-shadow: 0 2px 8px rgb(0 0 0 / 4%);
  transition: all 0.2s ease;
  min-height: 132px;
}
.report-type-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 20px rgb(27 78 155 / 14%);
  border-color: #1677ff;
}
.report-type-card.tone-primary:hover {
  border-color: #1677ff;
}
.report-type-card.tone-success:hover {
  border-color: #00b42a;
}
.report-type-card.tone-warning:hover {
  border-color: #ff7d00;
}
.report-type-card.tone-info:hover {
  border-color: #722ed1;
}
.card-icon {
  font-size: 28px;
  line-height: 1;
}
.card-title {
  font-size: 16px;
  font-weight: 700;
}
.card-desc {
  font-size: 12px;
  color: #4e5969;
  line-height: 1.5;
}
.card-arrow {
  position: absolute;
  right: 18px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 22px;
  color: #c9cdd4;
  font-weight: 400;
}
.report-type-card:hover .card-arrow {
  color: #1677ff;
}

@media (width <= 768px) {
  .report-card-grid {
    grid-template-columns: 1fr;
  }
}
</style>
