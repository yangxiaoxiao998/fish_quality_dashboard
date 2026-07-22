<script setup lang="ts">
defineProps<{
  title: string
  icon: string
}>()
</script>

<template>
  <button class="report-type-card">
    <div class="icon-box">
      <span class="icon-text">{{ icon }}</span>
    </div>
    <div class="card-title">{{ title }}</div>
  </button>
</template>

<style scoped>
.report-type-card {
  width: 168px;
  height: 148px;
  border: 1px solid #d9e2f3;
  border-radius: 14px;
  background: #fff;
  cursor: pointer;
  box-shadow: 0 6px 18px rgb(27 78 155 / 8%);
  transition: all 0.2s ease;
}
.report-type-card:hover {
  transform: translateY(-3px);
  border-color: #1677ff;
  box-shadow: 0 10px 24px rgb(22 119 255 / 16%);
}
.icon-box {
  width: 62px;
  height: 62px;
  margin: 22px auto 14px;
  border-radius: 14px;
  background: linear-gradient(135deg, #e8f2ff, #f4f8ff);
  display: flex;
  align-items: center;
  justify-content: center;
}
.icon-text {
  font-size: 34px;
  line-height: 1;
}
.card-title {
  font-size: 15px;
  color: #1f2d3d;
  font-weight: 600;
  text-align: center;
  white-space: nowrap;
}
</style>
