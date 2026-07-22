<script setup lang="ts">
// 系统设置首页 - 2 个入口按钮（数据备份 / 软件更新）
// 风格与 ReportHome.vue 一致：左上角 toolbar 横排 + 占位说明

const emit = defineEmits<{
  (e: 'open', type: 'visual-calibration' | 'data-backup' | 'software-update'): void
}>()

const cards = [
  { type: 'visual-calibration' as const, title: '视觉矫正', icon: '📐' },
  { type: 'data-backup' as const, title: '数据备份', icon: '💾' },
  { type: 'software-update' as const, title: '软件更新', icon: '🚀' }
]
</script>

<template>
  <div class="system-home">
    <div class="system-action-row">
      <button
        v-for="card in cards"
        :key="card.type"
        class="system-action-btn"
        @click="emit('open', card.type)"
      >
        <span class="icon">{{ card.icon }}</span>
        <span class="text">{{ card.title }}</span>
      </button>
    </div>

    <div class="system-home-placeholder">
      <div class="placeholder-title">请选择需要执行的操作</div>
      <div class="placeholder-desc">点击左上方按钮后，将在当前区域打开新的子标签页。</div>
    </div>
  </div>
</template>

<style scoped>
.system-home {
  height: 100%;
  padding: 16px;
  box-sizing: border-box;
  background: #f5f7fb;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.system-action-row {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 12px;
  flex-wrap: wrap;
}
.system-action-btn {
  height: 44px;
  padding: 0 16px;
  border: 1px solid #d9e2f3;
  border-radius: 12px;
  background: #fff;
  color: #1f2d3d;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  box-shadow: 0 4px 12px rgb(27 78 155 / 8%);
  transition: all 0.2s ease;
  font: inherit;
}
.system-action-btn:hover {
  transform: translateY(-2px);
  border-color: #1677ff;
  color: #1677ff;
  box-shadow: 0 8px 18px rgb(22 119 255 / 14%);
}
.system-action-btn .icon {
  font-size: 20px;
  line-height: 1;
}
.system-action-btn .text {
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
}
.system-home-placeholder {
  margin-top: 4px;
  min-height: 220px;
  border: 1px dashed #d9e2f3;
  border-radius: 14px;
  background: #fff;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #6b7280;
  padding: 32px;
  text-align: center;
}
.placeholder-title {
  font-size: 16px;
  font-weight: 600;
  color: #1f2d3d;
}
.placeholder-desc {
  margin-top: 8px;
  font-size: 13px;
}
</style>