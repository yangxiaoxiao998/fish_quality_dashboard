<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { http, toErrorMessage } from '@/api/http'
import { formatChinaDateTime } from '@/utils/time'

interface OperationLog {
  id?: number
  created_at?: string
  operator?: string | null
  username?: string | null
  ip?: string | null
  ip_address?: string | null
  module?: string | null
  action?: string | null
  action_label?: string | null
  method?: string | null
  request_method?: string | null
  path?: string | null
  request_path?: string | null
  status?: string | null
  status_code?: number | null
  success?: boolean | null
  message?: string | null
  error_message?: string | null
  duration_ms?: number | null
}

interface NormalizedRow extends OperationLog {
  _id: string
  _created_at: string
  _time_text: string
  _operator: string
  _module: string
  _action: string
  _status: string
  _method: string
  _path: string
  _ip: string
  _duration_ms: number | null
  _duration_text: string
  _message: string
}

const dateRange = ref<string[]>([])
const moduleFilter = ref<string>('')
const statusFilter = ref<string>('')
const keyword = ref<string>('')
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const loading = ref(false)
const tableRows = ref<NormalizedRow[]>([])

const safeRows = computed<NormalizedRow[]>(() =>
  Array.isArray(tableRows.value) ? tableRows.value : [],
)

function statusText(record: OperationLog): string {
  if (record.status) return record.status === 'success' ? '成功' : '失败'
  if (record.success === false) return '失败'
  return '成功'
}

function statusColor(record: OperationLog): string {
  return statusText(record) === '成功' ? 'green' : 'red'
}

function formatDurationMs(value: any): string {
  const n = Number(value)
  if (!Number.isFinite(n) || n < 0) return '--'
  if (n < 1000) return `${Math.round(n)} ms`
  return `${(n / 1000).toFixed(2)} s`
}

function normalizeRow(row: any): NormalizedRow {
  const createdAt =
    row.created_at || row.createdAt || row.time || row.timestamp || ''
  const moduleName = row.module || row.module_name || row.category || '--'
  const action = row.action || row.operation || row.event || '--'
  const actionLabel = row.action_label || action || '--'
  const status =
    row.status ||
    row.result ||
    (row.success === false ? 'failed' : row.success === true ? 'success' : '') ||
    '--'
  const method = row.method || row.request_method || '--'
  const path = row.path || row.request_path || row.url || '--'
  const ip = row.ip || row.ip_address || row.client_ip || '--'
  const durationMs =
    row.duration_ms ?? row.duration ?? row.cost_ms ?? null
  const message =
    row.message || row.description || row.detail || row.summary || '--'
  const operator =
    row.operator || row.username || row.user || row.user_name || '系统用户'

  const id =
    row.id || row.log_id || `${createdAt}-${moduleName}-${action}-${Math.random()}`
  return {
    ...row,
    _id: String(id),
    _created_at: String(createdAt),
    _time_text: formatChinaDateTime(createdAt),
    _operator: String(operator || '系统用户'),
    _module: String(moduleName || '--'),
    _action: String(actionLabel || '--'),
    _status: String(status || '--'),
    _method: String(method || '--'),
    _path: String(path || '--'),
    _ip: String(ip || '--'),
    _duration_ms: durationMs == null ? null : Number(durationMs),
    _duration_text: formatDurationMs(durationMs),
    _message: String(message || '--'),
  }
}

function normalizeResponse(resp: any): { items: any[]; total: number } {
  const raw = resp?.data ?? resp ?? {}
  const payload = raw?.data && typeof raw.data === 'object' ? raw.data : raw
  const items = Array.isArray(payload?.items)
    ? payload.items
    : Array.isArray(payload?.records)
    ? payload.records
    : Array.isArray(payload?.rows)
    ? payload.rows
    : Array.isArray(payload?.list)
    ? payload.list
    : Array.isArray(raw)
    ? raw
    : []
  const total = Number(
    payload?.total ?? payload?.count ?? payload?.total_count ?? items.length,
  )
  return {
    items,
    total: Number.isFinite(total) && total >= 0 ? total : items.length,
  }
}

async function fetchData() {
  loading.value = true
  try {
    const params: Record<string, any> = {
      page: page.value,
      page_size: pageSize.value,
    }
    if (dateRange.value && dateRange.value.length === 2) {
      params.date_from = dateRange.value[0]
      params.date_to = dateRange.value[1]
    }
    if (moduleFilter.value) params.module = moduleFilter.value
    if (statusFilter.value) params.status = statusFilter.value
    if (keyword.value && keyword.value.trim()) params.keyword = keyword.value.trim()
    const resp = await http.get('/api/operation-logs', { params })
    const normalized = normalizeResponse(resp)
    tableRows.value = normalized.items.map(normalizeRow)
    total.value = normalized.total
    console.log(
      '[OperationLogReport] total:',
      total.value,
      'rows:',
      tableRows.value.length,
    )
    if (tableRows.value.length > 0) {
      console.log(
        '[OperationLogReport] first row:',
        JSON.stringify(tableRows.value[0]).slice(0, 400),
      )
    }
  } catch (err) {
    Message.error(toErrorMessage(err))
    tableRows.value = []
    total.value = 0
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
  moduleFilter.value = ''
  statusFilter.value = ''
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

onMounted(() => {
  fetchData()
})
</script>

<template>
  <div class="report-page">
    <div class="report-page-inner">
      <div class="report-panel report-header-card">
        <div>
          <h2 class="page-title">系统操作日志</h2>
          <div class="page-subtitle">展示真实系统操作记录与用户行为日志。</div>
        </div>
      </div>

      <div class="report-panel">
        <a-form layout="inline" :model="{}">
          <a-form-item label="时间">
            <a-range-picker v-model="dateRange" value-format="YYYY-MM-DD HH:mm" show-time style="width: 360px" />
          </a-form-item>
          <a-form-item label="模块">
            <a-input v-model="moduleFilter" placeholder="模块名" allow-clear style="width: 160px" />
          </a-form-item>
          <a-form-item label="状态">
            <a-select v-model="statusFilter" placeholder="全部" allow-clear style="width: 120px">
              <a-option value="success">成功</a-option>
              <a-option value="failed">失败</a-option>
            </a-select>
          </a-form-item>
          <a-form-item label="关键词">
            <a-input v-model="keyword" placeholder="路径/IP/操作" style="width: 180px" allow-clear @press-enter="handleSearch" />
          </a-form-item>
          <a-form-item>
            <a-space>
              <a-button type="primary" @click="handleSearch">查询</a-button>
              <a-button @click="handleReset">重置</a-button>
            </a-space>
          </a-form-item>
        </a-form>
      </div>

      <div class="report-panel">
        <a-table
          class="operation-log-table"
          :data="safeRows"
          :loading="loading"
          :pagination="false"
          row-key="_id"
          :bordered="{ cell: true }"
          :scroll="{ x: 1500 }"
          stripe
          size="small"
        >
          <template #columns>
            <a-table-column title="时间" data-index="_time_text" :width="160">
              <template #cell="{ record }">
                <span class="cell-mono">{{ record._time_text || '--' }}</span>
              </template>
            </a-table-column>

            <a-table-column title="用户" data-index="_operator" :width="100">
              <template #cell="{ record }">
                <span>{{ record._operator }}</span>
              </template>
            </a-table-column>

            <a-table-column title="模块" data-index="_module" :width="120">
              <template #cell="{ record }">
                <a-tag size="small">{{ record._module }}</a-tag>
              </template>
            </a-table-column>

            <a-table-column title="操作" data-index="_action" :width="180" ellipsis tooltip>
              <template #cell="{ record }">
                <span>{{ record._action }}</span>
              </template>
            </a-table-column>

            <a-table-column title="状态" data-index="_status" :width="90">
              <template #cell="{ record }">
                <a-tag :color="statusColor(record)" size="small">
                  {{ statusText(record) }}
                </a-tag>
              </template>
            </a-table-column>

            <a-table-column title="方法" data-index="_method" :width="80">
              <template #cell="{ record }">
                <span class="cell-mono">{{ record._method }}</span>
              </template>
            </a-table-column>

            <a-table-column title="请求路径" data-index="_path" :width="280" ellipsis tooltip>
              <template #cell="{ record }">
                <a-tooltip :content="record._path">
                  <span class="cell-mono ellipsis">{{ record._path }}</span>
                </a-tooltip>
              </template>
            </a-table-column>

            <a-table-column title="IP" data-index="_ip" :width="130">
              <template #cell="{ record }">
                <span class="cell-mono">{{ record._ip }}</span>
              </template>
            </a-table-column>

            <a-table-column title="耗时" data-index="_duration_text" :width="100">
              <template #cell="{ record }">
                <span class="cell-mono">{{ record._duration_text }}</span>
              </template>
            </a-table-column>

            <a-table-column title="说明" data-index="_message" :width="280" ellipsis tooltip>
              <template #cell="{ record }">
                <a-tooltip :content="record._message">
                  <span class="ellipsis">{{ record._message }}</span>
                </a-tooltip>
              </template>
            </a-table-column>
          </template>
        </a-table>

        <a-empty v-if="!loading && safeRows.length === 0" description="暂无真实操作日志" />

        <div class="pagination-wrap">
          <a-pagination
            v-model:current="page"
            v-model:page-size="pageSize"
            :total="total"
            :page-size-options="[20, 50, 100, 200]"
            show-total
            show-page-size
            @change="handlePageChange"
            @page-size-change="handlePageSizeChange"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.report-page {
  height: 100%;
  min-height: 0;
  box-sizing: border-box;
  overflow: hidden auto;
  background: #f5f7fb;
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
.report-panel {
  padding: 20px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 2px 8px rgb(0 0 0 / 6%);
}
.report-header-card {
  padding: 18px 22px;
}
.page-title {
  margin: 0 0 6px;
  font-size: 18px;
  font-weight: 600;
  color: #1d2129;
}
.page-subtitle {
  color: #86909c;
  font-size: 12px;
}
.pagination-wrap {
  margin-top: 14px;
  display: flex;
  justify-content: flex-end;
}
.operation-log-table {
  width: 100%;
}
.cell-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  color: #1d2129;
}
.ellipsis {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}
</style>