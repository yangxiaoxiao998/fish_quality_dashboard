<script setup lang="ts">
defineProps<{
  title: string
  value: string | number
  unit?: string
}>()
</script>

<template>
  <div class="report-metric-card">
    <div class="metric-title">{{ title }}</div>
    <div class="metric-value">
      {{ value }}
      <span v-if="unit" class="metric-unit">{{ unit }}</span>
    </div>
  </div>
</template>

<style scoped>
.report-metric-card {
  padding: 16px;
  border-radius: 12px;
  background: #fff;
  border: 1px solid #edf1f7;
}
.metric-title {
  font-size: 13px;
  color: #6b7280;
}
.metric-value {
  margin-top: 8px;
  font-size: 24px;
  font-weight: 700;
  color: #1f2937;
}
.metric-unit {
  font-size: 13px;
  margin-left: 4px;
  color: #6b7280;
}
</style>
