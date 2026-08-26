<script setup lang="ts">
/**
 * AlgorithmTaskMonitor.vue
 *
 * 系统运维 -> 算法任务监控。
 * 展示算法队列、各阶段耗时、错误日志、结果文件、模型运行状态。
 * 仅供运维使用；普通用户页面继续显示"正在处理中，请稍后刷新"提示。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Message, Modal } from '@arco-design/web-vue'
import dayjs from 'dayjs'
import {
  getOpsAlgorithmTaskDetail,
  getOpsAlgorithmTaskSummary,
  getOpsAlgorithmRuntimeStatus,
  listOpsAlgorithmTasks,
  markOpsAlgorithmTaskFailed,
  retryOpsAlgorithmTask,
  type OpsAlgorithmRuntimeData,
  type OpsAlgorithmTaskDetail,
  type OpsAlgorithmTaskItem,
  type OpsAlgorithmTaskSummary,
  type OpsAlgorithmTaskStage,
} from '@/api/opsAlgorithmTasks'
import { logUserAction } from '@/utils/operationLogger'

const router = useRouter()

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const todayStr = dayjs().format('YYYY-MM-DD')

const loading = ref(false)
const detailLoading = ref(false)
const autoRefresh = ref(false)
let refreshTimer: number | null = null

const summary = ref<OpsAlgorithmTaskSummary | null>(null)

const filterForm = reactive({
  date: todayStr,
  videoId: '',
  sourceType: '' as '' | 'rtsp' | 'upload',
  status: '' as
    | ''
    | 'queued'
    | 'recording'
    | 'processing'
    | 'done'
    | 'failed'
    | 'stuck',
  failedStage: '' as '' | OpsAlgorithmTaskStage['key'],
})

const items = ref<OpsAlgorithmTaskItem[]>([])
const total = ref(0)
const pagination = reactive({
  current: 1,
  pageSize: 20,
  showTotal: true,
  showJumper: true,
  showPageSize: true,
  pageSizeOptions: [10, 20, 50, 100],
})

// detail drawer
const detailVisible = ref(false)
const detail = ref<OpsAlgorithmTaskDetail | null>(null)
const detailTab = ref<'basic' | 'stages' | 'files' | 'log' | 'queue'>('basic')
const markFailedVisible = ref(false)
const markFailedReason = ref('')

// 模型运行详情弹窗
const modelDetailVisible = ref(false)
const modelDetail = ref<OpsAlgorithmRuntimeData | null>(null)

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STATUS_COLOR: Record<string, string> = {
  done: 'green',
  failed: 'red',
  processing: 'blue',
  queued: 'orange',
  recording: 'purple',
  stuck: 'magenta',
  unknown: 'gray',
}

const STAGE_NAME: Record<string, string> = {
  calibrate: '标定检查',
  video_to_frames: '视频抽帧',
  adjust_batch: '视角矫正',
  grounded_sam: '鱼体检测分割',
  filter_complete_fish: '完整鱼筛选',
  fish_condition: '五维指标计算',
  publish_results: '结果发布',
  // 兼容旧 key
  crop_fish: '鱼体检测裁剪',
  correct_image: '视角矫正',
  import_db: '结果入库',
  report: '报告生成',
}

const FILE_LABEL: Record<string, string> = {
  playable_h264: '可播放 H.264',
  playable_mp4: '原始 MP4',
  job_root: 'job 目录',
  pipeline_state_json: 'pipeline_state.json',
  pipeline_result_json: 'pipeline_result.json',
  error_conclusion_txt: 'error_conclusion.txt',
  measurement_csv: '五维测量 CSV',
  grounded_sam_mp4: 'grounded_sam.mp4',
}

const STATUS_OPTIONS = [
  { label: '全部', value: '' },
  { label: '排队中', value: 'queued' },
  { label: '录制中', value: 'recording' },
  { label: '处理中', value: 'processing' },
  { label: '疑似卡住', value: 'stuck' },
  { label: '已完成', value: 'done' },
  { label: '失败', value: 'failed' },
]

const SOURCE_OPTIONS = [
  { label: '全部', value: '' },
  { label: '摄像头录制 rtsp', value: 'rtsp' },
  { label: '上传视频 upload', value: 'upload' },
]

const STAGE_OPTIONS = [
  { label: '全部', value: '' },
  { label: '标定检查 calibrate', value: 'calibrate' },
  { label: '视频抽帧 video_to_frames', value: 'video_to_frames' },
  { label: '视角矫正 adjust_batch', value: 'adjust_batch' },
  { label: '鱼体检测分割 grounded_sam', value: 'grounded_sam' },
  { label: '完整鱼筛选 filter_complete_fish', value: 'filter_complete_fish' },
  { label: '五维指标 fish_condition', value: 'fish_condition' },
  { label: '结果发布 publish_results', value: 'publish_results' },
  // 兼容旧 key
  { label: '鱼体裁剪 crop_fish (旧)', value: 'crop_fish' },
  { label: '视角矫正 correct_image (旧)', value: 'correct_image' },
  { label: '结果入库 import_db (旧)', value: 'import_db' },
  { label: '报告生成 report (旧)', value: 'report' },
]

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function stageName(key: string | null | undefined): string {
  if (!key) return '--'
  return STAGE_NAME[key] || key
}

function statusColor(s: string | null | undefined): string {
  return STATUS_COLOR[(s || '').toLowerCase()] || 'gray'
}

function fmtTime(s: string | null | undefined): string {
  if (!s) return '--'
  return dayjs(s).format('YYYY-MM-DD HH:mm:ss')
}

function fmtElapsed(seconds: number | null | undefined): string {
  if (seconds == null) return '--'
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m} 分 ${s} 秒`
}

function progressColor(record: OpsAlgorithmTaskItem): string {
  if (record.display_status === 'failed' || record.display_status === 'stuck') {
    return '#f53f3f'
  }
  if (record.display_status === 'done') {
    return '#00b42a'
  }
  return '#165dff'
}

// ---------------------------------------------------------------------------
// Data load
// ---------------------------------------------------------------------------

async function loadSummary() {
  try {
    const resp = await getOpsAlgorithmTaskSummary()
    if (resp.data?.ok && resp.data?.data) {
      summary.value = resp.data.data as OpsAlgorithmTaskSummary
    } else {
      summary.value = null
    }
  } catch (err) {
    console.error('[AlgorithmTaskMonitor] load summary failed', err)
    summary.value = null
  }
}

async function loadList() {
  loading.value = true
  try {
    const params: Record<string, string | number> = {
      limit: pagination.pageSize,
    }
    if (filterForm.date) params.date = filterForm.date
    if (filterForm.videoId.trim()) params.keyword = filterForm.videoId.trim()
    if (filterForm.sourceType) params.source_type = filterForm.sourceType
    if (filterForm.status) params.status = filterForm.status
    // failed_stage 暂时没在后端加参数过滤，先用前端过滤
    const resp = await listOpsAlgorithmTasks(params)
    if (resp.data?.ok) {
      let raw = resp.data.items || []
      if (filterForm.failedStage) {
        raw = raw.filter(
          (it) => (it.failed_stage || '') === filterForm.failedStage,
        )
      }
      if (filterForm.videoId.trim()) {
        const k = filterForm.videoId.trim().toLowerCase()
        raw = raw.filter((it) =>
          (it.video_id || '').toLowerCase().includes(k),
        )
      }
      items.value = raw
      total.value = raw.length
    } else {
      items.value = []
      total.value = 0
    }
  } catch (err) {
    console.error('[AlgorithmTaskMonitor] load list failed', err)
    items.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function getLogSource(type: 'algorithm_cli' | 'pipeline_state' | 'worker') {
  return (detail.value?.log_sources || []).find((s) => s.type === type) || null
}

function resetFilters() {
  filterForm.date = todayStr
  filterForm.videoId = ''
  filterForm.sourceType = ''
  filterForm.status = ''
  filterForm.failedStage = ''
  pagination.current = 1
  void loadList()
}

async function loadAll() {
  logUserAction({
    module: '系统运维',
    action: 'refresh_algorithm_tasks',
    targetType: 'page',
    targetId: 'algorithm-tasks',
    message: '刷新算法任务监控',
  })
  await Promise.all([loadSummary(), loadList()])
}

// ---------------------------------------------------------------------------
// Detail drawer
// ---------------------------------------------------------------------------

async function openDetail(record: OpsAlgorithmTaskItem) {
  detailVisible.value = true
  detailTab.value = 'basic'
  detailLoading.value = true
  detail.value = null
  logUserAction({
    module: '系统运维',
    action: 'open_algorithm_task_detail',
    targetType: 'video',
    targetId: record.video_id,
    message: '打开算法任务详情',
  })
  try {
    const resp = await getOpsAlgorithmTaskDetail(record.video_id)
    if (resp.data?.ok && resp.data?.data) {
      detail.value = resp.data.data as OpsAlgorithmTaskDetail
    } else {
      Message.error('加载任务详情失败')
    }
  } catch (err) {
    Message.error('加载任务详情失败：' + (err as Error).message)
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  detailVisible.value = false
  detail.value = null
}

async function openModelDetail() {
  modelDetailVisible.value = true
  modelDetail.value = null
  try {
    const resp = await getOpsAlgorithmRuntimeStatus()
    if (resp.data?.ok && resp.data?.data) {
      modelDetail.value = resp.data.data as OpsAlgorithmRuntimeData
    } else {
      Message.error('加载模型运行状态失败')
    }
  } catch (err) {
    Message.error('加载模型运行状态失败：' + (err as Error).message)
  }
}

// ---------------------------------------------------------------------------
// Mark failed
// ---------------------------------------------------------------------------

function openMarkFailedDialog(record: OpsAlgorithmTaskItem) {
  detail.value = {
    video: { video_id: record.video_id },
    queue: {} as any,
    files: {} as any,
    pipeline_state: { raw: null, parse_error: null },
    pipeline_result: { raw: null, parse_error: null },
    stages: [],
    display_status: record.display_status,
    is_stuck: record.is_stuck,
    stuck_reason: record.stuck_reason,
    eta_seconds: record.eta_seconds,
    eta_text: record.eta_text,
    eta_confidence: record.eta_confidence,
    latest_log: {
      path: null,
      exists: false,
      returncode: null,
      command: null,
      stdout_tail: '',
      stderr_tail: '',
      mtime: null,
      parse_error: null,
    },
    log_sources: [],
    diagnosis: { is_stuck: false, reason: '', suggestion: '' },
    model_runtime: {} as any,
  }
  markFailedReason.value = '运维手动标记失败'
  markFailedVisible.value = true
}

async function submitMarkFailed() {
  if (!detail.value) return
  const videoId = (detail.value.video as { video_id?: string }).video_id
  if (!videoId) return
  try {
    const resp = await markOpsAlgorithmTaskFailed(
      videoId,
      markFailedReason.value || '运维手动标记失败',
    )
    if (resp.data?.ok) {
      Message.success('已标记为失败')
      markFailedVisible.value = false
      await loadAll()
    } else {
      Message.error('标记失败失败')
    }
  } catch (err: any) {
    const detail2 = err?.response?.data?.detail
    Message.error(typeof detail2 === 'string' ? detail2 : err?.message || '标记失败失败')
  }
}

// ---------------------------------------------------------------------------
// Retry
// ---------------------------------------------------------------------------

function confirmRetry(record: OpsAlgorithmTaskItem) {
  Modal.confirm({
    title: '重新入队完整性设置',
    content:
      '点击“确认”表示需要检测完整性；点击“取消”表示本次不检测完整性并使用 --force-all-complete。',
    okText: '需要检测',
    cancelText: '不检测',
    onOk: () => {
      void retryWithIntegrity(record, false)
    },
    onCancel: () => {
      void retryWithIntegrity(record, true)
    },
  })
}

async function retryWithIntegrity(record: OpsAlgorithmTaskItem, forceAllComplete: boolean): Promise<void> {
      void (async () => {
        try {
          const resp = await retryOpsAlgorithmTask(record.video_id, false, forceAllComplete)
          if (resp.data?.ok) {
            Message.success('已重新入队，job_id=' + (resp.data.job_id || '--'))
            // 该任务已变为 queued。若当前状态筛选会把它过滤掉，
            // 自动切换到 "全部" 避免表格突然看起来 "单元格消失"。
            if (filterForm.status && filterForm.status !== 'queued') {
              filterForm.status = ''
              pagination.current = 1
              Message.info('该任务已变为排队中，已自动切换到全部状态。')
            }
            await loadAll()
          } else {
            Message.error('重新入队失败')
          }
        } catch (err: any) {
          const detail2 = err?.response?.data?.detail
          Message.error(
            typeof detail2 === 'string' ? detail2 : err?.message || '重新入队失败',
          )
        }
      })()
}

// ---------------------------------------------------------------------------
// Jump to dashboard
// ---------------------------------------------------------------------------

function jumpToDashboard(record: OpsAlgorithmTaskItem) {
  router.push({
    path: '/',
    query: { mode: 'result', video_id: record.video_id },
  })
}

// ---------------------------------------------------------------------------
// Auto refresh
// ---------------------------------------------------------------------------

function clearRefreshTimer() {
  if (refreshTimer != null) {
    window.clearInterval(refreshTimer)
    refreshTimer = null
  }
}

function setupRefreshTimer() {
  clearRefreshTimer()
  if (!autoRefresh.value) return
  refreshTimer = window.setInterval(() => {
    void loadAll()
  }, 5000)
}

watch(autoRefresh, () => {
  setupRefreshTimer()
})

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

onMounted(async () => {
  await loadAll()
})

onBeforeUnmount(() => {
  clearRefreshTimer()
})
</script>

<template>
  <div class="algorithm-task-monitor">
    <div class="page-head">
      <div>
        <h2>算法任务监控</h2>
        <p class="page-sub">
          查看算法队列、处理阶段、耗时、错误日志和结果文件状态。该页面仅供运维排障使用。
        </p>
      </div>
      <div class="page-head-actions">
        <a-space :size="8">
          <a-switch v-model="autoRefresh" />
          <span>自动刷新（5s）</span>
          <a-button type="primary" @click="loadAll">刷新</a-button>
        </a-space>
      </div>
    </div>

    <!-- 顶部概览卡片 -->
    <div class="summary-grid">
      <a-card class="summary-card">
        <div class="summary-label">今日任务</div>
        <div class="summary-value">{{ summary?.today_total ?? '--' }}</div>
      </a-card>
      <a-card class="summary-card">
        <div class="summary-label">排队中</div>
        <div class="summary-value" :style="{ color: '#ff7d00' }">
          {{ summary?.queued ?? '--' }}
        </div>
      </a-card>
      <a-card class="summary-card">
        <div class="summary-label">处理中</div>
        <div class="summary-value" :style="{ color: '#165dff' }">
          {{ summary?.processing ?? '--' }}
        </div>
      </a-card>
      <a-card class="summary-card">
        <div class="summary-label">已完成</div>
        <div class="summary-value" :style="{ color: '#00b42a' }">
          {{ summary?.done ?? '--' }}
        </div>
      </a-card>
      <a-card class="summary-card">
        <div class="summary-label">失败</div>
        <div class="summary-value" :style="{ color: '#f53f3f' }">
          {{ summary?.failed ?? '--' }}
        </div>
      </a-card>
      <a-card class="summary-card">
        <div class="summary-label">疑似卡住</div>
        <div class="summary-value" :style="{ color: '#c41d7f' }">
          {{ summary?.stuck ?? '--' }}
        </div>
      </a-card>
      <a-card class="summary-card summary-card-wide">
        <div class="summary-label">Worker 队列</div>
        <div v-if="summary?.worker" class="summary-worker">
          <a-tag :color="summary.worker.redis_ok ? 'green' : 'red'">
            redis {{ summary.worker.redis_ok ? 'ok' : 'fail' }}
          </a-tag>
          <a-tag>queue: {{ summary.worker.queue || '--' }}</a-tag>
          <a-tag color="orange">queued {{ summary.worker.queued_jobs }}</a-tag>
          <a-tag color="blue">started {{ summary.worker.started_jobs }}</a-tag>
          <a-tag color="green">finished {{ summary.worker.finished_jobs }}</a-tag>
          <a-tag color="red">failed {{ summary.worker.failed_jobs }}</a-tag>
        </div>
        <div
          v-if="summary?.worker && !summary.worker.redis_ok"
          class="summary-model-hint"
        >
          队列状态读取失败：{{ summary.worker.error_message || 'redis 不可达' }}
        </div>
        <div v-else>--</div>
      </a-card>
      <a-card class="summary-card summary-card-wide">
        <div class="summary-label-row">
          <span class="summary-label">模型运行状态</span>
          <a-button
            size="mini"
            type="text"
            @click="openModelDetail"
          >查看模型文件</a-button>
        </div>
        <div v-if="summary?.model_runtime" class="summary-model">
          <a-tag :color="summary.model_runtime.mode === 'cli' ? 'orange' : 'green'">
            mode: {{ summary.model_runtime.mode }}
          </a-tag>
          <a-tag>preload_supported: {{ summary.model_runtime.preload_supported }}</a-tag>
          <a-tag>loaded: {{ summary.model_runtime.loaded }}</a-tag>
          <a-tag v-if="summary.model_runtime.gpu?.available" color="arcoblue">
            GPU: {{ summary.model_runtime.gpu?.name }}
            ({{ summary.model_runtime.gpu?.memory_used_mb ?? '?' }} /
            {{ summary.model_runtime.gpu?.memory_total_mb ?? '?' }} MB)
          </a-tag>
          <a-tag v-else color="gray">GPU: n/a</a-tag>
          <a-tag v-if="(summary.model_runtime as unknown as OpsAlgorithmRuntimeData).torch?.installed" color="purple">
            torch {{ (summary.model_runtime as unknown as OpsAlgorithmRuntimeData).torch?.version }}
          </a-tag>
          <a-tag v-if="(summary.model_runtime as unknown as OpsAlgorithmRuntimeData).torch?.cuda_available" color="arcoblue">
            CUDA {{ (summary.model_runtime as unknown as OpsAlgorithmRuntimeData).torch?.cuda_version }}
          </a-tag>
          <a-tag color="green">
            模型文件 {{ (summary.model_runtime as unknown as OpsAlgorithmRuntimeData).models_summary?.found ?? '?' }}
            / {{ (summary.model_runtime as unknown as OpsAlgorithmRuntimeData).models_summary?.total ?? '?' }}
          </a-tag>
        </div>
        <div
          v-if="summary?.model_runtime?.mode === 'cli'"
          class="summary-model-hint"
        >
          当前为 CLI 子进程模式，模型会随任务进程加载和释放；如需模型常驻显存，需要后续改造成常驻算法服务。
        </div>
      </a-card>
    </div>

    <!-- 筛选区 -->
    <a-card class="filter-card">
      <a-form :model="filterForm" layout="inline">
        <a-form-item label="日期">
          <a-date-picker
            v-model="filterForm.date"
            value-format="YYYY-MM-DD"
            style="width: 160px"
            allow-clear
          />
        </a-form-item>
        <a-form-item label="视频 ID">
          <a-input
            v-model="filterForm.videoId"
            placeholder="模糊搜索"
            allow-clear
            style="width: 220px"
          />
        </a-form-item>
        <a-form-item label="来源">
          <a-select
            v-model="filterForm.sourceType"
            :options="SOURCE_OPTIONS"
            placeholder="全部"
            style="width: 160px"
          />
        </a-form-item>
        <a-form-item label="状态">
          <a-select
            v-model="filterForm.status"
            :options="STATUS_OPTIONS"
            placeholder="全部"
            style="width: 140px"
          />
        </a-form-item>
        <a-form-item label="失败阶段">
          <a-select
            v-model="filterForm.failedStage"
            :options="STAGE_OPTIONS"
            placeholder="全部"
            style="width: 200px"
          />
        </a-form-item>
        <a-form-item>
          <a-space :size="8">
            <a-button type="primary" @click="loadList">查询</a-button>
            <a-button @click="resetFilters">重置</a-button>
          </a-space>
        </a-form-item>
      </a-form>
    </a-card>

    <!-- 任务表格 -->
    <a-card>
      <a-table
        :data="items"
        :loading="loading"
        :pagination="pagination"
        :bordered="false"
        row-key="video_id"
        :stripe="true"
      >
        <template #columns>
          <a-table-column title="视频 ID" data-index="video_id" :width="220">
            <template #cell="{ record }">
              <a-link @click="openDetail(record)">{{ record.video_id }}</a-link>
            </template>
          </a-table-column>
          <a-table-column title="来源" :width="100">
            <template #cell="{ record }">
              <a-tag
                :color="record.source_type === 'rtsp' ? 'purple' : 'arcoblue'"
              >
                {{ record.source_type }}
              </a-tag>
            </template>
          </a-table-column>
          <a-table-column title="状态" :width="100">
            <template #cell="{ record }">
              <a-tag :color="statusColor(record.display_status)">
                {{ record.display_status }}
              </a-tag>
            </template>
          </a-table-column>
          <a-table-column title="当前阶段" :width="130">
            <template #cell="{ record }">
              <span v-if="record.current_stage">{{ stageName(record.current_stage) }}</span>
              <span v-else>--</span>
            </template>
          </a-table-column>
          <a-table-column title="进度" :width="120">
            <template #cell="{ record }">
              <a-progress
                :percent="
                  record.display_status === 'done'
                    ? 100
                    : Math.min(99, Math.max(0, record.progress_percent || 0))
                "
                :show-text="true"
                :color="progressColor(record)"
                :status="
                  record.display_status === 'failed' ||
                  record.display_status === 'stuck'
                    ? 'danger'
                    : record.display_status === 'done'
                    ? 'success'
                    : 'normal'
                "
              />
            </template>
          </a-table-column>
          <a-table-column title="已耗时" :width="100">
            <template #cell="{ record }">
              {{ fmtElapsed(record.elapsed_seconds) }}
            </template>
          </a-table-column>
          <a-table-column title="视频时长" :width="110">
            <template #cell="{ record }">
              {{ fmtElapsed(record.video_duration_seconds) }}
            </template>
          </a-table-column>
          <a-table-column title="预计剩余" :width="120">
            <template #cell="{ record }">
              <span v-if="record.eta_seconds != null">
                {{ record.eta_text }}
                <span class="eta-conf"> ({{ record.eta_confidence }})</span>
              </span>
              <span v-else>--</span>
            </template>
          </a-table-column>
          <a-table-column title="鱼数量" :width="170">
            <template #cell="{ record }">
              <div v-if="(record.total_fish || 0) > 0 || (record.processed_fish || 0) > 0">
                <div>总数 {{ record.total_fish || 0 }}</div>
                <div>完整 {{ record.complete_fish || 0 }} ｜ 不完整 {{ record.incomplete_fish || 0 }} ｜ 失败 {{ record.failed_fish || 0 }}</div>
                <div v-if="record.damaged_fish">破损 {{ record.damaged_fish }}</div>
              </div>
              <span v-else>--</span>
            </template>
          </a-table-column>
          <a-table-column title="结果文件" :width="170">
            <template #cell="{ record }">
              <a-space :size="4" wrap>
                <a-tag
                  v-if="record.has_pipeline_result"
                  color="green"
                  size="small"
                >result</a-tag>
                <a-tag
                  v-if="record.has_grounded_video"
                  color="green"
                  size="small"
                >grounded</a-tag>
                <a-tag
                  v-if="record.has_error_conclusion"
                  color="orange"
                  size="small"
                >conclusion</a-tag>
                <a-tag
                  v-if="record.latest_log_path"
                  color="gray"
                  size="small"
                >log</a-tag>
                <span v-if="
                  !record.has_pipeline_result &&
                  !record.has_grounded_video &&
                  !record.has_error_conclusion &&
                  !record.latest_log_path
                " class="text-muted">--</span>
              </a-space>
            </template>
          </a-table-column>
          <a-table-column title="最新错误" :width="220">
            <template #cell="{ record }">
              <div class="error-cell">
                {{ record.last_error || '--' }}
              </div>
            </template>
          </a-table-column>
          <a-table-column title="更新时间" :width="160">
            <template #cell="{ record }">
              {{ fmtTime(record.started_at || record.created_at) }}
            </template>
          </a-table-column>
          <a-table-column title="操作" :width="240" fixed="right">
            <template #cell="{ record }">
              <a-space :size="6" wrap>
                <a-button
                  type="text"
                  size="mini"
                  @click="openDetail(record)"
                >详情</a-button>
                <a-button
                  v-if="record.has_pipeline_result || record.display_status === 'done'"
                  type="text"
                  size="mini"
                  @click="jumpToDashboard(record)"
                >跳转大屏</a-button>
                <a-button
                  v-if="
                    record.display_status === 'failed' ||
                    record.display_status === 'stuck' ||
                    record.display_status === 'done' ||
                    record.is_stuck
                  "
                  type="text"
                  size="mini"
                  @click="confirmRetry(record)"
                >重新入队</a-button>
                <a-button
                  v-if="
                    ['processing', 'queued', 'recording', 'stuck'].includes(
                      record.display_status,
                    )
                  "
                  type="text"
                  size="mini"
                  status="danger"
                  @click="openMarkFailedDialog(record)"
                >标记失败</a-button>
              </a-space>
            </template>
          </a-table-column>
        </template>
      </a-table>
    </a-card>

    <!-- 详情抽屉 -->
    <a-drawer
      v-model:visible="detailVisible"
      :width="860"
      title="算法任务详情"
      :footer="false"
      :unmount-on-close="true"
      @cancel="closeDetail"
    >
      <a-spin v-if="detailLoading" :loading="true" tip="加载中...">
        <div style="height: 200px" />
      </a-spin>
      <div v-else-if="detail">
        <a-tabs v-model:active-key="detailTab" type="rounded">
          <a-tab-pane key="basic" title="基本信息">
            <a-descriptions
              v-if="detail.video"
              :data="[
                { label: '视频 ID', value: (detail.video as { video_id?: string }).video_id },
                { label: '来源', value: (detail.video as { source_type?: string }).source_type },
                { label: 'DB 状态', value: (detail.video as { status?: string }).status },
                { label: '显示状态', value: detail.display_status },
                {
                  label: '当前阶段',
                  value: detail.current_stage
                    || detail.stages?.find((s) => s.status === 'running')?.key
                    || '--',
                },
                { label: '创建时间', value: fmtTime((detail.video as { created_at?: string }).created_at) },
                { label: '开始时间', value: fmtTime((detail.video as { started_at?: string }).started_at) },
                { label: '结束时间', value: fmtTime((detail.video as { finished_at?: string }).finished_at) },
                {
                  label: '失败阶段',
                  value: detail.failed_stage
                    || (detail.is_stuck ? '未失败，任务疑似卡住' : '--'),
                },
                {
                  label: '最近活动',
                  value: detail.last_activity_at
                    ? `${fmtTime(detail.last_activity_at)}${detail.last_activity_source ? ` (${detail.last_activity_source})` : ''}`
                    : '--',
                },
                { label: '总鱼数', value: (detail.video as { total_fish?: number }).total_fish ?? 0 },
                { label: '完整鱼', value: (detail.video as { complete_fish?: number }).complete_fish ?? 0 },
                { label: '不完整', value: (detail.video as { incomplete_fish?: number }).incomplete_fish ?? 0 },
                { label: '失败', value: (detail.video as { failed_fish?: number }).failed_fish ?? 0 },
                { label: '破损', value: (detail.video as { damaged_fish?: number }).damaged_fish ?? 0 },
                { label: '已处理', value: (detail.video as { processed_fish?: number }).processed_fish ?? 0 },
                { label: 'source_path', value: (detail.video as { source_path?: string }).source_path },
              ]"
              :column="2"
              :align="false"
            />
            <div v-if="(detail.video as { last_error?: string }).last_error" class="error-block">
              <div class="error-label">last_error</div>
              <pre class="error-pre">{{ (detail.video as { last_error?: string }).last_error }}</pre>
            </div>
            <a-alert
              v-if="detail.diagnosis?.reason"
              type="error"
              style="margin-top: 12px"
            >
              <div slot="title">诊断：{{ detail.diagnosis?.reason }}</div>
              <div style="margin-top: 6px; color: #86909c">
                建议：{{ detail.diagnosis?.suggestion || '请结合日志排查。' }}
              </div>
            </a-alert>
          </a-tab-pane>

          <a-tab-pane key="stages" title="阶段进度">
            <a-timeline>
              <a-timeline-item
                v-for="s in (detail.stages || [])"
                :key="s.key"
                :label="s.name"
                :dot-color="
                  s.status === 'done'
                    ? 'green'
                    : s.status === 'running'
                    ? 'blue'
                    : s.status === 'failed'
                    ? 'red'
                    : s.status === 'skipped'
                    ? 'gray'
                    : 'gray'
                "
              >
                <div class="stage-row">
                  <a-tag
                    :color="
                      s.status === 'done'
                        ? 'green'
                        : s.status === 'running'
                        ? 'blue'
                        : s.status === 'failed'
                        ? 'red'
                        : 'gray'
                    "
                  >
                    {{ s.status }}
                  </a-tag>
                  <span class="stage-elapsed"
                    >耗时: {{ fmtElapsed(s.elapsed_seconds) }}</span
                  >
                  <span class="stage-count" v-if="s.input_count != null"
                    >输入: {{ s.input_count }}</span
                  >
                  <span class="stage-count" v-if="s.output_count != null"
                    >输出: {{ s.output_count }}</span
                  >
                </div>
                <pre v-if="s.error_message" class="error-pre">{{ s.error_message }}</pre>
              </a-timeline-item>
            </a-timeline>
          </a-tab-pane>

          <a-tab-pane key="files" title="文件检查">
            <a-table
              :data="Object.entries(detail.files || {}).map(([k, v]) => ({ key: k, ...(v as unknown as Record<string, unknown>) }))"
              :pagination="false"
              row-key="key"
              :bordered="false"
            >
              <template #columns>
                <a-table-column title="文件项" :width="160">
                  <template #cell="{ record }">
                    {{ FILE_LABEL[(record as { key: string }).key] || (record as { key: string }).key }}
                  </template>
                </a-table-column>
                <a-table-column title="存在" :width="80">
                  <template #cell="{ record }">
                    <a-tag :color="(record as { exists: boolean }).exists ? 'green' : 'red'">
                      {{ (record as { exists: boolean }).exists ? '是' : '否' }}
                    </a-tag>
                  </template>
                </a-table-column>
                <a-table-column title="路径 / 大小" :width="380">
                  <template #cell="{ record }">
                    <div class="file-path">
                      {{ (record as { path?: string }).path || '--' }}
                    </div>
                    <div class="file-size" v-if="(record as { size?: number }).size">
                      {{ ((record as { size: number }).size / 1024).toFixed(1) }} KB
                    </div>
                    <div
                      v-if="(record as { candidates?: string[] }).candidates && (record as { candidates: string[] }).candidates.length > 1"
                      class="file-candidates"
                    >
                      <a-tooltip>
                        <template #content>
                          <div
                            v-for="c in (record as { candidates: string[] }).candidates"
                            :key="c"
                          >{{ c }}</div>
                        </template>
                        <a-tag size="small">候选 {{ (record as { candidates: string[] }).candidates.length }} 条</a-tag>
                      </a-tooltip>
                    </div>
                  </template>
                </a-table-column>
                <a-table-column title="更新时间" :width="170">
                  <template #cell="{ record }">
                    {{ fmtTime((record as { mtime?: string }).mtime) }}
                  </template>
                </a-table-column>
              </template>
            </a-table>
          </a-tab-pane>

          <a-tab-pane key="log" title="日志">
            <a-alert
              v-if="detail.latest_log?.returncode != null && detail.latest_log.returncode !== 0"
              type="error"
              style="margin-bottom: 12px"
            >
              <div slot="title">
                算法进程异常退出（returncode={{ detail.latest_log.returncode }}），请查看 stderr 和 error_conclusion。
              </div>
            </a-alert>
            <a-alert
              v-else-if="detail.latest_log?.returncode === 0"
              type="success"
              style="margin-bottom: 12px"
            >
              <div slot="title">算法进程正常退出（returncode=0）</div>
            </a-alert>
            <a-alert
              v-else-if="detail.latest_log?.source === 'pipeline_state'"
              type="info"
              style="margin-bottom: 12px"
            >
              <div slot="title">未找到 algorithm_cli 日志，已用 pipeline_state 兜底展示。</div>
            </a-alert>

            <a-tabs default-active-key="algo_cli" size="small">
              <a-tab-pane key="algo_cli" title="算法 CLI 日志">
                <div v-if="getLogSource('algorithm_cli')?.exists">
                  <a-descriptions
                    :data="[
                      { label: '日志文件', value: getLogSource('algorithm_cli')?.path || '--' },
                      { label: 'mtime', value: fmtTime(getLogSource('algorithm_cli')?.mtime) },
                      { label: 'returncode', value: getLogSource('algorithm_cli')?.returncode ?? '--' },
                      { label: 'command', value: getLogSource('algorithm_cli')?.command || '--' },
                    ]"
                    :column="1"
                    :align="false"
                  />
                  <div class="log-section">
                    <div class="log-label">stdout tail</div>
                    <pre class="log-pre">{{ (getLogSource('algorithm_cli')?.stdout_tail as string | undefined) || '(空)' }}</pre>
                  </div>
                  <div class="log-section">
                    <div class="log-label">stderr tail</div>
                    <pre class="log-pre">{{ (getLogSource('algorithm_cli')?.stderr_tail as string | undefined) || '(空)' }}</pre>
                  </div>
                </div>
                <a-empty
                  v-else
                  :description="getLogSource('algorithm_cli')?.message || '未找到 algorithm_cli 日志'"
                />
              </a-tab-pane>

              <a-tab-pane key="pipeline_state" title="pipeline_state.json">
                <div v-if="detail.pipeline_state?.summary?.exists">
                  <a-descriptions
                    :data="[
                      { label: '路径', value: detail.pipeline_state?.summary?.path || '--' },
                      { label: 'mtime', value: fmtTime(detail.pipeline_state?.summary?.mtime) },
                      { label: 'status', value: detail.pipeline_state?.summary?.status || '--' },
                      { label: 'current_stage', value: detail.pipeline_state?.summary?.current_stage || '--' },
                      { label: 'failed_stage', value: detail.pipeline_state?.summary?.failed_stage || '--' },
                      { label: 'error_message', value: detail.pipeline_state?.summary?.error_message || '--' },
                      { label: 'completed_stages', value: (detail.pipeline_state?.summary?.completed_stages || []).join(', ') || '--' },
                      { label: 'command', value: detail.pipeline_state?.summary?.command || '--' },
                    ]"
                    :column="1"
                    :align="false"
                  />
                  <div class="log-section">
                    <div class="log-label">raw_tail (JSON 摘要)</div>
                    <pre class="log-pre">{{ detail.pipeline_state?.summary?.raw_tail || '(空)' }}</pre>
                  </div>
                </div>
                <a-empty
                  v-else
                  :description="detail.pipeline_state?.summary?.message || '未找到 pipeline_state.json'"
                />
              </a-tab-pane>

              <a-tab-pane key="worker" title="Worker 日志">
                <div v-if="getLogSource('worker')?.exists">
                  <a-descriptions
                    :data="[
                      { label: '日志文件', value: getLogSource('worker')?.path || '--' },
                      { label: 'mtime', value: fmtTime(getLogSource('worker')?.mtime) },
                      { label: '匹配行数', value: String(getLogSource('worker')?.matched_lines || 0) },
                    ]"
                    :column="1"
                    :align="false"
                  />
                  <div class="log-section">
                    <div class="log-label">tail (包含该 video_id 的最近上下文)</div>
                    <pre class="log-pre">{{ (getLogSource('worker')?.tail as string | undefined) || '(空)' }}</pre>
                  </div>
                </div>
                <a-empty
                  v-else
                  :description="getLogSource('worker')?.message || '未找到 worker 日志'"
                />
              </a-tab-pane>
            </a-tabs>
          </a-tab-pane>

          <a-tab-pane key="queue" title="队列与模型">
            <a-alert
              v-if="detail.queue && !detail.queue.redis_ok"
              type="error"
              style="margin-bottom: 12px"
            >
              <div slot="title">
                队列状态读取失败：{{ detail.queue.error_message || 'redis 不可达' }}
              </div>
            </a-alert>
            <a-descriptions
              v-if="detail.queue"
              :data="[
                { label: 'redis', value: detail.queue.redis_ok ? 'ok' : 'fail' },
                { label: 'queue', value: detail.queue.queue || '--' },
                { label: 'job_id', value: detail.queue.job_id || '--' },
                { label: 'job_state', value: detail.queue.job_state || '--' },
                { label: 'func_name', value: detail.queue.func_name || '--' },
                { label: 'args_summary', value: detail.queue.args_summary || '--' },
                { label: 'started_at', value: fmtTime(detail.queue.started_at) },
                { label: 'ended_at', value: fmtTime(detail.queue.ended_at) },
                { label: 'error_message', value: detail.queue.error_message || '--' },
                { label: 'exc_info', value: detail.queue.exc_info || '--' },
              ]"
              :column="2"
              :align="false"
            />
            <a-divider />
            <div class="model-section">
              <div class="model-label">模型运行状态</div>
              <a-space :size="6" wrap style="margin-top: 4px">
                <a-tag>mode: {{ detail.model_runtime?.mode }}</a-tag>
                <a-tag>preload_supported: {{ detail.model_runtime?.preload_supported }}</a-tag>
                <a-tag>loaded: {{ detail.model_runtime?.loaded }}</a-tag>
                <a-tag v-if="detail.model_runtime?.gpu?.available" color="arcoblue">
                  GPU: {{ detail.model_runtime?.gpu?.name }}
                </a-tag>
                <a-tag v-else color="gray">GPU: n/a</a-tag>
              </a-space>
              <div class="model-message">{{ detail.model_runtime?.message }}</div>
              <a-alert
                v-if="detail.model_runtime?.mode === 'cli'"
                type="warning"
                style="margin-top: 8px"
              >
                <div slot="title">
                  当前算法为 CLI 子进程模式，模型不会跨任务常驻显存。若需要提高速度，需要后续改造为常驻算法服务。
                </div>
              </a-alert>
            </div>
          </a-tab-pane>
        </a-tabs>
      </div>
    </a-drawer>

    <!-- 标记失败 Modal -->
    <a-modal
      v-model:visible="markFailedVisible"
      title="标记为失败"
      :ok-text="'确认标记'"
      :cancel-text="'取消'"
      @ok="submitMarkFailed"
      @cancel="markFailedVisible = false"
    >
      <a-form-item label="失败原因">
        <a-textarea
          v-model="markFailedReason"
          :auto-size="{ minRows: 3, maxRows: 6 }"
          placeholder="请输入失败原因"
        />
      </a-form-item>
    </a-modal>

    <!-- 模型运行详情 Modal -->
    <a-modal
      v-model:visible="modelDetailVisible"
      :title="'算法模型运行状态'"
      :footer="false"
      :width="880"
      :unmount-on-close="true"
      @cancel="modelDetailVisible = false"
    >
      <div v-if="modelDetail">
        <a-alert
          v-if="modelDetail.mode === 'cli'"
          type="warning"
          style="margin-bottom: 12px"
        >
          <div slot="title">
            当前算法为 CLI 子进程模式，模型不会跨任务常驻显存。
            若需要提高速度，需要后续改造为常驻算法服务（AlgorithmRuntimeService / 独立 algorithm_server）。
          </div>
        </a-alert>

        <a-descriptions
          :data="[
            { label: '当前模式', value: modelDetail.mode },
            {
              label: 'preload_supported',
              value: modelDetail.preload_supported ? '是' : '否',
            },
            { label: 'loaded', value: modelDetail.loaded ? '是' : '否' },
            {
              label: 'CUDA',
              value: modelDetail.torch?.cuda_available
                ? `可用 (CUDA ${modelDetail.torch?.cuda_version ?? '-'})`
                : '不可用',
            },
            {
              label: 'GPU',
              value: modelDetail.gpu?.available
                ? `${modelDetail.gpu.name}（${modelDetail.gpu.memory_used_mb ?? '?'} / ${modelDetail.gpu.memory_total_mb ?? '?'} MB）`
                : 'n/a',
            },
            {
              label: 'torch',
              value: modelDetail.torch?.version ?? (modelDetail.torch?.installed ? 'unknown' : '未安装'),
            },
            { label: 'python', value: modelDetail.python },
            {
              label: '模型文件',
              value: `已找到 ${modelDetail.models_summary?.found ?? '?'} / 总数 ${modelDetail.models_summary?.total ?? '?'}`,
            },
          ]"
          :column="2"
          :align="false"
        />

        <a-divider style="margin: 12px 0" />

        <div class="model-section-title">
          模型文件
          <a-tag
            v-if="modelDetail.models_summary?.missing"
            color="red"
            style="margin-left: 8px"
          >
            缺失 {{ modelDetail.models_summary.missing }} 个
          </a-tag>
        </div>

        <a-table
          :data="(modelDetail.models || [])"
          :pagination="false"
          row-key="key"
          :bordered="false"
          :stripe="true"
        >
          <template #columns>
            <a-table-column title="模型" :width="220">
              <template #cell="{ record }">
                <div class="model-name">{{ (record as { name: string }).name }}</div>
                <div class="model-key">{{ (record as { key: string }).key }}</div>
              </template>
            </a-table-column>
            <a-table-column title="是否存在" :width="90">
              <template #cell="{ record }">
                <a-tag :color="(record as { exists: boolean }).exists ? 'green' : 'red'">
                  {{ (record as { exists: boolean }).exists ? '是' : '否' }}
                </a-tag>
              </template>
            </a-table-column>
            <a-table-column title="大小" :width="100">
              <template #cell="{ record }">
                <span v-if="(record as { size_mb: number | null }).size_mb != null">
                  {{ (record as { size_mb: number }).size_mb }} MB
                </span>
                <span v-else>--</span>
              </template>
            </a-table-column>
            <a-table-column title="路径">
              <template #cell="{ record }">
                <div class="model-path">
                  {{ (record as { path: string }).path }}
                </div>
                <div v-if="(record as { source?: string }).source" class="model-source">
                  来源: {{ (record as { source: string }).source }}
                </div>
              </template>
            </a-table-column>
          </template>
        </a-table>
      </div>
      <a-spin v-else :loading="true" tip="加载中...">
        <div style="height: 200px" />
      </a-spin>
    </a-modal>
  </div>
</template>

<style scoped>
.algorithm-task-monitor {
  width: 100%;
  height: 100%;
  min-height: 0;
  box-sizing: border-box;
  background: #f5f7fb;
  padding: 16px;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  padding: 14px 18px;
  border-radius: 10px;
  box-shadow: 0 2px 8px rgb(0, 0, 0, 4%);
}
.page-head h2 {
  margin: 0;
  font-size: 18px;
  color: #1d2129;
}
.page-sub {
  margin: 4px 0 0;
  font-size: 12px;
  color: #86909c;
}
.summary-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
}
.summary-card {
  background: #fff;
  border-radius: 8px;
  padding: 12px 16px;
  box-shadow: 0 2px 8px rgb(0, 0, 0, 4%);
}
.summary-card-wide {
  grid-column: span 3;
}
.summary-label {
  font-size: 12px;
  color: #86909c;
  margin-bottom: 4px;
}
.summary-label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.summary-label-row .summary-label {
  margin-bottom: 0;
}
.summary-value {
  font-size: 24px;
  font-weight: 700;
  color: #1d2129;
}
.summary-worker,
.summary-model {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}
.summary-model-hint {
  margin-top: 6px;
  font-size: 12px;
  color: #c41d7f;
  background: #fff0f6;
  padding: 4px 8px;
  border-radius: 4px;
}
.filter-card {
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgb(0, 0, 0, 4%);
}
.eta-conf {
  font-size: 11px;
  color: #86909c;
}
.error-cell {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 12px;
  color: #4e5969;
  word-break: break-all;
  white-space: pre-wrap;
}
.text-muted {
  color: #c9cdd4;
  font-size: 12px;
}
.error-block {
  margin-top: 12px;
}
.error-label {
  font-size: 12px;
  color: #86909c;
  margin-bottom: 4px;
}
.model-section-title {
  font-size: 13px;
  font-weight: 700;
  color: #1d2129;
  margin-bottom: 8px;
}
.model-name {
  font-weight: 600;
  color: #1d2129;
}
.model-key {
  font-size: 11px;
  color: #86909c;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  margin-top: 2px;
}
.model-path {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: #1d2129;
  word-break: break-all;
}
.model-source {
  font-size: 11px;
  color: #86909c;
  margin-top: 2px;
}
.error-pre {
  max-height: 160px;
  overflow: auto;
  background: #fff7e8;
  border: 1px solid #ffd591;
  border-radius: 4px;
  padding: 8px 10px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: #4e5969;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
}
.stage-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.stage-elapsed,
.stage-count {
  font-size: 12px;
  color: #4e5969;
}
.file-path {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: #1d2129;
  word-break: break-all;
}
.file-candidates { margin-top: 4px; }
.file-size {
  font-size: 12px;
  color: #86909c;
}
.log-section {
  margin-top: 12px;
}
.log-label {
  font-size: 12px;
  color: #86909c;
  margin-bottom: 4px;
}
.log-pre {
  max-height: 360px;
  overflow: auto;
  background: #1e1e1e;
  color: #e6e6e6;
  border-radius: 4px;
  padding: 10px 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
}
.model-section {
  margin-top: 8px;
}
.model-label {
  font-size: 12px;
  color: #86909c;
}
.model-message {
  margin-top: 4px;
  font-size: 12px;
  color: #4e5969;
  line-height: 1.5;
}
</style>
