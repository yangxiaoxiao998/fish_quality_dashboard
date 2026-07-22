<script setup lang="ts">
const emit = defineEmits<{
  (e: 'refresh'): void
}>()
</script>

<template>
  <div class="report-filter-bar">
    <slot />
    <a-button type="primary" @click="emit('refresh')">
      查询
    </a-button>
  </div>
</template>

<style scoped>
.report-filter-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  background: #fff;
  border-radius: 12px;
}
</style>
