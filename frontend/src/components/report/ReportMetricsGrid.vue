<template>
  <a-empty
    v-if="rows.length === 0"
    description="暂无统计指标"
  />
  <div v-else class="report-metrics-grid">
    <div v-for="m in rows" :key="m.key" class="metric-card">
      <div class="metric-title">{{ m.title }}</div>
      <div class="metric-value">
        <span>{{ formatNumber(m.value, m.digits) }}</span>
        <span v-if="m.unit" class="metric-unit">{{ m.unit }}</span>
      </div>
      <div v-if="m.subtitle" class="metric-subtitle">{{ m.subtitle }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

export interface MetricItem {
  key: string
  title: string
  value: number | string | null | undefined
  unit?: string
  digits?: number
  subtitle?: string
}

const props = defineProps<{
  rows: MetricItem[]
}>()

const rows = computed(() => props.rows)

function formatNumber(v: any, digits: number = 2): string {
  if (v === null || v === undefined || v === '') return '--'
  const n = Number(v)
  if (!Number.isFinite(n)) return '--'
  return n.toFixed(digits)
}
</script>

<style scoped>
.report-metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
}
.metric-card {
  box-sizing: border-box;
  min-height: 86px;
  padding: 12px 14px;
  text-align: center;
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
}
.metric-title {
  font-size: 12px;
  line-height: 1.5;
  color: #6b7280;
}
.metric-value {
  margin-top: 6px;
  font-size: 22px;
  font-weight: 700;
  line-height: 1.3;
  color: #1f2937;
  font-family: SarasaMonoSC, monospace;
}
.metric-unit {
  margin-left: 4px;
  font-size: 12px;
  font-weight: 400;
  color: #6b7280;
}
.metric-subtitle {
  margin-top: 4px;
  font-size: 12px;
  color: #86909c;
}
</style>
