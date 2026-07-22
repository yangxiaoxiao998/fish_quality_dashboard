<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  reportId?: number
}>()

const pdfUrl = computed(() => {
  if (!props.reportId) return ''
  return `/api/reports/history/${props.reportId}/preview`
})
</script>

<template>
  <div class="pdf-viewer-page">
    <h2>日报 PDF 预览</h2>

    <iframe
      v-if="pdfUrl"
      class="pdf-frame"
      :src="pdfUrl"
    />

    <a-empty v-else description="未选择日报" />
  </div>
</template>

<style scoped>
.pdf-viewer-page {
  height: 100%;
  padding: 16px;
  background: #fff;
  border-radius: 12px;
}
.pdf-frame {
  width: 100%;
  height: calc(100vh - 220px);
  border: none;
}
</style>
