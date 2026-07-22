<template>
  <div class="video-history-page">
    <!-- 面包屑 -->
    <div class="page-breadcrumb">
      <a-breadcrumb>
        <a-breadcrumb-item>数据管理</a-breadcrumb-item>
        <a-breadcrumb-item>视频任务管理</a-breadcrumb-item>
      </a-breadcrumb>
    </div>

    <div class="page-header">
      <h2>视频任务管理</h2>
      <p class="page-subtitle">
        以 <code>video_task</code> 为单位管理算法结果视频任务。
        点击"跳转大屏"会以 <code>?mode=result&video_id=&lt;video_id&gt;</code> 进入大屏结果模式。
      </p>
    </div>

    <!-- 筛选区 -->
    <div class="filter-section">
      <a-row :gutter="16">
        <a-col :span="6">
          <div class="filter-item">
            <label>video_id</label>
            <a-input
              v-model="filters.videoId"
              placeholder="支持模糊匹配"
              allow-clear
              @press-enter="handleSearch"
            />
          </div>
        </a-col>
        <a-col :span="6">
          <div class="filter-item">
            <label>状态</label>
            <a-select v-model="filters.status" placeholder="不限" allow-clear>
              <a-option value="queued">排队</a-option>
              <a-option value="processing">处理中</a-option>
              <a-option value="done">已完成</a-option>
              <a-option value="failed">失败</a-option>
              <a-option value="interrupted">中断</a-option>
              <a-option value="no_fish_detected">未识别到鱼</a-option>
            </a-select>
          </div>
        </a-col>
        <a-col :span="6">
          <div class="filter-item">
            <label>检测时间</label>
            <a-range-picker v-model="filters.dateRange" show-time style="width: 100%" />
          </div>
        </a-col>
        <a-col :span="6">
          <div class="filter-item filter-actions">
            <a-button type="primary" @click="handleSearch">查询</a-button>
            <a-button @click="handleReset">重置</a-button>
          </div>
        </a-col>
      </a-row>
    </div>

    <div class="action-bar">
      <span class="result-count">共 {{ total }} 条视频任务</span>
      <a-space>
        <a-button
          type="primary"
          :loading="batchImporting"
          @click="handleBatchImport"
        >
          导入 data/results 全部结果
        </a-button>
      </a-space>
    </div>

    <a-alert
      v-if="loadError"
      type="error"
      :show-icon="true"
      style="margin-bottom: 12px"
    >
      <template #title>加载失败</template>
      {{ loadError }}
    </a-alert>

    <!-- 表格 -->
    <div class="table-container">
      <a-table
        :columns="columns"
        :data="tableData"
        :pagination="false"
        :loading="loading"
        :row-key="'video_id'"
        stripe
        hoverable
      >
        <template #columns>
          <a-table-column title="video_id" data-index="video_id" :width="240">
            <template #cell="{ record }">
              <a-tooltip :content="record.video_id">
                <a-link @click="openVideoPreview(record)">
                  {{ record.video_id }}
                </a-link>
              </a-tooltip>
            </template>
          </a-table-column>
          <a-table-column title="检测时间" :width="170">
            <template #cell="{ record }">{{ formatChinaDateTime(record.created_at) }}</template>
          </a-table-column>
          <a-table-column title="状态" data-index="status" :width="120">
            <template #cell="{ record }">
              <a-tag :color="getStatusColor(record.status)">
                {{ getStatusText(record.status) }}
              </a-tag>
            </template>
          </a-table-column>
          <a-table-column title="总鱼数" :width="80">
            <template #cell="{ record }">{{ record.total_fish ?? 0 }}</template>
          </a-table-column>
          <a-table-column title="完整" :width="70">
            <template #cell="{ record }">{{ record.complete_fish ?? 0 }}</template>
          </a-table-column>
          <a-table-column title="不完整" :width="80">
            <template #cell="{ record }">{{ record.incomplete_fish ?? 0 }}</template>
          </a-table-column>
          <a-table-column title="失败" :width="70">
            <template #cell="{ record }">{{ record.failed_fish ?? 0 }}</template>
          </a-table-column>
          <a-table-column title="破损" :width="70">
            <template #cell="{ record }">{{ record.damaged_fish ?? 0 }}</template>
          </a-table-column>
          <a-table-column title="平均破损率" :width="100">
            <template #cell="{ record }">
              {{ formatRatioPct(record.avg_surface_damage_ratio) }}
            </template>
          </a-table-column>
          <a-table-column title="操作" :width="240" fixed="right">
            <template #cell="{ record }">
              <a-space>
                <a-button
                  type="text"
                  size="small"
                  @click="handleViewFishes(record)"
                >
                  查看报表
                </a-button>
                <a-button
                  type="primary"
                  size="small"
                  :disabled="record.status !== 'done'"
                  @click="handleJumpToDashboard(record)"
                >
                  跳转大屏
                </a-button>
                <a-button
                  type="text"
                  size="small"
                  :loading="reimportingId === record.video_id"
                  @click="handleReimport(record)"
                >
                  重新导入
                </a-button>
              </a-space>
            </template>
          </a-table-column>
        </template>
      </a-table>
    </div>

    <!-- 分页 -->
    <div class="pagination-wrap">
      <a-pagination
        v-model:current="currentPage"
        v-model:page-size="pageSize"
        :total="total"
        :page-size-options="[10, 20, 50, 100]"
        show-total
        show-page-size
        @change="handlePageChange"
        @page-size-change="handlePageSizeChange"
      />
    </div>

    <!-- 视频预览 Modal：固定大小 + 内部左右切换 -->
    <a-modal
      v-model:visible="videoPreviewVisible"
      :footer="false"
      :width="980"
      unmount-on-close
      modal-class="video-preview-fixed-modal"
      @cancel="closeVideoPreview"
    >
      <div class="fixed-video-preview-card">
        <div class="fixed-video-preview-head">
          <div class="fixed-video-preview-head-info">
            <div class="fixed-video-preview-title">源视频预览</div>
            <div
              class="fixed-video-preview-id"
              :title="currentPreviewVideo?.video_id || ''"
            >
              {{ currentPreviewVideo?.video_id || '--' }}
            </div>
          </div>
          <a-button @click="closeVideoPreview">关闭</a-button>
        </div>

        <div class="fixed-video-stage">
          <button
            v-if="tableData.length > 1"
            class="fixed-video-nav fixed-video-nav-left"
            type="button"
            aria-label="上一条视频"
            @click.stop="showPrevVideo"
          >
            &lt;
          </button>

          <video
            v-if="currentPlayableUrl && !videoPreviewError"
            :key="currentPlayableUrl"
            class="fixed-video-player"
            :src="currentPlayableUrl"
            controls
            autoplay
            muted
            playsinline
            preload="metadata"
            @error="handleVideoElementError"
          />
          <a-empty
            v-else-if="!videoPreviewLoading"
            class="fixed-video-empty"
            :description="videoPreviewError || '暂无可播放视频'"
          />

          <div v-if="videoPreviewLoading" class="fixed-video-loading-mask">
            <a-spin />
            <div class="fixed-video-loading-text">正在加载视频...</div>
          </div>

          <button
            v-if="tableData.length > 1"
            class="fixed-video-nav fixed-video-nav-right"
            type="button"
            aria-label="下一条视频"
            @click.stop="showNextVideo"
          >
            &gt;
          </button>
        </div>

        <div class="fixed-video-preview-footer">
          <span>
            {{ currentPreviewVideoIndex + 1 }} / {{ tableData.length }}
          </span>
          <span class="fixed-video-preview-meta-info">
            <a-tag v-if="currentPlaybackCodec" size="small">
              {{ currentPlaybackCodec }}
            </a-tag>
            <span>
              {{
                formatChinaDateTime(currentPreviewVideo?.created_at) || ''
              }}
            </span>
          </span>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { http } from '@/api/http'
import { videoStatusText, videoStatusColor } from '@/utils/status'
import { importAlgorithmResult } from '@/api/algorithm'
import { formatChinaDateTime, formatChinaDate } from '@/utils/time'
import { normalizeReportUrl } from '@/utils/reportPath'
import { logUserAction } from '@/utils/operationLogger'

interface VideoTaskRow {
  video_id: string
  status: string
  source_type?: string
  total_fish?: number
  complete_fish?: number
  incomplete_fish?: number
  failed_fish?: number
  damaged_fish?: number
  avg_surface_damage_ratio?: number | null
  max_surface_damage_ratio?: number | null
  pipeline_result_path?: string | null
  result_dir?: string | null
  error_conclusion_path?: string | null
  grounded_video_path?: string | null
  grounded_video_url?: string | null
  input_video?: string | null
  original_input_video?: string | null
  created_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  failed_stage?: string | null
  error_message?: string | null
}

const router = useRouter()

const filters = reactive({
  videoId: '',
  status: '' as string,
  dateRange: [] as string[],
})

const currentPage = ref(1)
const pageSize = ref(10)
const total = ref(0)
const tableData = ref<VideoTaskRow[]>([])
const loading = ref(false)
const batchImporting = ref(false)
const reimportingId = ref<string>('')
const loadError = ref<string>('')

const columns = [
  { title: 'video_id', dataIndex: 'video_id', width: 240, fixed: 'left' as const },
  { title: '检测时间', dataIndex: 'created_at', width: 170 },
  { title: '状态', dataIndex: 'status', width: 120 },
  { title: '总鱼数', width: 80 },
  { title: '完整', width: 70 },
  { title: '不完整', width: 80 },
  { title: '失败', width: 70 },
  { title: '破损', width: 70 },
  { title: '平均破损率', width: 100 },
  { title: '操作', width: 240, fixed: 'right' as const },
]

function getStatusColor(status: string) {
  return videoStatusColor(status)
}

function getStatusText(status: string) {
  return videoStatusText(status)
}

function formatRatioPct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--'
  return `${(Number(value) * 100).toFixed(2)}%`
}

function copyToClipboard(text: string) {
  if (typeof navigator !== 'undefined' && navigator.clipboard) {
    navigator.clipboard.writeText(text).catch(() => {})
  }
}

async function fetchData() {
  loading.value = true
  try {
    const params: Record<string, any> = {
      page: currentPage.value,
      page_size: pageSize.value,
    }
    if (filters.videoId) params.keyword = filters.videoId
    if (filters.status) params.status = filters.status
    if (filters.dateRange && filters.dateRange.length === 2) {
      params.start_date = filters.dateRange[0]
      params.end_date = filters.dateRange[1]
    }

    const resp = await http.get('/api/videos', { params })
    const data = resp.data
    tableData.value = (data.items || []) as VideoTaskRow[]
    total.value = data.total || 0
    loadError.value = ''
  } catch (err: any) {
    console.error('Failed to fetch videos:', err)
    loadError.value =
      err?.response?.data?.detail ||
      err?.response?.data?.error ||
      err?.message ||
      '加载视频任务失败，请检查后端是否启动（http://127.0.0.1:8000）'
    tableData.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  currentPage.value = 1
  fetchData()
}

function handleReset() {
  filters.videoId = ''
  filters.status = ''
  filters.dateRange = []
  currentPage.value = 1
  fetchData()
}

function handlePageChange(page: number) {
  currentPage.value = page
  fetchData()
}

function handlePageSizeChange(size: number) {
  pageSize.value = size
  currentPage.value = 1
  fetchData()
}

function getVideoDate(record: VideoTaskRow): string {
  const s = String(
    record.created_at ||
      record.started_at ||
      record.finished_at ||
      '',
  )
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10)
  const d = new Date(s)
  if (!Number.isNaN(d.getTime())) return formatChinaDate(d)
  return formatChinaDate(new Date())
}

// ---------- 视频预览 Modal ----------
const videoPreviewVisible = ref(false)
const currentPreviewVideoIndex = ref(0)
const videoPreviewLoading = ref(false)
const videoPreviewError = ref('')
const currentPlayableUrl = ref('')
const currentPlaybackCodec = ref('')

const currentPreviewVideo = computed<VideoTaskRow | null>(() => {
  return tableData.value[currentPreviewVideoIndex.value] || null
})

function handleVideoElementError() {
  if (videoPreviewLoading.value) return
  videoPreviewError.value =
    '浏览器无法播放该视频，可能需要转码为 H.264。'
}

function openVideoPreview(record: VideoTaskRow) {
  const idx = tableData.value.findIndex(
    (item) => String(item.video_id) === String(record.video_id),
  )
  currentPreviewVideoIndex.value = idx >= 0 ? idx : 0
  currentPlayableUrl.value = ''
  videoPreviewError.value = ''
  currentPlaybackCodec.value = ''
  videoPreviewVisible.value = true
  logUserAction({
    module: '视频管理',
    action: 'open_video_preview',
    targetType: 'video',
    targetId: record.video_id,
    message: '打开源视频预览',
  })
  loadPlayableVideo(record.video_id)
}

async function loadPlayableVideo(videoId: string) {
  if (!videoId) return
  videoPreviewLoading.value = true
  videoPreviewError.value = ''
  currentPlayableUrl.value = ''
  currentPlaybackCodec.value = ''
  try {
    const resp = await http.get(
      `/api/videos/${encodeURIComponent(videoId)}/playback`,
    )
    const data = resp.data || {}
    if (!data.ok) {
      throw new Error(data.error_message || '视频预览地址获取失败')
    }
    const playableUrl = String(data.playable_url || data.source_url || '')
    if (!playableUrl) {
      throw new Error('后端未返回 playable_url')
    }
    currentPlayableUrl.value = playableUrl.startsWith('/')
      ? playableUrl
      : normalizeReportUrl(playableUrl)
    currentPlaybackCodec.value = data.codec_name
      ? `${data.codec_name}${data.pix_fmt ? ` · ${data.pix_fmt}` : ''}`
      : ''
  } catch (err: any) {
    const detail = err?.response?.data?.detail || err?.response?.data || {}
    videoPreviewError.value =
      detail.error_message ||
      err?.response?.data?.error_message ||
      err?.message ||
      '视频预览失败'
  } finally {
    videoPreviewLoading.value = false
  }
}

function closeVideoPreview() {
  videoPreviewVisible.value = false
}

function showPrevVideo() {
  const total = tableData.value.length
  if (total <= 1) return
  currentPreviewVideoIndex.value =
    (currentPreviewVideoIndex.value - 1 + total) % total
  const next = tableData.value[currentPreviewVideoIndex.value]
  if (next) loadPlayableVideo(next.video_id)
}

function showNextVideo() {
  const total = tableData.value.length
  if (total <= 1) return
  currentPreviewVideoIndex.value =
    (currentPreviewVideoIndex.value + 1) % total
  const next = tableData.value[currentPreviewVideoIndex.value]
  if (next) loadPlayableVideo(next.video_id)
}

function handleViewFishes(record: VideoTaskRow) {
  logUserAction({
    module: '视频管理',
    action: 'open_video_report',
    targetType: 'video',
    targetId: record.video_id,
    message: '选择视频查看报告',
  })
  router.push({
    path: '/admin/data-report',
    query: {
      type: 'daily-videos',
      video_id: record.video_id,
      date: getVideoDate(record),
    },
  })
}


function handleJumpToDashboard(record: VideoTaskRow) {
  if (!record.video_id) return
  if (record.status !== 'done') {
    Message.warning('只有 status=done 的任务可以跳转到大屏结果模式')
    return
  }
  router.push({
    path: '/',
    query: { mode: 'result', video_id: record.video_id },
  })
}

async function handleReimport(record: VideoTaskRow) {
  reimportingId.value = record.video_id
  try {
    const resp = await importAlgorithmResult(record.video_id)
    if (resp.data?.ok) {
      Message.success(
        `重新导入成功：${resp.data.fish_imported ?? 0} 条鱼 / ${resp.data.fish_total ?? 0}`
      )
      await fetchData()
    } else {
      Message.error(resp.data?.error || '导入失败')
    }
  } catch (err: any) {
    Message.error(err?.response?.data?.error || err?.message || '导入失败')
  } finally {
    reimportingId.value = ''
  }
}

async function handleBatchImport() {
  batchImporting.value = true
  try {
    const resp = await http.post('/api/algorithm/import-results/batch', {
      scan_all: true,
      overwrite: true,
    })
    const data = resp.data || {}
    if (data.ok) {
      Message.success(
        `批量导入完成：${data.success}/${data.total} 成功，${data.failed ?? 0} 失败`
      )
      await fetchData()
    } else {
      Message.error(data.error || '批量导入失败')
    }
  } catch (err: any) {
    Message.error(err?.response?.data?.detail || err?.message || '批量导入失败')
  } finally {
    batchImporting.value = false
  }
}

onMounted(() => {
  fetchData()
  window.addEventListener('focus', fetchData)
})

onBeforeUnmount(() => {
  window.removeEventListener('focus', fetchData)
})
</script>

<style lang="scss" scoped>
.video-history-page {
  padding: 20px;
  box-sizing: border-box;
  background: #f7f8fa;
  .page-breadcrumb {
    margin-bottom: 16px;
  }
  .page-header {
    margin-bottom: 20px;
    h2 {
      margin: 0;
      font-size: 20px;
      font-weight: 600;
      color: #1d2129;
    }
    .page-subtitle {
      margin: 8px 0 0;
      color: #86909c;
      font-size: 12px;
      code {
        background: #fff;
        padding: 1px 4px;
        border-radius: 3px;
        color: #1677ff;
      }
    }
  }
  .filter-section {
    background: #fff;
    padding: 20px;
    border-radius: 8px;
    margin-bottom: 16px;
    box-shadow: 0 2px 8px rgb(0, 0, 0, 6%);
    .filter-item {
      label {
        display: block;
        margin-bottom: 8px;
        font-size: 13px;
        color: #4e5969;
      }
    }
    .filter-actions {
      display: flex;
      gap: 12px;
      align-items: flex-end;
    }
  }
  .action-bar {
    margin-bottom: 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    .result-count {
      color: #4e5969;
      font-size: 13px;
    }
  }
  .table-container {
    background: #fff;
    border-radius: 8px;
    overflow: auto visible;
    box-shadow: 0 2px 8px rgb(0, 0, 0, 6%);
  }
  .pagination-wrap {
    display: flex;
    justify-content: flex-end;
    margin-top: 20px;
  }
}
</style>

<style>
/* VideoHistory video_id 源视频预览弹窗：全局样式（非 scoped）。
   原因：Arco Modal 使用 Teleport 渲染到 body，scoped style + :deep() 对 modal-class
   不稳定（modal-class 可能直接加在 .arco-modal 本身上，也可能在父元素上）。
   这里同时匹配两种情况，并且直接覆盖 Arco 默认 modal 尺寸/内边距。 */
.video-preview-fixed-modal.arco-modal,
.video-preview-fixed-modal .arco-modal {
  width: 980px !important;
  max-width: 90vw !important;
  box-sizing: border-box;
}
.video-preview-fixed-modal .arco-modal-body,
.video-preview-fixed-modal.arco-modal .arco-modal-body {
  height: 760px;
  max-height: 86vh;
  overflow: hidden !important;
  box-sizing: border-box;
  padding: 20px !important;
}
.video-preview-fixed-modal .fixed-video-preview-card {
  width: 100%;
  height: 100%;
  min-height: 0;
  box-sizing: border-box;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  gap: 12px;
  overflow: hidden;
}
.video-preview-fixed-modal .fixed-video-preview-head {
  min-width: 0;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex: 0 0 auto;
}
.video-preview-fixed-modal .fixed-video-preview-head-info {
  min-width: 0;
  flex: 1 1 auto;
}
.video-preview-fixed-modal .fixed-video-preview-title {
  font-size: 16px;
  font-weight: 700;
  color: #1d2129;
}
.video-preview-fixed-modal .fixed-video-preview-id {
  margin-top: 4px;
  max-width: 780px;
  font-size: 12px;
  color: #86909c;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.video-preview-fixed-modal .fixed-video-stage {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
  border-radius: 12px;
  background: #0b1220;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
}
.video-preview-fixed-modal .fixed-video-player {
  width: 100%;
  height: 100%;
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  display: block;
  background: #000;
  flex: 0 0 auto;
}
.video-preview-fixed-modal .fixed-video-empty {
  color: #fff;
}
.video-preview-fixed-modal .fixed-video-loading-mask {
  position: absolute;
  inset: 0;
  z-index: 6;
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: center;
  justify-content: center;
  background: rgb(0 0 0 / 45%);
  color: #fff;
}
.video-preview-fixed-modal .fixed-video-loading-text {
  font-size: 13px;
  color: rgb(255 255 255 / 82%);
}
.video-preview-fixed-modal .fixed-video-nav {
  position: absolute;
  top: 50%;
  z-index: 8;
  transform: translateY(-50%);
  width: 42px;
  height: 72px;
  border: none;
  border-radius: 8px;
  background: rgb(0 0 0 / 38%);
  color: #fff;
  font-size: 34px;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}
.video-preview-fixed-modal .fixed-video-nav:hover {
  background: rgb(0 0 0 / 58%);
}
.video-preview-fixed-modal .fixed-video-nav-left {
  left: 12px;
}
.video-preview-fixed-modal .fixed-video-nav-right {
  right: 12px;
}
.video-preview-fixed-modal .fixed-video-preview-footer {
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: #4e5969;
  font-size: 13px;
}
.video-preview-fixed-modal .fixed-video-preview-meta-info {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (width <= 768px) {
  .video-preview-fixed-modal.arco-modal,
  .video-preview-fixed-modal .arco-modal {
    width: 94vw !important;
    max-width: 94vw !important;
  }
  .video-preview-fixed-modal .arco-modal-body,
  .video-preview-fixed-modal.arco-modal .arco-modal-body {
    height: 70vh;
    min-height: 520px;
    max-height: 88vh;
  }
  .video-preview-fixed-modal .fixed-video-nav {
    width: 36px;
    height: 60px;
    font-size: 28px;
  }
  .video-preview-fixed-modal .fixed-video-nav-left {
    left: 8px;
  }
  .video-preview-fixed-modal .fixed-video-nav-right {
    right: 8px;
  }
}
</style>
