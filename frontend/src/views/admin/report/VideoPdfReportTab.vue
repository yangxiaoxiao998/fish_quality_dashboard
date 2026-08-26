<script setup lang="ts">
// 视频 PDF 报表标签
// - 每个实例独立通过 props.videoId / props.instanceId 加载 PDF
// - 绝不使用 route.query.video_id
// - PDF 顺序：先查 /api/algorithm/videos/{id}/result，回退查 /api/reports/history
// - 顶部仅显示 PDF 视频 + 视频编号 + 刷新按钮
// - 不显示 Word / 不显示 docx
import { computed, onMounted, ref, watch } from 'vue'
import { http, toErrorMessage } from '@/api/http'
import { normalizeReportUrl } from '@/utils/reportPath'

interface Props {
  videoId: string
  instanceId: string
}

const props = defineProps<Props>()

const pdfUrl = ref<string>('')
const loading = ref<boolean>(false)
const errorMessage = ref<string>('')

// 当前标签自己的 cache-busting 版本号
const pdfVersion = ref<number>(Date.now())

const versionedPdfUrl = computed<string>(() => {
  if (!pdfUrl.value) return ''
  const sep = pdfUrl.value.includes('?') ? '&' : '?'
  return `${pdfUrl.value}${sep}_v=${pdfVersion.value}`
})

function appendVersion(url: string): string {
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}_v=${pdfVersion.value}`
}

// 解包多种 API 响应层级（AxiosResponse.data、body.data、body.payload），最终取得标准 payload
function unwrapApiPayload(raw: any): any {
  let cur = raw
  for (let i = 0; i < 4 && cur && typeof cur === 'object'; i += 1) {
    const candidate =
      (cur as any).data !== undefined
        ? (cur as any).data
        : (cur as any).payload !== undefined
          ? (cur as any).payload
          : null
    if (candidate === null || candidate === cur) break
    cur = candidate
  }
  return cur && typeof cur === 'object' ? cur : {}
}

async function loadFromAlgorithmResult(videoId: string): Promise<string | null> {
  try {
    const resp = await http.get(
      `/api/algorithm/videos/${encodeURIComponent(videoId)}/result`,
    )
    // 后端响应结构：{ok: true, data: {video_id, report_pdf_url, report_pdf, ...}}
    // 真实 PDF 候选 URL 优先级（按顺序短路返回第一个非空值）：
    //   1. payload.report_pdf_url    ← 内层标准
    //   2. payload.report_pdf       ← 内层相对路径（同样可规范化为 /static/...）
    //   3. envelope.report_pdf_url  ← 外层（兜底）
    //   4. envelope.report_pdf      ← 外层（兜底）
    //   5. ''                       ← 全部为空
    const envelope: any = (resp && (resp as any).data) || {}
    const payload: any = unwrapApiPayload(envelope) || {}
    const rawCandidate = String(
      payload.report_pdf_url ||
        payload.report_pdf ||
        envelope.report_pdf_url ||
        envelope.report_pdf ||
        '',
    ).trim()
    if (!rawCandidate) return null
    return normalizeReportUrl(rawCandidate)
  } catch {
    return null
  }
}

async function loadFromReportsHistory(videoId: string): Promise<string | null> {
  try {
    const resp = await http.get('/api/reports/history', {
      params: {
        report_type: 'video_fish_quality',
        keyword: videoId,
        page: 1,
        page_size: 20,
      },
    })
    const payload = unwrapApiPayload(resp.data) || {}
    const items: any[] = Array.isArray(payload.items) ? payload.items : []
    for (const it of items) {
      if (
        String(it.video_id || '') === videoId &&
        String(it.status || '') === 'generated'
      ) {
        const rid = it.report_id
        if (rid !== null && rid !== undefined) {
          return appendVersion(`/api/reports/history/${rid}/preview`)
        }
      }
    }
    return null
  } catch {
    return null
  }
}

async function loadPdf(): Promise<void> {
  const videoId = String(props.videoId || '').trim()
  if (!videoId) {
    errorMessage.value = '视频编号为空'
    return
  }
  loading.value = true
  errorMessage.value = ''
  pdfUrl.value = ''
  try {
    // 优先级：
    // 1. /api/algorithm/videos/{id}/result 静态 URL（不走 history preview）
    let url = await loadFromAlgorithmResult(videoId)
    if (!url) {
      // 2. /api/reports/history -> /api/reports/history/{id}/preview
      url = await loadFromReportsHistory(videoId)
    }
    if (url) {
      pdfUrl.value = url
      errorMessage.value = ''
    } else {
      pdfUrl.value = ''
      errorMessage.value = '当前视频暂未生成 PDF 报表'
    }
  } catch (err: unknown) {
    pdfUrl.value = ''
    errorMessage.value = toErrorMessage(err) || '加载 PDF 失败'
  } finally {
    loading.value = false
  }
}

function refreshPdf(): void {
  pdfVersion.value = Date.now()
  void regenerateAndLoadPdf()
}

async function regenerateAndLoadPdf(): Promise<void> {
  const videoId = String(props.videoId || '').trim()
  if (!videoId) return
  loading.value = true
  try {
    await http.post(
      '/api/reports/fish-quality/video-pdf/generate',
      { video_id: videoId },
      { timeout: 180000 },
    )
  } catch (err) {
    errorMessage.value = toErrorMessage(err) || '重新生成 PDF 失败'
  } finally {
    await loadPdf()
  }
}

onMounted(() => {
  void loadPdf()
})

watch(
  () => props.videoId,
  () => {
    pdfVersion.value = Date.now()
    void loadPdf()
  },
)
</script>

<template>
  <div class="video-pdf-tab">
    <div class="pdf-tab-header">
      <div>
        <h2 class="pdf-tab-title">视频 PDF 报表</h2>
        <div class="pdf-tab-meta">视频编号：{{ videoId }}</div>
      </div>
      <a-button :loading="loading" @click="refreshPdf">刷新 PDF</a-button>
    </div>

    <div class="pdf-tab-body">
      <div v-if="loading" class="pdf-tab-state">
        <a-spin tip="正在加载 PDF 报表..." />
      </div>
      <a-alert
        v-else-if="errorMessage"
        type="error"
        class="pdf-tab-state"
        show-icon
      >
        {{ errorMessage }}
      </a-alert>
      <iframe
        v-else-if="pdfUrl"
        class="video-pdf-frame"
        :src="versionedPdfUrl"
        :title="`视频 ${videoId} PDF 报表`"
      />
      <a-empty
        v-else
        class="pdf-tab-state"
        description="当前视频暂未生成 PDF 报表"
      />
    </div>
  </div>
</template>

<style lang="scss" scoped>
.video-pdf-tab {
  width: 100%;
  height: 100%;
  min-height: 0;
  box-sizing: border-box;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  background: #f5f7fb;
}
.pdf-tab-header {
  flex: 0 0 auto;
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  padding: 14px 18px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  .pdf-tab-title {
    margin: 0 0 4px;
    font-size: 16px;
    font-weight: 600;
    color: #1d2129;
  }
  .pdf-tab-meta {
    font-size: 12px;
    color: #4e5969;
    font-family: SarasaMonoSC, Menlo, Monaco, Consolas, monospace;
  }
}
.pdf-tab-body {
  flex: 1 1 auto;
  min-height: 600px;
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  align-items: stretch;
  justify-content: stretch;
}
.pdf-tab-state {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 16px;
  color: #4e5969;
  font-size: 14px;
}
.video-pdf-frame {
  display: block;
  width: 100%;
  height: 100%;
  min-height: 720px;
  border: 0;
  background: #fff;
}
</style>
