<template>
  <section class="report-section-card">
    <h2 :id="id" class="report-section-title">
      {{ title }}
    </h2>
    <p v-if="subtitle" class="report-section-subtitle">
      {{ subtitle }}
    </p>
    <slot />
  </section>
</template>

<script setup lang="ts">
// 板块 ID + 标题 prop 可选保留 index，但本轮不在标题中显示。
defineProps<{
  id: string
  title: string
  subtitle?: string
  index?: string
}>()
</script>

<style scoped>
.report-section-card {
  box-sizing: border-box;
  width: 100%;
  padding: 16px;
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgb(0 0 0 / 6%);
}
.report-section-title {
  margin: 0 0 10px;
  font-size: 16px;
  font-weight: 600;
  line-height: 1.5;
  color: #1d2129;
  scroll-margin-top: 88px;
}
.report-section-subtitle {
  margin: 0 0 14px;
  font-size: 13px;
  line-height: 1.5;
  color: #86909c;
}
</style>
