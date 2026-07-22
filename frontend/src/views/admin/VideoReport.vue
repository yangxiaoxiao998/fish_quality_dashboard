<template>
  <div class="video-report-page-standalone">
    <EmbeddedVideoReport
      v-if="videoId"
      :video-id="videoId"
    />
    <a-empty
      v-else
      description="未提供 video_id"
    />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import EmbeddedVideoReport from '@/components/report/EmbeddedVideoReport.vue'

const route = useRoute()
const videoId = computed<string>(() => {
  const q = String(route.query.video_id || '').trim()
  if (q) return q
  const p = String(route.params.videoId || '').trim()
  return p
})
</script>

<style scoped>
.video-report-page-standalone {
  background: #f5f7fb;
  padding: 16px;
  min-height: 100%;
  box-sizing: border-box;
}
</style>
