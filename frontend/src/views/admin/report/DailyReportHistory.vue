<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Message, Modal } from '@arco-design/web-vue'
import { http, toErrorMessage } from '@/api/http'
import { formatChinaDateTime } from '@/utils/time'
import { logUserAction } from '@/utils/operationLogger'

const emit = defineEmits<{
  (e: 'open-pdf', payload: { report_id: number; report_date: string }): void
}>()

const router = useRouter()

interface DetectionReportRow {
  report_id: number
  report_date: string
  report_type: string
  report_no: string
  report_name: string
  file_name?: string | null
  file_path?: string | null
  file_size?: number | null
  total_video: number
  total_fish: number
  abnormal_fish: number
  status: string
  error_message?: string | null
  usb_backup_status: string
  usb_backup_path?: string | null
  summary_json?: any
  created_at?: string | null
  updated_at?: string | null
}

type StatusFilter = '' | 'all' | 'generated' | 'failed'
// 报表类型筛选：
// '' 或 'all'       → 全部（不过滤）
// 'daily_fish_quality' → 日报
// 'video_fish_quality'  → 视频报表
type ReportTypeFilter = '' | 'all' | 'daily_fish_quality' | 'video_fish_quality'

const dateRange = ref<string[]>([])
const statusFilter = ref<StatusFilter>('')
const reportTypeFilter = ref<ReportTypeFilter>('')
const keyword = ref<string>('')
const page = ref(1)
const pageSize = ref(10)
const total = ref(0)
const loading = ref(false)
const tableData = ref<DetectionReportRow[]>([])

const actionLoading = ref<Record<number, boolean>>({})

const columns = computed(() => [
  { title: '报告日期', dataIndex: 'report_date', width: 120 },
  { title: '报表类型', dataIndex: 'report_type', width: 100, slotName: 'reportTypeCell' },
  { title: '报告名称', dataIndex: 'report_name', width: 220, ellipsis: true, tooltip: true },
  { title: '报告编号', dataIndex: 'report_no', width: 160 },
  { title: '视频总数', dataIndex: 'total_video', width: 90 },
  { title: '检测鱼总数', dataIndex: 'total_fish', width: 100 },
  { title: '异常鱼数量', dataIndex: 'abnormal_fish', width: 100 },
  { title: '生成状态', dataIndex: 'status', width: 100, slotName: 'statusCell' },
  { title: '生成时间', dataIndex: 'created_at', width: 170, slotName: 'createdAtCell' },
  { title: '文件大小', dataIndex: 'file_size', width: 100, slotName: 'fileSizeCell' },
  { title: '本地保存', dataIndex: 'file_path', width: 90, slotName: 'localCell' },
  { title: 'U盘备份', dataIndex: 'usb_backup_status', width: 100, slotName: 'usbCell' },
  { title: '操作', width: 260, fixed: 'right', slotName: 'actionCell' }
])

function fmtFileSize(size?: number | null) {
  if (size === null || size === undefined) return '--'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(2)} MB`
}

function statusLabel(s: string | undefined) {
  if (!s) return '--'
  if (s === 'generated') return '成功'
  if (s === 'failed') return '失败'
  return s
}

function statusColor(s: string | undefined) {
  if (s === 'generated') return 'green'
  if (s === 'failed') return 'red'
  return 'gray'
}

// 报表类型 → 中文标签
// daily_fish_quality → 日报
// video_fish_quality  → 视频报表
// 其它原始值或 '-'
function reportTypeLabel(t: string | undefined) {
  if (!t) return '-'
  if (t === 'daily_fish_quality') return '日报'
  if (t === 'video_fish_quality') return '视频报表'
  return t
}

function reportTypeColor(t: string | undefined) {
  if (t === 'daily_fish_quality') return 'blue'
  if (t === 'video_fish_quality') return 'green'
  return 'gray'
}

function usbLabel(s: string | undefined) {
  if (!s) return '--'
  if (s === 'backed_up') return '已备份'
  if (s === 'not_detected') return '未检测'
  if (s === 'failed') return '备份失败'
  return s
}

function usbColor(s: string | undefined) {
  if (s === 'backed_up') return 'green'
  if (s === 'failed') return 'red'
  return 'gray'
}

function isLocalSaved(row: DetectionReportRow) {
  return Boolean(row.file_path)
}

async function fetchData() {
  loading.value = true
  try {
    const params: Record<string, any> = {
      page: page.value,
      page_size: pageSize.value
    }
    if (dateRange.value && dateRange.value.length === 2) {
      params.start_date = dateRange.value[0]
      params.end_date = dateRange.value[1]
    }
    if (statusFilter.value) {
      params.status = statusFilter.value
    }
    if (reportTypeFilter.value && reportTypeFilter.value !== 'all') {
      // 'all' 和空都不过滤；其它值（daily_fish_quality / video_fish_quality）传给后端
      params.report_type = reportTypeFilter.value
    }
    if (keyword.value && keyword.value.trim()) {
      params.keyword = keyword.value.trim()
    }
    const resp = await http.get('/api/reports/history', { params })
    const data = resp.data
    tableData.value = data.items || []
    total.value = data.total || 0
  } catch (err) {
    Message.error(toErrorMessage(err))
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  page.value = 1
  fetchData()
}

function handleReset() {
  dateRange.value = []
  statusFilter.value = ''
  reportTypeFilter.value = ''
  keyword.value = ''
  page.value = 1
  fetchData()
}

function handlePageChange(p: number) {
  page.value = p
  fetchData()
}

function handlePageSizeChange(s: number) {
  pageSize.value = s
  page.value = 1
  fetchData()
}

function handleView(row: DetectionReportRow) {
  logUserAction({ module: '数据报表', action: 'open_history_pdf', targetType: 'report', targetId: row.report_id, message: '查看历史日报' })
  emit('open-pdf', {
    report_id: row.report_id,
    report_date: row.report_date
  })
}

function goToDailyToday() {
  router.push({
    path: '/admin/data-report/fish-quality',
    query: { type: 'daily' },
  })
}

function handleDownload(row: DetectionReportRow) {
  logUserAction({ module: '数据报表', action: 'download_history_pdf', targetType: 'report', targetId: row.report_id, message: '下载历史日报' })
  window.open(`/api/reports/history/${row.report_id}/download`, '_blank')
}

async function handleRegenerate(row: DetectionReportRow) {
  actionLoading.value[row.report_id] = true
  try {
    const resp = await http.post(
      `/api/reports/history/${row.report_id}/regenerate`,
      undefined,
      { timeout: 180000 },
    )
    Message.success(`日报已重新生成：${resp.data.report_name || row.report_name}`)
    fetchData()
  } catch (err) {
    Message.error(toErrorMessage(err))
  } finally {
    actionLoading.value[row.report_id] = false
  }
}

function handleDelete(row: DetectionReportRow) {
  Modal.confirm({
    title: '确认删除日报',
    content: `确定要删除报告《${row.report_name}》（编号：${row.report_no}）吗？该操作不可恢复。`,
    okText: '删除',
    cancelText: '取消',
    okButtonProps: { status: 'danger' },
    onOk: async () => {
      actionLoading.value[row.report_id] = true
      try {
        await http.delete(`/api/reports/history/${row.report_id}`)
        Message.success('日报已删除')
        fetchData()
      } catch (err) {
        Message.error(toErrorMessage(err))
      } finally {
        actionLoading.value[row.report_id] = false
      }
    }
  })
}

onMounted(() => {
  fetchData()
})
</script>

<template>
  <div class="report-page">
    <div class="report-page-inner daily-report-history-page">
    <div class="page-header">
      <h2>历史日报</h2>
      <p class="page-desc">用于查询、预览、下载、重新生成和删除已生成的鱼质检测日报。</p>
    </div>

    <a-card :bordered="false" class="filter-card">
      <a-row :gutter="[16, 16]">
        <a-col :span="6">
          <div class="filter-item">
            <label>报告日期范围</label>
            <a-range-picker
              v-model="dateRange"
              value-format="YYYY-MM-DD"
              style="width: 100%"
              @change="handleSearch"
            />
          </div>
        </a-col>
        <a-col :span="4">
          <div class="filter-item">
            <label>生成状态</label>
            <a-select
              v-model="statusFilter"
              placeholder="全部"
              allow-clear
              style="width: 100%"
              @change="handleSearch"
            >
              <a-option value="all">全部</a-option>
              <a-option value="generated">成功</a-option>
              <a-option value="failed">失败</a-option>
            </a-select>
          </div>
        </a-col>
        <a-col :span="4">
          <div class="filter-item">
            <label>报表类型</label>
            <a-select
              v-model="reportTypeFilter"
              placeholder="全部"
              allow-clear
              style="width: 100%"
              @change="handleSearch"
            >
              <a-option value="">全部</a-option>
              <a-option value="daily_fish_quality">日报</a-option>
              <a-option value="video_fish_quality">视频报表</a-option>
            </a-select>
          </div>
        </a-col>
        <a-col :span="6">
          <div class="filter-item">
            <label>关键词（报告名称 / 报告编号）</label>
            <a-input
              v-model="keyword"
              placeholder="请输入关键词"
              allow-clear
              @press-enter="handleSearch"
            />
          </div>
        </a-col>
        <a-col :span="8">
          <div class="filter-item filter-actions">
            <label>&nbsp;</label>
            <div>
              <a-button type="primary" @click="handleSearch">
                <template #icon><icon-search /></template>
                查询
              </a-button>
              <a-button @click="handleReset">
                <template #icon><icon-refresh /></template>
                重置
              </a-button>
            </div>
          </div>
        </a-col>
      </a-row>
    </a-card>

    <a-card :bordered="false" class="table-card">
      <a-table
        :columns="columns"
        :data="tableData"
        :loading="loading"
        :pagination="false"
        :bordered="{ cell: true }"
        :scroll="{ x: 1500 }"
        row-key="report_id"
        stripe
        size="small"
      >
        <template #reportTypeCell="{ record }">
          <a-tag :color="reportTypeColor(record.report_type)">{{ reportTypeLabel(record.report_type) }}</a-tag>
        </template>
        <template #statusCell="{ record }">
          <a-tag :color="statusColor(record.status)">{{ statusLabel(record.status) }}</a-tag>
        </template>
        <template #createdAtCell="{ record }">
          {{ formatChinaDateTime(record.created_at) }}
        </template>
        <template #fileSizeCell="{ record }">
          {{ fmtFileSize(record.file_size) }}
        </template>
        <template #localCell="{ record }">
          <a-tag :color="isLocalSaved(record) ? 'green' : 'gray'">
            {{ isLocalSaved(record) ? '已保存' : '未保存' }}
          </a-tag>
        </template>
        <template #usbCell="{ record }">
          <a-tag :color="usbColor(record.usb_backup_status)">{{ usbLabel(record.usb_backup_status) }}</a-tag>
        </template>
        <template #actionCell="{ record }">
          <a-button
            type="text"
            size="small"
            :disabled="!isLocalSaved(record)"
            @click="handleView(record)"
          >
            查看
          </a-button>
          <a-button
            type="text"
            size="small"
            :disabled="!isLocalSaved(record)"
            @click="handleDownload(record)"
          >
            下载
          </a-button>
          <a-button
            type="text"
            size="small"
            :loading="actionLoading[record.report_id]"
            @click="handleRegenerate(record)"
          >
            重新生成
          </a-button>
          <a-button
            type="text"
            size="small"
            status="danger"
            :loading="actionLoading[record.report_id]"
            @click="handleDelete(record)"
          >
            删除
          </a-button>
        </template>
      </a-table>

      <a-empty
        v-if="!loading && tableData.length === 0"
        description="暂无已生成的日报"
      >
        <a-button type="primary" @click="goToDailyToday">前往当日数据生成日报</a-button>
      </a-empty>

      <div class="pagination-wrap">
        <a-pagination
          v-model:current="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-size-options="[10, 20, 50, 100]"
          show-total
          show-page-size
          @change="handlePageChange"
          @page-size-change="handlePageSizeChange"
        />
      </div>
    </a-card>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.report-page {
  height: 100%;
  min-height: 0;
  box-sizing: border-box;
  overflow: hidden auto;
}
.report-page-inner {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
  padding-bottom: 24px;
  box-sizing: border-box;
}
.daily-report-history-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.page-header {
  h2 {
    margin: 0;
    font-size: 20px;
    font-weight: 600;
    color: #1d2129;
  }
  .page-desc {
    margin: 6px 0 0;
    font-size: 13px;
    color: #6b7280;
  }
}
.filter-card {
  background: #fff;
  border-radius: 8px;
  .filter-item {
    display: flex;
    flex-direction: column;
    gap: 6px;
    label {
      font-size: 12px;
      color: #4e5969;
    }
  }
  .filter-actions {
    flex-direction: column;
    div {
      display: flex;
      gap: 8px;
    }
  }
}
.table-card {
  background: #fff;
  border-radius: 8px;
}
.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
