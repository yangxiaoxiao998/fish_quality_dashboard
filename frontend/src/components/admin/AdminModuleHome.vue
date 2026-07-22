<script setup lang="ts">
// 通用后台模块首页（统一方正卡片版式）
// 用于：
//   - 数据报表
//   - 系统运维
//   - 系统设置
//
// 统一每行 3 个入口，超过 3 个自动换到下一行。
// 响应式：
//   - 默认桌面端：每行 3 个
//   - 中屏 (≤1200px)：每行 2 个
//   - 小屏 (≤768px)：每行 1 个
// 通过 CSS 实现，columns 参数仍保留作为可选显式覆盖（不传则按响应式）。

interface HomeCard {
  key: string
  title: string
  desc: string
  icon: string
  tone?: 'primary' | 'success' | 'warning' | 'info' | 'calibration'
}

const props = withDefaults(
  defineProps<{
    title: string
    description?: string
    cards: HomeCard[]
    /** 可选显式覆盖列数；不传则按响应式（默认 3 / 2 / 1） */
    columns?: number
  }>(),
  { columns: 3 },
)

const emit = defineEmits<{ (e: 'select', key: string): void }>()

function onClick(card: HomeCard) {
  emit('select', card.key)
}
</script>

<template>
  <div class="admin-module-home">
    <div class="home-header">
      <h2>{{ title }}</h2>
      <p v-if="description">{{ description }}</p>
    </div>

    <div class="admin-home-card-grid" :data-columns="props.columns">
      <button
        v-for="card in cards"
        :key="card.key"
        class="admin-home-card"
        :class="`tone-${card.tone || 'primary'}`"
        @click="onClick(card)"
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
.admin-module-home {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 22px;
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
.admin-home-card-grid {
  display: grid;
  gap: 16px;

  /* 默认桌面端每行 3 个，超过自动换行 */
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
.admin-home-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 10px;

  /* 内容整体垂直居中，但水平左对齐；
     右侧 64px 留白用于放置 .card-arrow。 */
  align-items: flex-start;
  justify-content: center;
  text-align: left;
  padding: 24px 64px 24px 28px;
  border: 1px solid #e5e6eb;
  border-radius: 14px;
  background: linear-gradient(135deg, #fff 0%, #f4f8ff 100%);
  color: #1d2129;
  cursor: pointer;
  font: inherit;
  box-shadow: 0 8px 24px rgb(32 91 179 / 8%);
  min-height: 180px;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
}
.admin-home-card:hover {
  transform: translateY(-3px);
  border-color: #4080ff;
  box-shadow: 0 12px 32px rgb(32 91 179 / 15%);
}
.admin-home-card .card-icon {
  font-size: 32px;
  line-height: 1;
}
.admin-home-card .card-title {
  font-size: 17px;
  font-weight: 700;
  width: 100%;
}
.admin-home-card .card-desc {
  font-size: 13px;
  color: #4e5969;
  line-height: 1.5;
  width: 100%;

  /* 描述左侧与标题/图标对齐；不再设 max-width 以免换行后仍居中显示。 */
  max-width: 100%;
}
.admin-home-card .card-arrow {
  position: absolute;
  right: 18px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 22px;
  color: #c9cdd4;
  font-weight: 400;
  transition: color 0.18s ease;
}
.admin-home-card:hover .card-arrow {
  color: #1677ff;
}

/* 中屏：每行 2 个 */
@media (width <= 1200px) {
  .admin-home-card-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

/* 小屏：每行 1 个 */
@media (width <= 768px) {
  .admin-home-card-grid {
    grid-template-columns: 1fr;
  }
}
</style>