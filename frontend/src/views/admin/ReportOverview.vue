<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { http, httpLong, toErrorMessage } from '@/api/http'
import { Message, Modal } from '@arco-design/web-vue'
import AdminTabbedWorkspace from '@/components/admin/AdminTabbedWorkspace.vue'
import ReportPdfTab from '@/views/admin/report/ReportPdfTab.vue'
import { formatChinaDateTime } from '@/utils/time'

const route = useRoute()
type BaseReportTab = 'video' | 'daily' | 'custom'
const activeTab = ref<string>('video')
const dateRange = ref<string[]>([])
const aquacultureBin = ref('')
const loading = ref(false)
const errorMessage = ref('')
const rows = ref<any[]>([])
const selectedVideoIds = ref<string[]>([])
const selectedReportIds = ref<number[]>([])
const reportNameDrafts = ref<Record<number, string>>({})
const renamingReportIds = ref<Set<number>>(new Set())
const customReportName = ref('自定义报表')
const videoPage = ref(1)
const videoPageSize = ref(10)
const reportTotal = ref(0)
const videoPreviewVisible = ref(false)
const videoPreviewLoading = ref(false)
const videoPreviewError = ref('')
const videoPreviewUrl = ref('')
const videoPreviewId = ref('')
const currentPlaybackCodec = ref('')
const currentPreviewVideoIndex = ref(-1)

const reportTabs = ref([
  { key: 'video', title: '视频报表', closable: false },
  { key: 'daily', title: '当日报表', closable: false },
  { key: 'custom', title: '自定义报表', closable: false },
])

const REPORT_OVERVIEW_STATE_KEY = 'admin-report-overview-state'
type ReportOverviewState = Record<BaseReportTab, {
  dateRange: string[]
  aquacultureBin: string
  page: number
  pageSize: number
}>
const REPORT_OVERVIEW_ACTIVE_TAB_KEY = 'admin-report-overview-active-tab'
const REPORT_OVERVIEW_SELECTED_VIDEO_IDS_KEY = 'admin-report-overview-selected-video-ids'

function saveSelectedVideoIds(): void {
  try {
    sessionStorage.setItem(REPORT_OVERVIEW_SELECTED_VIDEO_IDS_KEY, JSON.stringify(selectedVideoIds.value))
  } catch {
    // Storage may be unavailable in restricted browser contexts.
  }
}

function restoreSelectedVideoIds(): void {
  try {
    const raw = sessionStorage.getItem(REPORT_OVERVIEW_SELECTED_VIDEO_IDS_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    if (Array.isArray(parsed)) selectedVideoIds.value = parsed.map(String).filter(Boolean)
  } catch {
    selectedVideoIds.value = []
  }
}

function defaultReportOverviewState(): ReportOverviewState {
  return {
    video: { dateRange: [], aquacultureBin: '', page: 1, pageSize: 10 },
    daily: { dateRange: [], aquacultureBin: '', page: 1, pageSize: 10 },
    custom: { dateRange: [], aquacultureBin: '', page: 1, pageSize: 10 },
  }
}

function readReportOverviewState(): ReportOverviewState {
  const fallback = defaultReportOverviewState()
  try {
    const raw = sessionStorage.getItem(REPORT_OVERVIEW_STATE_KEY)
    if (!raw) return fallback
    const parsed = JSON.parse(raw) as Partial<ReportOverviewState>
    for (const key of ['video', 'daily', 'custom'] as BaseReportTab[]) {
      const value = parsed[key]
      if (!value) continue
      fallback[key].dateRange = Array.isArray(value.dateRange) ? value.dateRange.filter(Boolean) : []
      fallback[key].aquacultureBin = String(value.aquacultureBin || '')
      fallback[key].page = Number.isInteger(value.page) && Number(value.page) > 0 ? Number(value.page) : 1
      fallback[key].pageSize = Number.isInteger(value.pageSize) && Number(value.pageSize) > 0 ? Number(value.pageSize) : 10
    }
  } catch {
    // Storage may be unavailable in restricted browser contexts.
  }
  return fallback
}

function saveReportOverviewState(): void {
  try {
    const state = readReportOverviewState()
    state[baseTab.value] = {
      dateRange: dateRange.value,
      aquacultureBin: aquacultureBin.value,
      page: videoPage.value,
      pageSize: videoPageSize.value,
    }
    sessionStorage.setItem(REPORT_OVERVIEW_STATE_KEY, JSON.stringify(state))
  } catch {
    // Storage may be unavailable in restricted browser contexts.
  }
}

function restoreReportOverviewTabState(tab: BaseReportTab): void {
  const state = readReportOverviewState()[tab]
  dateRange.value = [...state.dateRange]
  aquacultureBin.value = state.aquacultureBin
  videoPage.value = state.page
  videoPageSize.value = state.pageSize
}

const baseTab = computed<BaseReportTab>(() => {
  if (activeTab.value === 'daily' || activeTab.value === 'custom') return activeTab.value
  return 'video'
})
const isPdfTab = computed(() => activeTab.value.startsWith('report-pdf-'))

async function loadVideoReports(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const resp = await http.get('/api/videos', {
      params: {
        page: videoPage.value,
        page_size: videoPageSize.value,
        start_date: dateRange.value[0] || undefined,
        end_date: dateRange.value[1] || dateRange.value[0] || undefined,
        aquaculture_bin: aquacultureBin.value.trim() || undefined,
      },
    })
    rows.value = resp.data?.items || []
    reportTotal.value = Number(resp.data?.total) || rows.value.length
  } catch (err) {
    errorMessage.value = toErrorMessage(err) || '加载视频报表失败'
  } finally {
    loading.value = false
  }
}

async function loadDailyReports(): Promise<void> {
  loading.value = true
  try {
    const resp = await http.get('/api/reports/history', {
      params: { report_type: 'daily_fish_quality', page: videoPage.value, page_size: videoPageSize.value, start_date: dateRange.value[0] || undefined, end_date: dateRange.value[1] || dateRange.value[0] || undefined },
    })
    rows.value = resp.data?.items || []
    reportTotal.value = Number(resp.data?.total) || rows.value.length
  } catch (err) {
    errorMessage.value = toErrorMessage(err) || '加载日报失败'
  } finally {
    loading.value = false
  }
}

async function loadCustomReports(): Promise<void> {
  loading.value = true
  try {
    const resp = await http.get('/api/reports/history', {
      params: {
        report_type: 'custom_video_fish_quality',
        page: videoPage.value,
        page_size: videoPageSize.value,
        start_date: dateRange.value[0] || undefined,
        end_date: dateRange.value[1] || dateRange.value[0] || undefined,
      },
    })
    rows.value = resp.data?.items || []
    reportNameDrafts.value = Object.fromEntries(
      rows.value
        .filter((record) => record.report_id)
        .map((record) => [Number(record.report_id), String(record.report_name || '')])
    )
    reportTotal.value = Number(resp.data?.total) || rows.value.length
  } catch (err) {
    errorMessage.value = toErrorMessage(err) || '加载自定义报表失败'
  } finally {
    loading.value = false
  }
}

function switchTab(tab: BaseReportTab): void {
  activeTab.value = tab
  try {
    sessionStorage.setItem(REPORT_OVERVIEW_ACTIVE_TAB_KEY, tab)
  } catch {
    // Storage may be unavailable in restricted browser contexts.
  }
  selectedVideoIds.value = []
  selectedReportIds.value = []
  restoreReportOverviewTabState(tab)
  if (tab === 'video') void loadVideoReports()
  if (tab === 'daily') void loadDailyReports()
  if (tab === 'custom') void loadCustomReports()
}

function handleDateRangeChange(): void {
  videoPage.value = 1
  saveReportOverviewState()
  if (baseTab.value === 'video') void loadVideoReports()
  else if (baseTab.value === 'daily') void loadDailyReports()
  else void loadCustomReports()
}

function handleFilterChange(): void {
  handleDateRangeChange()
  selectedVideoIds.value = []
  saveSelectedVideoIds()
}

function isCurrentPageSelected(): boolean {
  const pageIds = rows.value.map((row) => String(row.video_id || '')).filter(Boolean)
  return pageIds.length > 0 && pageIds.every((id) => selectedVideoIds.value.includes(id))
}

function toggleCurrentPageSelection(checked: boolean): void {
  const pageIds = rows.value.map((row) => String(row.video_id || '')).filter(Boolean)
  selectedVideoIds.value = checked
    ? [...new Set([...selectedVideoIds.value, ...pageIds])]
    : selectedVideoIds.value.filter((id) => !pageIds.includes(id))
  saveSelectedVideoIds()
}

function handlePageChange(page: number): void {
  videoPage.value = page
  saveReportOverviewState()
  if (baseTab.value === 'video') void loadVideoReports()
  else if (baseTab.value === 'daily') void loadDailyReports()
  else void loadCustomReports()
}

function handlePageSizeChange(size: number): void {
  videoPageSize.value = size
  saveReportOverviewState()
  handlePageChange(1)
}

function toggleReportSelection(reportId: number, checked: boolean): void {
  selectedReportIds.value = checked
    ? [...new Set([...selectedReportIds.value, reportId])]
    : selectedReportIds.value.filter((id) => id !== reportId)
}

async function renameReport(record: any, value: string): Promise<void> {
  const name = value.trim()
  const reportId = Number(record.report_id)
  if (!reportId || renamingReportIds.value.has(reportId)) return
  if (!name) {
    reportNameDrafts.value[reportId] = String(record.report_name || '')
    Message.warning('报表名称不能为空')
    return
  }
  if (name === String(record.report_name || '').trim()) {
    reportNameDrafts.value[reportId] = String(record.report_name || '')
    return
  }

  renamingReportIds.value = new Set([...renamingReportIds.value, reportId])
  try {
    const resp = await http.patch(`/api/reports/history/${reportId}/name`, { report_name: name })
    record.report_name = resp.data?.report_name || name
    reportNameDrafts.value[reportId] = record.report_name
    const tab = reportTabs.value.find((item) => item.key === `report-pdf-${reportId}`)
    if (tab) tab.title = record.report_name
    Message.success('报表名称已保存')
  } catch (err) {
    Message.error(toErrorMessage(err) || '报表名称保存失败')
  } finally {
    const pending = new Set(renamingReportIds.value)
    pending.delete(reportId)
    renamingReportIds.value = pending
  }
}

async function deleteSelectedReports(): Promise<void> {
  if (baseTab.value === 'video') {
    if (!selectedVideoIds.value.length) {
      Message.info('请先选择需要删除的视频')
      return
    }
    Modal.confirm({
      title: '确认批量永久删除',
      content: `已选择 ${selectedVideoIds.value.length} 条视频。此操作会永久删除视频、检测结果、报表及相关文件，删除后不会留下任何数据。确定继续吗？`,
      okText: '永久删除',
      cancelText: '取消',
      okButtonProps: { status: 'danger' },
      onOk: async () => {
        try {
          await http.post('/api/videos/batch-delete', { video_ids: selectedVideoIds.value })
          Message.success('选中视频及相关数据已永久删除')
          selectedVideoIds.value = []
          await loadVideoReports()
        } catch (err) {
          Message.error(toErrorMessage(err) || '批量删除失败')
        }
      },
    })
    return
  }

  if (!selectedReportIds.value.length) {
    Message.info('请先选择需要删除的报表')
    return
  }
  Modal.confirm({
    title: '确认批量删除报表',
    content: `已选择 ${selectedReportIds.value.length} 条报表。删除后不可恢复，确定继续吗？`,
    okText: '删除',
    cancelText: '取消',
    okButtonProps: { status: 'danger' },
    onOk: async () => {
      try {
        await Promise.all(selectedReportIds.value.map((id) => http.delete(`/api/reports/history/${id}`)))
        Message.success('选中报表已删除')
        selectedReportIds.value = []
        handlePageChange(videoPage.value)
      } catch (err) {
        Message.error(toErrorMessage(err) || '报表删除失败')
      }
    },
  })
}

async function downloadSelectedReports(): Promise<void> {
  const reportIds = baseTab.value === 'video'
    ? rows.value
      .filter((row) => selectedVideoIds.value.includes(row.video_id) && row.report_id)
      .map((row) => Number(row.report_id))
    : selectedReportIds.value
  if (!reportIds.length) {
    Message.info('请先选择需要下载的报表')
    return
  }
  try {
    const response = await http.post('/api/reports/history/batch-download', { report_ids: reportIds }, { responseType: 'blob' })
    const contentType = String(response.headers['content-type'] || '')
    const isZip = contentType.includes('zip') || reportIds.length > 1
    const name = isZip ? 'fish_quality_reports.zip' : `${rows.value.find((row) => Number(row.report_id) === reportIds[0])?.report_name || `report_${reportIds[0]}`}.pdf`
    const url = URL.createObjectURL(response.data)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = name.replace(/[\\/:*?"<>|\r\n]+/g, '_')
    anchor.click()
    URL.revokeObjectURL(url)
    Message.success('报表下载已开始')
  } catch (err) {
    Message.error(toErrorMessage(err) || '报表下载失败')
  }
}

async function openVideo(videoId: string): Promise<void> {
  const index = rows.value.findIndex((row) => String(row.video_id) === String(videoId))
  currentPreviewVideoIndex.value = index >= 0 ? index : 0
  videoPreviewId.value = videoId
  videoPreviewVisible.value = true
  videoPreviewLoading.value = true
  videoPreviewError.value = ''
  videoPreviewUrl.value = ''
  currentPlaybackCodec.value = ''
  try {
    const resp = await http.get(`/api/videos/${encodeURIComponent(videoId)}/playback`)
    if (!resp.data?.ok) throw new Error(resp.data?.error_message || '视频预览地址获取失败')
    const playableUrl = String(resp.data?.playable_url || resp.data?.source_url || '')
    if (!playableUrl) throw new Error('后端未返回 playable_url')
    videoPreviewUrl.value = playableUrl
    currentPlaybackCodec.value = resp.data?.codec_name
      ? `${resp.data.codec_name}${resp.data.pix_fmt ? ` · ${resp.data.pix_fmt}` : ''}`
      : ''
  } catch (err) {
    videoPreviewError.value = toErrorMessage(err) || '视频预览失败'
  } finally {
    videoPreviewLoading.value = false
  }
}

function closeVideoPreview(): void {
  videoPreviewVisible.value = false
  videoPreviewUrl.value = ''
  videoPreviewError.value = ''
  currentPlaybackCodec.value = ''
}

function previewPrev(): void {
  if (!rows.value.length) return
  const next = currentPreviewVideoIndex.value <= 0
    ? rows.value.length - 1
    : currentPreviewVideoIndex.value - 1
  const target = rows.value[next]
  if (target?.video_id) void openVideo(target.video_id)
}

function previewNext(): void {
  if (!rows.value.length) return
  const next = currentPreviewVideoIndex.value < 0 || currentPreviewVideoIndex.value >= rows.value.length - 1
    ? 0
    : currentPreviewVideoIndex.value + 1
  const target = rows.value[next]
  if (target?.video_id) void openVideo(target.video_id)
}

function openReport(reportId: number): void {
  const key = `report-pdf-${reportId}`
  const row = rows.value.find((item) => Number(item.report_id) === reportId)
  if (!reportTabs.value.some((tab) => tab.key === key)) {
    reportTabs.value.push({ key, title: String(row?.report_name || `报表-${reportId}`), closable: true })
  }
  activeTab.value = key
}

async function generateCustomReport(): Promise<void> {
  if (!selectedVideoIds.value.length) {
    Message.warning('请先选择要生成报表的视频')
    return
  }
  try {
    const reportName = customReportName.value.trim()
    if (!reportName) {
      Message.warning('请输入自定义报表名称')
      return
    }
    const resp = await httpLong.post('/api/reports/custom-pdf/generate', {
      video_ids: selectedVideoIds.value,
      report_name: reportName,
    })
    Message.success('自定义报表已生成')
    if (resp.data?.report_id) openReport(Number(resp.data.report_id))
  } catch (err) {
    Message.error(toErrorMessage(err) || '生成自定义报表失败')
  }
}

onMounted(() => {
  const restoredState = readReportOverviewState()
  let restoredTab: BaseReportTab = 'video'
  try {
    const storedTab = sessionStorage.getItem(REPORT_OVERVIEW_ACTIVE_TAB_KEY)
    if (storedTab === 'daily' || storedTab === 'custom' || storedTab === 'video') {
      restoredTab = storedTab
    }
  } catch {
    // Storage may be unavailable in restricted browser contexts.
  }
  if (route.query.tab === 'daily' || route.query.tab === 'custom') {
    restoredTab = route.query.tab as BaseReportTab
  }
  activeTab.value = restoredTab
  restoreSelectedVideoIds()
  dateRange.value = [...restoredState[restoredTab].dateRange]
  videoPage.value = restoredState[restoredTab].page
  videoPageSize.value = restoredState[restoredTab].pageSize
  if (route.query.tab === 'custom') {
    switchTab('custom')
    const ids = String(route.query.video_ids || '').split(',').filter(Boolean)
    if (ids.length) {
      selectedVideoIds.value = ids
      void generateCustomReport()
    }
  }
  else if (restoredTab === 'daily') void loadDailyReports()
  else if (restoredTab === 'custom') void loadCustomReports()
  else void loadVideoReports()
})
</script>

<template>
  <main class="report-overview-page">
    <div class="data-report-list-header">
      <h2 class="data-report-list-title">报表总览</h2>
      <div class="data-report-list-subtitle">查看视频报表、当日报表和自定义报表。</div>
    </div>
    <AdminTabbedWorkspace
      :tabs="reportTabs"
      :active-key="activeTab"
      @update:active-key="(key: string) => {
        if (key.startsWith('report-pdf-')) activeTab = key
        else switchTab(key as BaseReportTab)
      }"
    >
      <ReportPdfTab
        v-if="isPdfTab"
        :report-id="Number(activeTab.replace('report-pdf-', ''))"
      />
      <div v-else class="report-overview-pane">
        <div class="report-overview-toolbar">
          <div class="report-overview-filter">
            <a-form layout="inline">
              <a-form-item label="检测日期">
                <a-range-picker
              v-model="dateRange"
              value-format="YYYY-MM-DD"
              :placeholder="['开始日期', '结束日期']"
              :style="{ width: '260px' }"
              @change="handleDateRangeChange"
            />
              </a-form-item>
              <a-form-item label="养鱼仓">
                <a-input
                  v-model="aquacultureBin"
                  placeholder="输入养鱼仓名称"
                  allow-clear
                  :style="{ width: '180px' }"
                  @press-enter="handleFilterChange"
                />
              </a-form-item>
              <a-form-item>
                <a-button type="primary" @click="handleFilterChange">查询</a-button>
              </a-form-item>
            </a-form>
          </div>
          <a-space>
            <a-button @click="downloadSelectedReports">批量下载</a-button>
            <a-button status="danger" @click="deleteSelectedReports">批量删除</a-button>
          </a-space>
        </div>
        <a-alert v-if="errorMessage" type="error">{{ errorMessage }}</a-alert>
        <a-table
          class="report-overview-table"
          :data="rows"
          :loading="loading"
          :pagination="false"
          :row-key="baseTab === 'video' ? 'video_id' : 'report_id'"
        >
          <template #columns>
            <a-table-column v-if="baseTab === 'video'" title="序号" :width="72">
              <template #cell="{ rowIndex }">
                {{ (videoPage - 1) * videoPageSize + rowIndex + 1 }}
              </template>
            </a-table-column>
            <a-table-column v-if="baseTab === 'video'" title="选择" :width="72">
              <template #header>
                <a-checkbox
                  :model-value="isCurrentPageSelected()"
                  @change="(checked: boolean) => toggleCurrentPageSelection(checked)"
                />
              </template>
              <template #cell="{ record }">
                <a-checkbox
                  :model-value="selectedVideoIds.includes(record.video_id)"
                  @change="(checked: boolean) => {
                    selectedVideoIds = checked
                       ? [...new Set([...selectedVideoIds, record.video_id])]
                       : selectedVideoIds.filter((id) => id !== record.video_id)
                     saveSelectedVideoIds()
                  }"
                />
              </template>
            </a-table-column>
            <a-table-column v-else title="选择" :width="72">
              <template #cell="{ record }">
                <a-checkbox
                  :model-value="selectedReportIds.includes(record.report_id)"
                  @change="(checked: boolean) => toggleReportSelection(record.report_id, checked)"
                />
              </template>
            </a-table-column>
            <a-table-column v-if="baseTab === 'video'" title="视频编号">
              <template #cell="{ record }">
                <a-link v-if="baseTab === 'video'" @click="openVideo(record.video_id)">{{ record.video_id }}</a-link>
                <span v-else-if="baseTab === 'custom'">{{ record.video_id || '--' }}</span>
                <span v-else>--</span>
              </template>
            </a-table-column>
            <a-table-column v-if="baseTab === 'video'" title="操作">
              <template #cell="{ record }">
                <a-button
                  size="small"
                  :disabled="!record.report_id"
                  @click="record.report_id && openReport(Number(record.report_id))"
                >查看报表</a-button>
              </template>
            </a-table-column>
            <a-table-column v-if="baseTab === 'video'" title="养鱼仓" data-index="aquaculture_bin" />
            <a-table-column v-if="baseTab === 'video'" title="总鱼数" data-index="total_fish" />
            <a-table-column v-if="baseTab !== 'video'" title="报表名称">
              <template #cell="{ record }">
                <a-input
                  v-if="baseTab === 'custom'"
                  :model-value="reportNameDrafts[record.report_id] ?? record.report_name"
                  allow-clear
                  :disabled="renamingReportIds.has(Number(record.report_id))"
                  @update:model-value="(value: string) => { reportNameDrafts[record.report_id] = value }"
                  @press-enter="renameReport(record, reportNameDrafts[record.report_id] ?? record.report_name)"
                />
                <span v-else>{{ record.report_name }}</span>
              </template>
            </a-table-column>
            <a-table-column v-if="baseTab !== 'video'" title="生成时间">
              <template #cell="{ record }">{{ formatChinaDateTime(record.created_at) }}</template>
            </a-table-column>
            <a-table-column v-if="baseTab !== 'video'" title="操作">
              <template #cell="{ record }">
                <a-button size="small" @click="openReport(record.report_id)">查看报表</a-button>
              </template>
            </a-table-column>
          </template>
        </a-table>
        <div class="report-overview-pagination">
          <a-pagination
            :current="videoPage"
            :page-size="videoPageSize"
            :total="reportTotal"
            show-total
            show-page-size
            :page-size-options="[10, 20, 50]"
            :buffer-size="1"
            @change="handlePageChange"
            @page-size-change="handlePageSizeChange"
          />
        </div>
        <a-button v-if="baseTab === 'video'" type="primary" :disabled="!selectedVideoIds.length" @click="generateCustomReport">生成合并报表</a-button>
        <a-input
          v-if="baseTab === 'video' && selectedVideoIds.length"
          v-model="customReportName"
          placeholder="请输入名称后回车"
          allow-clear
          class="custom-report-name-input"
          @press-enter="generateCustomReport"
          @keydown.enter="generateCustomReport"
        />
      </div>
    </AdminTabbedWorkspace>
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
            <div class="fixed-video-preview-id">视频编号：{{ videoPreviewId || '--' }}</div>
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
            v-else-if="videoPreviewUrl"
            class="fixed-video-preview-video"
            :src="videoPreviewUrl"
            controls
            autoplay
            playsinline
          />
          <div v-else class="fixed-video-preview-state">暂无可播放的视频源地址。</div>
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
  </main>
</template>

<style scoped>
.report-overview-page { padding: 20px; min-height: 100%; box-sizing: border-box; background: #f7f8fa; }
.data-report-list-header { margin-bottom: 12px; }
.data-report-list-title { margin: 0 0 4px; color: #1d2129; font-size: 20px; font-weight: 600; }
.data-report-list-subtitle { color: #4e5969; font-size: 13px; }
.report-overview-page :deep(.admin-workspace) { height: calc(100vh - 150px); }
.report-overview-pane { padding: 16px; background: #fff; min-height: 100%; box-sizing: border-box; }
.report-overview-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 14px; }
.report-overview-filter { display: flex; align-items: center; gap: 10px; }
.report-overview-pagination { display: flex; justify-content: flex-end; margin-top: 12px; }
.report-overview-pane :deep(.report-overview-table),
.report-overview-pane :deep(.arco-table-container),
.report-overview-pane :deep(.arco-table-content),
.report-overview-pane :deep(.arco-table-element) {
  width: 100%;
}
.report-overview-pane :deep(.arco-table-element) {
  table-layout: fixed;
}
.report-overview-pane :deep(.arco-table-td-content),
.report-overview-pane :deep(.arco-table-th-content) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.report-overview-pane :deep(.arco-table-td) {
  vertical-align: middle;
}
.report-overview-pdf { width: 100%; height: 720px; margin-top: 16px; border: 1px solid #e5e6eb; background: #fff; }
.fixed-video-preview-card { display: flex; flex-direction: column; gap: 12px; }
.fixed-video-preview-head { display: flex; justify-content: space-between; align-items: flex-start; }
.fixed-video-preview-head-info { display: flex; flex-direction: column; gap: 4px; }
.fixed-video-preview-title { font-size: 16px; font-weight: 600; color: #1d2129; }
.fixed-video-preview-id { font-size: 12px; color: #4e5969; font-family: SarasaMonoSC, Menlo, Monaco, Consolas, monospace; }
.fixed-video-preview-media { height: 480px; background: #000; }
.fixed-video-preview-state { height: 100%; box-sizing: border-box; padding: 32px 16px; display: flex; align-items: center; justify-content: center; text-align: center; color: #4e5969; font-size: 14px; background: #fff; }
.fixed-video-preview-state.error { color: #f5222d; }
.fixed-video-preview-video { width: 100%; height: 100%; max-width: 100%; object-fit: contain; background: #000; border-radius: 4px; outline: none; }
.fixed-video-preview-footer { display: flex; justify-content: space-between; align-items: center; font-size: 13px; color: #4e5969; }
.fixed-video-preview-meta-info { font-family: SarasaMonoSC, Menlo, Monaco, Consolas, monospace; }
</style>
