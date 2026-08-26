<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { http, toErrorMessage } from '@/api/http'

const props = defineProps<{ reportId: number }>()
const loading = ref(true)
const errorMessage = ref('')
const pdfUrl = ref('')
const version = ref(Date.now())
const reportName = ref('')
const reportType = ref('')
const videoId = ref('')
const refreshLabel = ref('刷新 PDF')

const versionedPdfUrl = computed(() => {
  if (!pdfUrl.value) return ''
  return `${pdfUrl.value}?_v=${version.value}`
})

async function loadPdf(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await http.get(`/api/reports/history/${props.reportId}`)
    if (!response.data?.file_path && !response.data?.file_name) {
      throw new Error('当前报表暂未生成 PDF')
    }
    reportName.value = String(response.data?.report_name || '')
    reportType.value = String(response.data?.report_type || '')
    videoId.value = String(response.data?.video_id || '')
    pdfUrl.value = `/api/reports/history/${props.reportId}/preview`
  } catch (error) {
    errorMessage.value = toErrorMessage(error) || '加载 PDF 失败'
  } finally {
    loading.value = false
  }
}

async function refresh(): Promise<void> {
  version.value = Date.now()
  loading.value = true
  try {
    if (reportType.value === 'daily_fish_quality') {
      const detail = await http.get(`/api/reports/history/${props.reportId}`)
      await http.post('/api/reports/fish-quality/daily-pdf/generate', { report_date: detail.data.report_date }, { timeout: 180000 })
    } else if (reportType.value === 'video_fish_quality' && videoId.value) {
      await http.post('/api/reports/fish-quality/video-pdf/generate', { video_id: videoId.value }, { timeout: 180000 })
    } else if (reportType.value === 'custom_video_fish_quality' && reportName.value) {
      await http.patch(`/api/reports/history/${props.reportId}/name`, { report_name: reportName.value })
    }
    await loadPdf()
  } catch (error) {
    errorMessage.value = toErrorMessage(error) || '重新生成 PDF 失败'
    loading.value = false
  }
}

onMounted(() => { void loadPdf() })
</script>

<template>
  <div class="report-pdf-tab">
    <div class="report-pdf-tab-header">
      <div>
        <h2>{{ reportName || '报表 PDF 预览' }}</h2>
      </div>
      <a-button :loading="loading" @click="refresh">{{ refreshLabel }}</a-button>
    </div>
    <div class="report-pdf-tab-body">
      <a-spin v-if="loading" tip="正在加载 PDF 报表..." />
      <a-alert v-else-if="errorMessage" type="error">{{ errorMessage }}</a-alert>
      <iframe v-else-if="pdfUrl" :src="versionedPdfUrl" title="报表 PDF 预览" />
      <a-empty v-else description="当前报表暂未生成 PDF" />
    </div>
  </div>
</template>

<style scoped>
.report-pdf-tab { display: flex; flex-direction: column; width: 100%; height: 100%; min-height: 0; gap: 16px; padding: 16px; box-sizing: border-box; background: #f7f8fa; }
.report-pdf-tab-header { display: flex; flex: 0 0 auto; align-items: center; justify-content: space-between; padding: 14px 18px; border: 1px solid #e5e6eb; border-radius: 8px; background: #fff; }
.report-pdf-tab-header h2 { margin: 0 0 4px; color: #1d2129; font-size: 16px; }
.report-pdf-tab-header div div { color: #4e5969; font-size: 12px; }
.report-pdf-tab-body { display: flex; flex: 1 1 auto; align-items: stretch; justify-content: stretch; min-height: 0; overflow: hidden; border: 1px solid #e5e6eb; border-radius: 8px; background: #fff; }
.report-pdf-tab-body iframe { width: 100%; height: 100%; min-height: 720px; border: 0; }
.report-pdf-tab-body > :deep(.arco-spin), .report-pdf-tab-body > :deep(.arco-alert), .report-pdf-tab-body > :deep(.arco-empty) { margin: auto; }
</style>
