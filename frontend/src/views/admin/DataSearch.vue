<template>
  <div class="data-search-page">
    <!-- 面包屑 -->
    <div class="page-breadcrumb">
      <a-breadcrumb>
        <a-breadcrumb-item>数据管理</a-breadcrumb-item>
        <a-breadcrumb-item>数据检索</a-breadcrumb-item>
      </a-breadcrumb>
    </div>

    <div class="page-header">
      <h2>数据检索</h2>
      <p class="page-subtitle">
        默认按当前 <code>video_id</code> 加载该视频的鱼列表。
        URL 中带 <code>?video_id=xxx</code> 会自动跳转 / 高亮该视频。
      </p>
    </div>

    <!-- 视频 ID 选择（主流程入口） -->
    <div class="filter-section">
      <a-row :gutter="16">
        <a-col :span="12">
          <div class="filter-item">
            <label>当前 video_id</label>
            <a-input
              v-model="videoId"
              placeholder="例如：video_20260704_143213_6a2acb0e"
              allow-clear
              @press-enter="handleLoadVideo"
            >
              <template #append>
                <a-button type="primary" @click="handleLoadVideo">加载该视频</a-button>
              </template>
            </a-input>
            <a-alert
              v-if="loadError"
              type="error"
              :show-icon="true"
              style="margin-top: 12px"
            >
              <template #title>加载失败</template>
              {{ loadError }}
            </a-alert>
          </div>
        </a-col>
        <a-col :span="6">
          <div class="filter-item">
            <label>本视频鱼状态</label>
            <a-select v-model="filters.status" placeholder="不限" allow-clear>
              <a-option value="done">完整鱼</a-option>
              <a-option value="incomplete">不完整鱼</a-option>
              <a-option value="failed">失败鱼</a-option>
            </a-select>
          </div>
        </a-col>
        <a-col :span="6">
          <div class="filter-item filter-actions">
            <a-button @click="handleClearVideo">清除当前视频</a-button>
          </div>
        </a-col>
      </a-row>
    </div>

    <!-- 筛选区 -->
    <div class="filter-section">
      <a-row :gutter="16">
        <a-col :span="6">
          <div class="filter-item">
            <label>鱼编号</label>
            <a-input v-model="filters.fishId" placeholder="支持模糊匹配" allow-clear />
          </div>
        </a-col>
        <a-col :span="6">
          <div class="filter-item">
            <label>体长范围 (cm)</label>
            <div class="range-inputs">
              <a-input-number v-model="filters.minLength" :min="0" placeholder="最小" :precision="1" />
              <span class="range-sep">~</span>
              <a-input-number v-model="filters.maxLength" :min="0" placeholder="最大" :precision="1" />
            </div>
          </div>
        </a-col>
        <a-col :span="6">
          <div class="filter-item">
            <label>体重范围 (g)</label>
            <div class="range-inputs">
              <a-input-number v-model="filters.minWeight" :min="0" placeholder="最小" :precision="1" />
              <span class="range-sep">~</span>
              <a-input-number v-model="filters.maxWeight" :min="0" placeholder="最大" :precision="1" />
            </div>
          </div>
        </a-col>
        <a-col :span="6">
          <div class="filter-item">
            <label>肥满度范围</label>
            <div class="range-inputs">
              <a-input-number v-model="filters.minCondition" :min="0" placeholder="最小" :precision="2" />
              <span class="range-sep">~</span>
              <a-input-number v-model="filters.maxCondition" :min="0" placeholder="最大" :precision="2" />
            </div>
          </div>
        </a-col>
      </a-row>
      <a-row :gutter="16" style="margin-top: 12px;">
        <a-col :span="6">
          <div class="filter-item">
            <label>体高范围 (cm)</label>
            <div class="range-inputs">
              <a-input-number v-model="filters.minHeight" :min="0" placeholder="最小" :precision="1" />
              <span class="range-sep">~</span>
              <a-input-number v-model="filters.maxHeight" :min="0" placeholder="最大" :precision="1" />
            </div>
          </div>
        </a-col>
        <a-col :span="6">
          <div class="filter-item">
            <label>体表损伤占比</label>
            <a-select v-model="filters.surfaceDamageLevel" placeholder="不限" allow-clear>
              <a-option value="none">无 (0%)</a-option>
              <a-option value="light">轻微 (0~5%)</a-option>
              <a-option value="medium">中度 (5~20%)</a-option>
              <a-option value="heavy">重度 (>20%)</a-option>
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

    <!-- 操作栏 -->
    <div class="action-bar">
      <div class="action-left">
        <span class="result-count">共 {{ total }} 条记录</span>
        <a-button
          type="primary"
          status="danger"
          :disabled="selectedRows.length === 0"
          @click="handleBatchDelete"
        >
          批量删除 ({{ selectedRows.length }})
        </a-button>
        <a-button @click="showExportModal = true">高级导出</a-button>
      </div>
      <div class="action-right">
        <a-button @click="handleRefresh">刷新</a-button>
      </div>
    </div>

    <!-- 表格 -->
    <div class="table-container">
      <a-table
        :columns="columns"
        :data="tableData"
        :pagination="false"
        :loading="loading"
        :row-selection="rowSelection"
        :column-resizable="true"
        :row-key="'fish_id'"
        stripe
        hoverable
      >
      <template #columns>
        <a-table-column type="selection" :width="50" />
        <a-table-column title="鱼编号" data-index="fish_short_id" :width="160">
          <template #cell="{ record }">
            <a-tooltip :content="record.fish_id">
              <a-link @click="copyToClipboard(record.fish_id)">
                {{ record.fish_short_id || getFishDisplayId(record) || record.fish_id }}
              </a-link>
            </a-tooltip>
          </template>
        </a-table-column>
        <a-table-column title="鱼裁剪图" :width="120">
          <template #cell="{ record }">
            <div class="table-image-with-hint">
              <div class="table-image">
                <img
                  v-if="pickDisplayImage(record)"
                  :src="pickDisplayImage(record)"
                  alt="鱼裁剪图"
                  @error="handleImageError($event)"
                />
                <span v-else style="color:#999">--</span>
              </div>
              <div v-if="imageSourceHint(record)" class="image-source-hint">
                {{ imageSourceHint(record) }}
              </div>
            </div>
          </template>
        </a-table-column>
        <a-table-column title="体长(cm)" data-index="body_length_cm" :width="100">
          <template #cell="{ record }">
            {{ getFishFieldForDisplay(record, 'body_length_cm', 'body_length') }}
          </template>
        </a-table-column>
        <a-table-column title="无尾体长(cm)" data-index="body_length_no_tail_cm" :width="120">
          <template #cell="{ record }">
            {{ getFishFieldForDisplay(record, 'body_length_no_tail_cm', 'body_length_without_tail') }}
          </template>
        </a-table-column>
        <a-table-column title="体高(cm)" data-index="body_height_cm" :width="100">
          <template #cell="{ record }">
            {{ getFishFieldForDisplay(record, 'body_height_cm', 'body_height') }}
          </template>
        </a-table-column>
        <a-table-column title="体重(g)" data-index="pred_weight_g" :width="100">
          <template #cell="{ record }">
            {{ getFishFieldForDisplay(record, 'pred_weight_g', 'body_weight') }}
          </template>
        </a-table-column>
        <a-table-column title="肥满度" data-index="condition_factor" :width="90">
          <template #cell="{ record }">
            {{ record.condition_factor?.toFixed(3) || '-' }}
          </template>
        </a-table-column>
        <a-table-column title="体表损伤占比" :width="120">
          <template #cell="{ record }">
            <a-tag :color="getDamageRatioColor(record)">
              {{ formatDamageRatio(record) }}
            </a-tag>
          </template>
        </a-table-column>
        <a-table-column title="操作" :width="120" fixed="right">
          <template #cell="{ record }">
            <a-button type="text" size="small" @click="handleView(record)">查看</a-button>
            <a-button type="text" size="small" status="danger" @click="handleDelete(record)">删除</a-button>
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

    <!-- 高级导出弹窗 -->
    <a-modal v-model:visible="showExportModal" title="选择导出字段" @ok="handleExport" @cancel="showExportModal = false">
      <div class="export-modal-content">
        <div class="export-section">
          <span class="label">必选字段：</span>
          <a-checkbox v-model="exportFields.fish_id" disabled>鱼ID</a-checkbox>
          <a-checkbox v-model="exportFields.video_id" disabled>视频ID</a-checkbox>
        </div>
        <div class="export-section">
          <span class="label">可选字段：</span>
          <a-checkbox-group v-model="exportFields.optional">
            <a-checkbox value="body_length">体长</a-checkbox>
            <a-checkbox value="body_height">体高</a-checkbox>
            <a-checkbox value="body_weight">体重</a-checkbox>
            <a-checkbox value="condition_factor">肥满度</a-checkbox>
            <a-checkbox value="surface_damage">体表损伤</a-checkbox>
            <a-checkbox value="surface_damage_ratio">体表损伤占比</a-checkbox>
            <a-checkbox value="confidence">置信度</a-checkbox>
            <a-checkbox value="created_at">检测时间</a-checkbox>
          </a-checkbox-group>
        </div>
      </div>
    </a-modal>

    <!-- 查看详情弹窗 -->
    <a-modal v-model:visible="showDetailModal" title="详细信息" :width="720" :footer="false">
      <div v-if="currentRecord" class="detail-content">
        <div class="detail-section">
          <div class="detail-section-title">基础信息</div>
          <a-descriptions :column="2" bordered size="small">
            <a-descriptions-item label="鱼编号 (track_id)">{{ getFishDisplayId(currentRecord) || '--' }}</a-descriptions-item>
            <a-descriptions-item label="视频ID">{{ currentRecord.video_display_id || currentRecord.video_id }}</a-descriptions-item>
            <a-descriptions-item label="检测时间" :span="2">{{ currentRecord.created_at }}</a-descriptions-item>
            <a-descriptions-item label="置信度">{{ currentRecord.confidence != null ? currentRecord.confidence.toFixed(1) + '%' : '-' }}</a-descriptions-item>
            <a-descriptions-item label="完整 fish_id" :span="2">
              <code style="word-break: break-all;">{{ currentRecord.fish_id }}</code>
            </a-descriptions-item>
          </a-descriptions>
        </div>

        <div class="detail-section">
          <div class="detail-section-title">矫正图像</div>
          <div class="detail-image-block">
            <a-image
              v-if="pickDisplayImage(currentRecord)"
              :src="pickDisplayImage(currentRecord)"
              width="320"
            />
            <span v-else style="color:#999">暂无图像</span>
            <div v-if="imageSourceHint(currentRecord)" class="image-source-hint" style="margin-top:6px;">
              {{ imageSourceHint(currentRecord) }}
            </div>
          </div>
        </div>

        <div class="detail-section">
          <div class="detail-section-title">鱼质指标检测结果</div>
          <a-descriptions :column="2" bordered size="small">
            <a-descriptions-item label="体长">{{ getFishFieldForDisplay(currentRecord, 'body_length_cm', 'body_length') }} cm</a-descriptions-item>
            <a-descriptions-item label="无尾体长">{{ getFishFieldForDisplay(currentRecord, 'body_length_no_tail_cm', 'body_length_without_tail') }} cm</a-descriptions-item>
            <a-descriptions-item label="体高">{{ getFishFieldForDisplay(currentRecord, 'body_height_cm', 'body_height') }} cm</a-descriptions-item>
            <a-descriptions-item label="体重">{{ getFishFieldForDisplay(currentRecord, 'pred_weight_g', 'body_weight') }} g</a-descriptions-item>
            <a-descriptions-item label="肥满度">{{ getFishConditionFactor(currentRecord)?.toFixed?.(3) || '-' }}</a-descriptions-item>
            <a-descriptions-item label="体表损伤占比">
              <a-tag :color="getDamageRatioColor(currentRecord)">{{ formatDamageRatio(currentRecord) }}</a-tag>
              <span v-if="currentRecord.surface_damage" style="margin-left:8px;color:#86909c;font-size:12px;">
                原类型：{{ currentRecord.surface_damage }}
              </span>
              <div v-if="getFishErrorMessage(currentRecord)" style="margin-top:8px;color:#ff7875;font-size:12px;">
                错误：{{ getFishErrorMessage(currentRecord) }}
              </div>
            </a-descriptions-item>
          </a-descriptions>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { normalizeImageUrl } from '@/utils/reportPath'
import { useRoute } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { http } from '@/api/http'
import {
  getFishImageUrl,
  getFishLengthCm,
  getFishNoTailLengthCm,
  getFishHeightCm,
  getFishWeightG,
  getFishDamageRatio,
  getFishDamagePercent,
  getFishConditionFactor,
  getFishErrorMessage,
  getFishDisplayId,
} from '@/utils/fishFields'

interface FishRecord {
  fish_id: string
  fish_display_id?: string
  fish_short_id?: string
  fish_uid?: string
  track_id?: string | number
  video_id: string
  video_display_id?: string
  created_at: string
  // 图像（新字段优先）
  crop_image_path?: string
  crop_image_url?: string
  // 旧字段兼容
  original_image_url?: string
  corrected_image_url?: string
  mask_image_url?: string
  display_image_url?: string
  image_source_type?: 'crop' | 'corrected' | 'original' | 'mask' | 'none'
  // 体征（新字段优先）
  body_length_cm?: number | null
  body_length_no_tail_cm?: number | null
  body_height_cm?: number | null
  pred_weight_g?: number | null
  // 旧字段兼容
  body_length?: number
  body_length_without_tail?: number
  body_height?: number
  body_weight?: number
  condition_factor?: number
  // 损伤
  surface_damage?: string
  surface_damage_ratio?: number | null
  wound_area_ratio?: number | null
  wound_ratio?: number | null
  damage_ratio?: number | null
  // 错误信息
  error_message?: string
  error_msg?: string
  // 其它
  confidence?: number
  status: string
}

const route = useRoute()

const videoId = ref<string>(String(route.query.video_id || ''))
const loadError = ref<string>('')

const filters = reactive({
  fishId: '',
  minLength: undefined as number | undefined,
  maxLength: undefined as number | undefined,
  minHeight: undefined as number | undefined,
  maxHeight: undefined as number | undefined,
  minWeight: undefined as number | undefined,
  maxWeight: undefined as number | undefined,
  minCondition: undefined as number | undefined,
  maxCondition: undefined as number | undefined,
  surfaceDamageLevel: '' as '' | 'none' | 'light' | 'medium' | 'heavy',
  status: '' as '' | 'done' | 'incomplete' | 'failed',
  dateRange: [] as string[]
})

const loading = ref(false)
const currentPage = ref(1)
const pageSize = ref(10)
const total = ref(0)
const tableData = ref<FishRecord[]>([])
const selectedRows = ref<string[]>([])
const showExportModal = ref(false)
const showDetailModal = ref(false)
const currentRecord = ref<FishRecord | null>(null)

const exportFields = reactive({
  fish_id: true,
  video_id: true,
  optional: ['body_length', 'body_height', 'body_weight', 'condition_factor'] as string[]
})

// 列定义（鱼裁剪图 + 算法新字段）
const columns = [
  { title: '鱼编号', dataIndex: 'fish_short_id', width: 160 },
  { title: '鱼裁剪图', dataIndex: 'crop_image_url', width: 120 },
  { title: '体长(cm)', dataIndex: 'body_length_cm', width: 100 },
  { title: '无尾体长(cm)', dataIndex: 'body_length_no_tail_cm', width: 120 },
  { title: '体高(cm)', dataIndex: 'body_height_cm', width: 100 },
  { title: '体重(g)', dataIndex: 'pred_weight_g', width: 100 },
  { title: '肥满度', dataIndex: 'condition_factor', width: 90 },
  { title: '体表损伤占比', dataIndex: 'surface_damage_ratio', width: 120 },
  { title: '操作', width: 120, fixed: 'right' }
]

const rowSelection = computed(() => ({
  type: 'checkbox' as const,
  selectedRowKeys: selectedRows.value
}))

// ---- 图像 fallback ----
function pickDisplayImage(record: FishRecord | null | undefined): string {
  if (!record) return ''
  return (
    getFishImageUrl(record)
    || record.display_image_url
    || record.corrected_image_url
    || record.original_image_url
    || record.mask_image_url
    || ''
  )
}

function getFishFieldForDisplay(
  record: FishRecord | null | undefined,
  newField: keyof FishRecord,
  oldField: keyof FishRecord
): string {
  if (!record) return '-'
  const v = record[newField]
  if (v !== undefined && v !== null && v !== '') {
    const n = Number(v)
    if (Number.isFinite(n)) return n.toFixed(2)
  }
  const oldV = record[oldField]
  if (oldV !== undefined && oldV !== null) {
    const n = Number(oldV)
    if (Number.isFinite(n)) return n.toFixed(2)
  }
  return '-'
}

function imageSourceHint(record: FishRecord | null | undefined): string {
  if (!record) return ''
  const t = record.image_source_type
  if (t === 'corrected' || t == null) return ''
  if (t === 'original') return '未完成矫正，显示原始图像'
  if (t === 'mask') return '未完成矫正，显示裁剪图像'
  if (t === 'none') return '暂无图像'
  return ''
}

// ---- 体表损伤占比 ----
function getDamageRatioRaw(record: FishRecord | null | undefined): number | null {
  if (!record) return null
  const candidates: Array<number | string | null | undefined> = [
    record.surface_damage_ratio,
    record.wound_area_ratio,
    record.wound_ratio,
    record.damage_ratio,
  ]
  for (const v of candidates) {
    if (v === null || v === undefined || v === '') continue
    const n = Number(v)
    if (Number.isFinite(n)) return n
  }
  return null
}

function formatDamageRatio(record: FishRecord | null | undefined): string {
  const raw = getDamageRatioRaw(record)
  if (raw === null) return '-'
  const percent = raw <= 1 ? raw * 100 : raw
  return `${percent.toFixed(2)}%`
}

function getDamageRatioColor(record: FishRecord | null | undefined): string {
  const raw = getDamageRatioRaw(record)
  if (raw === null) return 'gray'
  const percent = raw <= 1 ? raw * 100 : raw
  if (percent <= 0.01) return 'green'
  if (percent <= 5) return 'orange'
  return 'red'
}

async function handleLoadVideo() {
  const v = videoId.value.trim()
  if (!v) {
    Message.warning('请输入 video_id')
    return
  }
  currentPage.value = 1
  await fetchData()
}

async function handleClearVideo() {
  videoId.value = ''
  currentPage.value = 1
  await fetchData()
}

async function fetchData() {
  loading.value = true
  try {
    // 主流程：按当前 videoId 加载该视频的 fish 列表
    if (videoId.value) {
      const params: Record<string, any> = {
        page: currentPage.value,
        page_size: pageSize.value,
      }
      if (filters.status) params.status = filters.status

      const resp = await http.get(
        `/api/videos/${encodeURIComponent(videoId.value)}/fishes`,
        { params }
      )
      const data = resp.data || {}
      let items: FishRecord[] = data.items || []
      tableData.value = items
      total.value = data.total || items.length
      return
    }

    // 兼容：videoId 为空时回退到 /api/fishes 全局检索
    const params: Record<string, any> = {
      page: currentPage.value,
      page_size: pageSize.value,
    }

    if (filters.fishId) params.fish_id = filters.fishId
    if (filters.minLength != null) params.min_length = filters.minLength
    if (filters.maxLength != null) params.max_length = filters.maxLength
    if (filters.minHeight != null) params.min_height = filters.minHeight
    if (filters.maxHeight != null) params.max_height = filters.maxHeight
    if (filters.minWeight != null) params.min_weight = filters.minWeight
    if (filters.maxWeight != null) params.max_weight = filters.maxWeight
    if (filters.minCondition != null) params.min_condition = filters.minCondition
    if (filters.maxCondition != null) params.max_condition = filters.maxCondition
    // 体表损伤占比筛选：直接把 surface_damage 字符串也透传过去，让后端过滤做
    // 旧版接口也支持；占比筛选改为前端按 surface_damage_ratio 后过滤
    if (filters.dateRange && filters.dateRange.length === 2) {
      params.start_date = filters.dateRange[0]
      params.end_date = filters.dateRange[1]
    }

    const resp = await http.get('/api/fishes', { params })
    const data = resp.data

    let items: FishRecord[] = data.items || []
    // 前端按占比档位过滤（后端若已加对应参数可省略）
    if (filters.surfaceDamageLevel) {
      items = items.filter((r) => matchDamageLevel(r, filters.surfaceDamageLevel))
    }
    tableData.value = items
    total.value = data.total || 0
    loadError.value = ''
  } catch (err: any) {
    console.error('Failed to fetch data:', err)
    loadError.value =
      err?.response?.data?.detail ||
      err?.response?.data?.error ||
      err?.message ||
      '加载该视频鱼列表失败，请检查后端是否启动'
    tableData.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function matchDamageLevel(record: FishRecord, level: string): boolean {
  const raw = getDamageRatioRaw(record)
  if (raw === null) {
    // 没数值时按字符串类型兜底
    const t = record.surface_damage || ''
    if (level === 'none') return t.includes('无')
    if (level === 'light') return t.includes('轻微')
    if (level === 'medium') return t.includes('中')
    if (level === 'heavy') return t.includes('严重')
    return true
  }
  const percent = raw <= 1 ? raw * 100 : raw
  if (level === 'none') return percent <= 0.01
  if (level === 'light') return percent > 0.01 && percent <= 5
  if (level === 'medium') return percent > 5 && percent <= 20
  if (level === 'heavy') return percent > 20
  return true
}

function handleSearch() {
  currentPage.value = 1
  fetchData()
}

function handleReset() {
  filters.fishId = ''
  filters.minLength = undefined
  filters.maxLength = undefined
  filters.minHeight = undefined
  filters.maxHeight = undefined
  filters.minWeight = undefined
  filters.maxWeight = undefined
  filters.minCondition = undefined
  filters.maxCondition = undefined
  filters.surfaceDamageLevel = ''
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

function handleImageError(e: Event) {
  const img = e.target as HTMLImageElement
  img.src = '/src/assets/images/test-thumb.jpg'
}

function handleView(record: FishRecord) {
  currentRecord.value = record
  showDetailModal.value = true
}

function handleDelete(record: FishRecord) {
  http.delete(`/api/fishes/${record.fish_id}`)
    .then(() => {
      fetchData()
    })
    .catch((err) => {
      console.error('Failed to delete fish:', err)
    })
}

function handleBatchDelete() {
  if (selectedRows.value.length === 0) return
  http.post('/api/fishes/batch-delete', { fish_ids: selectedRows.value })
    .then(() => {
      selectedRows.value = []
      fetchData()
    })
    .catch((err) => {
      console.error('Failed to batch delete:', err)
    })
}

function handleRefresh() {
  fetchData()
}

function handleExport() {
  const fields = ['fish_id', 'video_id', ...exportFields.optional]
  const apiDomain = import.meta.env.VITE_API_DOMAIN || 'http://localhost:8000'

  fetch(`${apiDomain}/api/fishes/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fields }),
  })
    .then((res) => res.blob())
    .then((blob) => {
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'fish_export.csv'
      a.click()
      URL.revokeObjectURL(url)
    })
    .catch((err) => {
      console.error('Failed to export:', err)
    })

  showExportModal.value = false
}

function copyToClipboard(text: string) {
  navigator.clipboard.writeText(text)
}

onMounted(() => {
  fetchData()
  window.addEventListener('focus', fetchData)
})

watch(
  () => route.query.video_id,
  (newVal) => {
    const next = String(newVal || '')
    if (next !== videoId.value) {
      videoId.value = next
      currentPage.value = 1
      fetchData()
    }
  }
)

onBeforeUnmount(() => {
  window.removeEventListener('focus', fetchData)
})
</script>

<style lang="scss" scoped>
.data-search-page {
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
      .range-inputs {
        display: flex;
        align-items: center;
        gap: 4px;
      }
      .range-sep {
        color: #86909c;
        padding: 0 4px;
      }
    }
    .filter-actions {
      display: flex;
      gap: 12px;
      align-items: flex-end;
    }
  }
  .action-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
    .action-left, .action-right {
      display: flex;
      gap: 12px;
      align-items: center;
    }
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
    .table-image {
      width: 70px;
      height: 50px;
      display: flex;
      align-items: center;
      justify-content: center;
      background: #f7f8fa;
      border-radius: 4px;
      overflow: hidden;
      img {
        max-width: 100%;
        max-height: 100%;
        object-fit: contain;
      }
    }
    .table-image-with-hint {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 4px;
    }
    .image-source-hint {
      font-size: 11px;
      color: #86909c;
      max-width: 110px;
      text-align: center;
      line-height: 1.3;
    }
  }
  .pagination-wrap {
    display: flex;
    justify-content: flex-end;
    margin-top: 20px;
  }
}
.export-modal-content {
  .export-section {
    margin-bottom: 16px;
    .label {
      display: block;
      margin-bottom: 8px;
      font-weight: 500;
      color: #1d2129;
    }
  }
}
.detail-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
  .detail-section {
    .detail-section-title {
      font-size: 14px;
      font-weight: 600;
      color: #1d2129;
      padding-left: 10px;
      border-left: 3px solid #165dff;
      margin-bottom: 8px;
    }
  }
  .detail-image-block {
    .image-source-hint {
      font-size: 12px;
      color: #86909c;
      line-height: 1.4;
    }
  }
}
</style>