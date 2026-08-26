<script setup lang="ts">
// 数据报表 - 视频检测统计列表
// - 日期筛选 + 服务端分页
// - 视频编号点击预览源视频
// - 三个操作：查看 PDF 报表 / 跳转大屏 / 视频明细
// - 跳转大屏：/dashboard?mode=result&video_id=...
// - 视频明细：/admin/data-report?type=daily-videos&video_id=...&date=YYYY-MM-DD
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import dayjsLib from 'dayjs'
import { Message, Modal } from '@arco-design/web-vue'
import { http, toErrorMessage } from '@/api/http'
import { logUserAction } from '@/utils/operationLogger'

interface VideoReportListRow {
  video_id: string
  total_fish: number
  complete_fish: number
  incomplete_fish: number
  damaged_fish: number
  avg_body_length: number | null
  avg_body_length_without_tail: number | null
  avg_body_height: number | null
  avg_body_weight: number | null
  avg_condition_factor: number | null
  avg_surface_damage_ratio: number | null
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  video_duration_seconds?: number | null
  video_duration_error?: string | null
  processing_duration_seconds?: number | null
  status: string
  aquaculture_bin?: string | null
}

const emit = defineEmits<{
  (e: 'open-pdf-report', payload: { videoId: string }): void
  (e: 'open-custom-report', payload: { videoIds: string[] }): void
}>()

const router = useRouter()

// 日期筛选：selectedDate 是当前控件值，appliedDate 是最后真正查询的值
const selectedDate = ref<string>(dayjsLib().format('YYYY-MM-DD'))
const appliedDate = ref<string>(dayjsLib().format('YYYY-MM-DD'))
const dateRange = ref<string[]>([])
const aquacultureBin = ref<string>('')

const tableData = ref<VideoReportListRow[]>([])
const total = ref<number>(0)
const currentPage = ref<number>(1)
const pageSize = ref<number>(10)

const DATA_REPORT_STATE_KEY = 'admin-data-report-video-list-state'

interface DataReportListState {
  dateRange: string[]
  appliedDate: string
  aquacultureBin: string
  currentPage: number
  pageSize: number
}

function saveListState(): void {
  try {
    sessionStorage.setItem(DATA_REPORT_STATE_KEY, JSON.stringify({
      dateRange: dateRange.value,
      appliedDate: appliedDate.value,
      aquacultureBin: aquacultureBin.value,
      currentPage: currentPage.value,
      pageSize: pageSize.value,
    } satisfies DataReportListState))
  } catch {
    // Storage may be unavailable in restricted browser contexts.
  }
}

function restoreListState(): boolean {
  try {
    const raw = sessionStorage.getItem(DATA_REPORT_STATE_KEY)
    if (!raw) return false
    const state = JSON.parse(raw) as Partial<DataReportListState>
    if (Array.isArray(state.dateRange)) dateRange.value = state.dateRange.filter(Boolean)
    if (state.appliedDate) appliedDate.value = state.appliedDate
    if (typeof state.aquacultureBin === 'string') aquacultureBin.value = state.aquacultureBin
    if (Number.isInteger(state.currentPage) && Number(state.currentPage) > 0) {
      currentPage.value = Number(state.currentPage)
    }
    if (Number.isInteger(state.pageSize) && Number(state.pageSize) > 0) {
      pageSize.value = Number(state.pageSize)
    }
    return true
  } catch {
    return false
  }
}

const loading = ref<boolean>(false)
const errorMessage = ref<string>('')
const selectedVideoIds = ref<string[]>([])
const aquacultureBinDrafts = reactive<Record<string, string>>({})
const savingAquacultureBin = reactive<Record<string, boolean>>({})

// 视频预览弹窗
const videoPreviewVisible = ref<boolean>(false)
const videoPreviewLoading = ref<boolean>(false)
const videoPreviewError = ref<string>('')
const currentPlayableUrl = ref<string>('')
const currentPlaybackCodec = ref<string>('')
const currentPreviewVideoIndex = ref<number>(-1)

const totalPages = computed<number>(() => {
  const t = total.value
  const ps = pageSize.value
  if (t <= 0 || ps <= 0) return 1
  return Math.ceil(t / ps)
})

const serialColumnWidth = computed<number>(() => {
  const lastSerial = Math.max(1, total.value)
  return Math.max(52, String(lastSerial).length * 10 + 24)
})

function buildDayRange(dateStr: string): { start_date: string; end_date: string } {
  // 北京时间全天：00:00:00 ~ 23:59:59.999
  const d = dayjsLib(dateStr)
  return {
    start_date: d.startOf('day').format(),
    end_date: d.endOf('day').format(),
  }
}

async function fetchData(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const range = dateRange.value.length
      ? {
          start_date: buildDayRange(dateRange.value[0] || appliedDate.value).start_date,
          end_date: buildDayRange(dateRange.value[1] || dateRange.value[0] || appliedDate.value).end_date,
        }
      : buildDayRange(appliedDate.value)
    const resp = await http.get('/api/videos', {
      params: {
        page: currentPage.value,
        page_size: pageSize.value,
        start_date: range.start_date,
        end_date: range.end_date,
        aquaculture_bin: aquacultureBin.value.trim() || undefined,
      },
    })
    const data: any = resp.data || {}
    tableData.value = Array.isArray(data.items) ? data.items : []
    for (const row of tableData.value) {
      aquacultureBinDrafts[row.video_id] = row.aquaculture_bin || ''
    }
    selectedVideoIds.value = selectedVideoIds.value.filter((id) =>
      tableData.value.some((row) => row.video_id === id),
    )
    total.value = Number(data.total) || 0
  } catch (err: unknown) {
    errorMessage.value = toErrorMessage(err) || '加载视频列表失败'
    tableData.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function isSelected(videoId: string): boolean {
  return selectedVideoIds.value.includes(videoId)
}

function toggleSelected(videoId: string, checked: boolean): void {
  selectedVideoIds.value = checked
    ? [...new Set([...selectedVideoIds.value, videoId])]
    : selectedVideoIds.value.filter((id) => id !== videoId)
}

function toggleSelectAll(checked: boolean): void {
  selectedVideoIds.value = checked ? tableData.value.map((row) => row.video_id) : []
}

async function saveAquacultureBin(record: VideoReportListRow): Promise<void> {
  const videoId = record.video_id
  savingAquacultureBin[videoId] = true
  try {
    const resp = await http.patch(`/api/videos/${encodeURIComponent(videoId)}/aquaculture-bin`, {
      aquaculture_bin: aquacultureBinDrafts[videoId] || '',
    })
    record.aquaculture_bin = resp.data?.aquaculture_bin || null
    aquacultureBinDrafts[videoId] = record.aquaculture_bin || ''
    Message.success('养鱼仓已保存')
  } catch (err) {
    Message.error(toErrorMessage(err) || '养鱼仓保存失败')
  } finally {
    savingAquacultureBin[videoId] = false
  }
}

function saveAquacultureBinOnEnter(record: VideoReportListRow): void {
  void saveAquacultureBin(record)
}

async function deleteVideo(record: VideoReportListRow): Promise<void> {
  Modal.confirm({
    title: '确认永久删除视频',
    content: `将永久删除视频 ${record.video_id}、检测结果、报表及相关文件，删除后不会留下任何数据。确定继续吗？`,
    okText: '永久删除',
    cancelText: '取消',
    okButtonProps: { status: 'danger' },
    onOk: async () => {
      try {
    await http.delete(`/api/videos/${encodeURIComponent(record.video_id)}`)
    Message.success('视频及相关数据已永久删除')
    await fetchData()
      } catch (err) {
    Message.error(toErrorMessage(err) || '删除失败')
      }
    },
  })
}

function confirmBatchDelete(): void {
  const count = selectedVideoIds.value.length
  if (!count) {
    Message.info('请先选择需要删除的视频')
    return
  }
  Modal.confirm({
    title: '确认批量永久删除',
    content: `已选择 ${count} 条视频。此操作会永久删除视频、检测结果、报表及相关文件，删除后不会留下任何数据。确定继续吗？`,
    okText: '永久删除',
    cancelText: '我再想想',
    okButtonProps: { status: 'danger' },
    onOk: async () => {
      try {
    await http.post('/api/videos/batch-delete', { video_ids: selectedVideoIds.value })
    Message.success('选中视频及相关数据已永久删除')
    selectedVideoIds.value = []
    await fetchData()
      } catch (err) {
    Message.error(toErrorMessage(err) || '批量删除失败')
      }
    },
  })
}

async function batchDeleteVideos(): Promise<void> {
  confirmBatchDelete()
}

function toggleBatchDeleteButton(): void {
  if (!selectedVideoIds.value.length) {
    Message.info('请先勾选一条或多条视频')
  } else {
    confirmBatchDelete()
  }
}

function handleQuery(): void {
  // 日期范围和养鱼仓名称都以当前控件值查询。
  currentPage.value = 1
  saveListState()
  void fetchData()
}

function handleAquacultureBinQuery(): void {
  currentPage.value = 1
  saveListState()
  void fetchData()
}

function handleDateRangeChange(value: string[] | undefined): void {
  dateRange.value = Array.isArray(value) ? value : []
  currentPage.value = 1
  saveListState()
  void fetchData()
}

function handlePageChange(p: number): void {
  if (!Number.isInteger(p)) return
  if (p < 1) p = 1
  if (p > totalPages.value) p = totalPages.value
  if (p === currentPage.value) return
  currentPage.value = p
  saveListState()
  fetchData()
}

function handlePageSizeChange(ps: number): void {
  if (!Number.isInteger(ps) || ps <= 0) return
  pageSize.value = ps
  currentPage.value = 1
  saveListState()
  fetchData()
}

function formatFixed(value: number | null | undefined, digits: number): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  return Number(value).toFixed(digits)
}

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  return `${(Number(value) * 100).toFixed(2)}%`
}

function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return ''
  return dayjsLib(dateStr).format('YYYY-MM-DD')
}

function formatDuration(seconds: number | null | undefined): string {
  const n = Number(seconds)
  if (!Number.isFinite(n) || n < 0) return '--'
  const total = Math.round(n)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return h ? `${h}小时${m}分${s}秒` : m ? `${m}分${s}秒` : `${s}秒`
}

function durationTitle(record: VideoReportListRow): string {
  return record.video_duration_error || ''
}

function rowIndex(idx: number): number {
  return (currentPage.value - 1) * pageSize.value + idx + 1
}

function openVideoPreview(record: VideoReportListRow): void {
  const idx = tableData.value.findIndex(
    (it) => String(it.video_id) === String(record.video_id),
  )
  currentPreviewVideoIndex.value = idx >= 0 ? idx : 0
  currentPlayableUrl.value = ''
  videoPreviewError.value = ''
  currentPlaybackCodec.value = ''
  videoPreviewVisible.value = true
  logUserAction({
    module: '数据报表',
    action: 'open_video_preview',
    targetType: 'video',
    targetId: record.video_id,
    message: '打开源视频预览',
  })
  loadPlayableVideo(record.video_id)
}

async function loadPlayableVideo(videoId: string): Promise<void> {
  if (!videoId) return
  videoPreviewLoading.value = true
  videoPreviewError.value = ''
  currentPlayableUrl.value = ''
  currentPlaybackCodec.value = ''
  try {
    const resp = await http.get(
      `/api/videos/${encodeURIComponent(videoId)}/playback`,
    )
    const data: any = resp.data || {}
    if (!data.ok) {
      throw new Error(data.error_message || '视频预览地址获取失败')
    }
    const playableUrl = String(data.playable_url || data.source_url || '')
    if (!playableUrl) {
      throw new Error('后端未返回 playable_url')
    }
    currentPlayableUrl.value = playableUrl.startsWith('/')
      ? playableUrl
      : playableUrl
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

function closeVideoPreview(): void {
  videoPreviewVisible.value = false
  currentPlayableUrl.value = ''
  videoPreviewError.value = ''
  currentPlaybackCodec.value = ''
}

function previewPrev(): void {
  const list = tableData.value
  if (!list.length) return
  const idx = currentPreviewVideoIndex.value
  const next = idx <= 0 ? list.length - 1 : idx - 1
  const target = list[next]
  if (!target) return
  currentPreviewVideoIndex.value = next
  openVideoPreview(target)
}

function previewNext(): void {
  const list = tableData.value
  if (!list.length) return
  const idx = currentPreviewVideoIndex.value
  const next = idx < 0 || idx >= list.length - 1 ? 0 : idx + 1
  const target = list[next]
  if (!target) return
  currentPreviewVideoIndex.value = next
  openVideoPreview(target)
}

function openPdfReport(record: VideoReportListRow): void {
  const videoId = String(record.video_id || '').trim()
  if (!videoId) {
    Message.warning('视频编号为空，无法查看报表')
    return
  }
  if (String(record.status || '').toLowerCase() !== 'done') {
    Message.warning('该任务仍在运行中，请稍后查看报表')
    return
  }
  logUserAction({
    module: '数据报表',
    action: 'view_pdf_report',
    targetType: 'video',
    targetId: videoId,
    message: `查看视频 ${videoId} PDF 报表`,
  })
  emit('open-pdf-report', { videoId })
}

function goDashboard(record: VideoReportListRow): void {
  const videoId = String(record.video_id || '').trim()
  if (String(record.status || '').toLowerCase() !== 'done') {
    Message.warning('该任务仍在运行中，请稍后查看结果大屏')
    return
  }
  logUserAction({
    module: '数据报表',
    action: 'open_dashboard_result',
    targetType: 'video',
    targetId: videoId,
    message: `打开 ${videoId} 结果大屏`,
  })
  router.push({
    path: '/dashboard',
    query: {
      mode: 'result',
      video_id: videoId,
    },
  })
}

function goVideoDetail(record: VideoReportListRow): void {
  const videoId = String(record.video_id || '').trim()
  if (String(record.status || '').toLowerCase() !== 'done') {
    Message.warning('该任务仍在运行中，请稍后查看视频明细')
    return
  }
  // date 来源优先级：created_at / started_at / finished_at / 当前筛选日期
  const created =
    record.created_at || record.started_at || record.finished_at || ''
  const dateStr = created ? formatDate(created) : appliedDate.value
  logUserAction({
    module: '数据报表',
    action: 'open_video_detail',
    targetType: 'video',
    targetId: videoId,
    message: `打开 ${videoId} 视频明细`,
  })
  router.push({
    path: '/admin/data-report',
    query: {
      type: 'daily-videos',
      video_id: videoId,
      date: dateStr,
    },
  })
}

onMounted(() => {
  restoreListState()
  fetchData()
})
</script>

<template>
  <div class="data-report-list-page">
    <div class="data-report-list-header">
      <h2 class="data-report-list-title">数据报表</h2>
      <div class="data-report-list-subtitle">
        查看视频检测结果、鱼品质数据及对应检测报告。
      </div>
    </div>

    <div class="data-report-list-filter">
      <a-form :model="{ selectedDate }" layout="inline">
        <a-form-item label="检测日期">
          <a-range-picker
            v-model="dateRange"
            value-format="YYYY-MM-DD"
            placeholder="选择开始日期和结束日期"
            :style="{ width: '260px' }"
            @change="handleDateRangeChange"
          />
          <a-input
            v-model="aquacultureBin"
            placeholder="输入养鱼仓名称"
            allow-clear
            :style="{ width: '180px', marginLeft: '8px' }"
            @press-enter="handleAquacultureBinQuery"
          />
          <a-date-picker
            v-if="false"
            v-model="selectedDate"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
            :allow-clear="false"
            style="width: 180px"
          />
        </a-form-item>
        <a-form-item>
          <a-button type="primary" @click="handleQuery">查询</a-button>
        </a-form-item>
      </a-form>
      <div class="data-report-list-filter-actions">
        <a-button
          status="danger"
          :disabled="!selectedVideoIds.length"
          @click="toggleBatchDeleteButton"
        >批量删除</a-button>
        <a-button
          type="primary"
          :disabled="!selectedVideoIds.length"
          @click="emit('open-custom-report', { videoIds: [...selectedVideoIds] })"
        >合并报表</a-button>
      </div>
    </div>

    <div class="data-report-list-card">
      <a-spin :loading="loading">
        <a-table
          :data="tableData"
          :pagination="false"
          row-key="video_id"
          :bordered="true"
          :stripe="true"
           :scroll="{ x: 1860 }"
        >
          <template #columns>
            <a-table-column title="选择" :width="58">
              <template #header>
                <a-checkbox
                  :model-value="selectedVideoIds.length > 0 && selectedVideoIds.length === tableData.length"
                  @change="toggleSelectAll(Boolean($event))"
                />
              </template>
              <template #cell="{ record }">
                <a-checkbox
                  :model-value="isSelected(record.video_id)"
                  @change="toggleSelected(record.video_id, Boolean($event))"
                />
              </template>
            </a-table-column>
            <a-table-column title="序号" :width="serialColumnWidth">
              <template #cell="{ rowIndex: currentRowIndex }">
                {{ rowIndex(currentRowIndex) }}
              </template>
            </a-table-column>
            <a-table-column title="养鱼仓" :width="180">
              <template #cell="{ record }">
                <a-input
                  v-model="aquacultureBinDrafts[record.video_id]"
                  size="small"
                  placeholder="请输入名称后回车"
                  @press-enter="saveAquacultureBinOnEnter(record)"
                />
              </template>
            </a-table-column>
            <a-table-column
              title="视频编号"
              data-index="video_id"
              :width="145"
            >
              <template #cell="{ record }">
                <a-link @click="openVideoPreview(record)">
                  {{ record.video_id }}
                </a-link>
              </template>
            </a-table-column>
            <a-table-column title="视频时长" :width="120">
              <template #cell="{ record }">
                <span :title="durationTitle(record)">
                  {{ formatDuration(record.video_duration_seconds) }}
                </span>
              </template>
            </a-table-column>
            <a-table-column
              title="总鱼数"
              data-index="total_fish"
              :width="80"
            />
            <a-table-column
              title="完整鱼数"
              data-index="complete_fish"
              :width="100"
            />
            <a-table-column
              title="不完整鱼数"
              data-index="incomplete_fish"
              :width="110"
            />
            <!-- <a-table-column
              title="破损鱼数"
              data-index="damaged_fish"
              :width="100"
            /> -->

            <a-table-column title="平均全长(cm)" :width="120">
              <template #cell="{ record }">
                {{ formatFixed(record.avg_body_length, 2) }}
              </template>
            </a-table-column>
            <a-table-column title="平均体高(cm)" :width="120">
              <template #cell="{ record }">
                {{ formatFixed(record.avg_body_height, 2) }}
              </template>
            </a-table-column>
            <a-table-column title="平均体重(g)" :width="120">
              <template #cell="{ record }">
                {{ formatFixed(record.avg_body_weight, 2) }}
              </template>
            </a-table-column>
            <a-table-column title="平均肥满度" :width="120">
              <template #cell="{ record }">
                {{ formatFixed(record.avg_condition_factor, 3) }}
              </template>
            </a-table-column>
            <a-table-column title="平均破损率" :width="110">
              <template #cell="{ record }">
                {{ formatPercent(record.avg_surface_damage_ratio) }}
              </template>
            </a-table-column>
            <a-table-column
              title="操作"
              :width="360"
              fixed="right"
            >
              <template #cell="{ record }">
                <a-space :size="6">
                  <a-button
                    size="mini"
                    type="primary"
                    @click="openPdfReport(record)"
                  >
                    查看报表
                  </a-button>
                  <a-button
                    size="mini"
                    @click="goDashboard(record)"
                  >
                    跳转大屏
                  </a-button>
                  <a-button
                    size="mini"
                    @click="goVideoDetail(record)"
                  >
                    视频明细
                  </a-button>
                   <a-button size="mini" status="danger" @click="deleteVideo(record)">
                    删除
                  </a-button>
                </a-space>
              </template>
            </a-table-column>
          </template>
          <template #empty>
            <a-empty
              v-if="!loading"
              description="所选日期暂无视频检测数据"
            />
          </template>
        </a-table>
      </a-spin>

      <a-alert
        v-if="errorMessage"
        type="error"
        class="data-report-list-error"
        show-icon
      >
        {{ errorMessage }}
      </a-alert>
    </div>

    <div class="data-report-list-pagination">
      <a-pagination
        :current="currentPage"
        :page-size="pageSize"
        :total="total"
        show-total
        :show-size-changer="true"
        :page-size-options="[10, 20, 50]"
        :buffer-size="1"
        @change="handlePageChange"
        @page-size-change="handlePageSizeChange"
      />
    </div>

    <a-modal
      v-model:visible="videoPreviewVisible"
      title="源视频预览"
      :width="920"
      :footer="false"
      :mask-closable="true"
      @cancel="closeVideoPreview"
    >
      <div class="fixed-video-preview-card">
        <div class="fixed-video-preview-head">
          <div class="fixed-video-preview-head-info">
            <div class="fixed-video-preview-title">源视频预览</div>
            <div v-if="currentPreviewVideoIndex >= 0" class="fixed-video-preview-id">
              视频编号：{{
                tableData[currentPreviewVideoIndex]?.video_id || '--'
              }}
            </div>
          </div>
          <a-button @click="closeVideoPreview">关闭</a-button>
        </div>

        <div class="fixed-video-preview-media">
          <div v-if="videoPreviewLoading" class="fixed-video-preview-state">
            <a-spin tip="正在加载视频预览..." />
          </div>
          <div v-else-if="videoPreviewError" class="fixed-video-preview-state error">
            视频预览加载失败：{{ videoPreviewError }}
          </div>
          <video
            v-else-if="currentPlayableUrl"
            class="fixed-video-preview-video"
            :src="currentPlayableUrl"
            controls
            autoplay
            playsinline
          />
          <div v-else class="fixed-video-preview-state">
            暂无可播放的视频源地址。
          </div>
        </div>

        <div class="fixed-video-preview-footer">
          <a-space>
            <a-button @click="previewPrev">上一条</a-button>
            <a-button @click="previewNext">下一条</a-button>
            <span v-if="currentPlaybackCodec" class="fixed-video-preview-meta-info">
              编码：{{ currentPlaybackCodec }}
            </span>
          </a-space>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<style lang="scss" scoped>
.data-report-list-page {
  min-height: 100%;
  box-sizing: border-box;
  padding: 20px;
  background: #f7f8fa;
}
.data-report-list-header {
  margin-bottom: 12px;
  .data-report-list-title {
    margin: 0 0 4px;
    font-size: 20px;
    font-weight: 600;
    color: #1d2129;
  }
  .data-report-list-subtitle {
    font-size: 13px;
    color: #4e5969;
  }
}
.data-report-list-filter {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 12px;
}
.data-report-list-filter-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
.data-report-list-card {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  box-sizing: border-box;
  overflow: hidden;
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 12px;
  :deep(.arco-table-th) {
    white-space: nowrap;
  }

  /* Arco table keeps the configured x-scroll width (1564px), so the
     horizontal scroll must stay inside this card instead of expanding the page. */
  :deep(.arco-table),
  :deep(.arco-spin),
  :deep(.arco-table-container),
  :deep(.arco-table-content),
  :deep(.arco-table-body),
  :deep(.arco-table-header) {
    max-width: 100%;
    min-width: 0;
  }
  :deep(.arco-table-container) {
    overflow: hidden;
  }
  :deep(.arco-table-content),
  :deep(.arco-table-body),
  :deep(.arco-table-header) {
    overflow-x: auto;
  }
}
.data-report-list-error {
  margin-top: 8px;
}
.data-report-list-pagination {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  padding: 10px 16px;
}
.fixed-video-preview-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.fixed-video-preview-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  .fixed-video-preview-head-info {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .fixed-video-preview-title {
    font-size: 16px;
    font-weight: 600;
    color: #1d2129;
  }
.fixed-video-preview-id {
    font-size: 12px;
    color: #4e5969;
    font-family: SarasaMonoSC, Menlo, Monaco, Consolas, monospace;
  }
}
.fixed-video-preview-media {
  height: 480px;
  background: #000;
}
.fixed-video-preview-state {
  height: 100%;
  box-sizing: border-box;
  padding: 32px 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: #4e5969;
  font-size: 14px;
  background: #fff;
  &.error {
    color: #f5222d;
  }
}
.fixed-video-preview-video {
  width: 100%;
  height: 100%;
  max-width: 100%;
  object-fit: contain;
  background: #000;
  border-radius: 4px;
  outline: none;
}
.fixed-video-preview-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  color: #4e5969;
}
.fixed-video-preview-meta-info {
  font-family: SarasaMonoSC, Menlo, Monaco, Consolas, monospace;
}
</style>
