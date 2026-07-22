/**
 * composables/useReportData.ts
 *
 * 汇总报表（当日 / 当日视频 / 日期范围）统一数据加载。
 */
import { ref, watch } from 'vue'
import { http, toErrorMessage } from '@/api/http'
import { Message } from '@arco-design/web-vue'

export interface ReportQuery {
  report_date?: string
  video_id?: string
  start_date?: string
  end_date?: string
}

export interface ReportSummary {
  total_video: number
  total_fish: number
  valid_fish: number
  abnormal_fish: number
  avg_body_length: number
  avg_body_length_without_tail: number
  avg_body_height: number
  avg_body_weight: number
  avg_condition_factor: number
  surface_damage_count: number
  /** 后端 /api/reports/fish-quality/summary 直接给出的比率字段 */
  metric_complete_rate?: number | null
  abnormal_fish_rate?: number | null
  surface_damage_rate?: number | null
}

export interface ReportCharts {
  fish_count_trend: { bucket: string; bucket_kind: string; count: number }[]
  body_length_distribution: { label: string; count: number }[]
  body_length_without_tail_distribution: { label: string; count: number }[]
  body_height_distribution: { label: string; count: number }[]
  body_weight_distribution: { label: string; count: number }[]
  condition_factor_distribution: { label: string; count: number }[]
  surface_damage_distribution: { label: string; count: number }[]
  video_fish_rank: { video_id: string; count: number }[]
}

export interface ReportFishRow {
  fish_id: string
  video_id: string
  track_id?: string | number | null
  body_length_cm?: number | null
  body_length_no_tail_cm?: number | null
  body_height_cm?: number | null
  pred_weight_g?: number | null
  condition_factor?: number | null
  surface_damage_ratio?: number | null
  status?: string
  error_message?: string
  crop_image_path?: string | null
  crop_image_url?: string | null
  fish_uid?: string
  frame_index?: number | null
  created_at?: string
}

export interface ReportFishesResp {
  items: ReportFishRow[]
  total: number
}

const EMPTY_SUMMARY: ReportSummary = {
  total_video: 0,
  total_fish: 0,
  valid_fish: 0,
  abnormal_fish: 0,
  avg_body_length: 0,
  avg_body_length_without_tail: 0,
  avg_body_height: 0,
  avg_body_weight: 0,
  avg_condition_factor: 0,
  surface_damage_count: 0,
  metric_complete_rate: null,
  abnormal_fish_rate: null,
  surface_damage_rate: null,
}

const EMPTY_CHARTS: ReportCharts = {
  fish_count_trend: [],
  body_length_distribution: [],
  body_length_without_tail_distribution: [],
  body_height_distribution: [],
  body_weight_distribution: [],
  condition_factor_distribution: [],
  surface_damage_distribution: [],
  video_fish_rank: [],
}

function buildQuery(q: ReportQuery): Record<string, string> {
  const p: Record<string, string> = {}
  if (q.report_date) p.report_date = q.report_date
  if (q.video_id) p.video_id = q.video_id
  if (q.start_date) p.start_date = q.start_date
  if (q.end_date) p.end_date = q.end_date
  return p
}

export function useReportData(query: () => ReportQuery) {
  const loading = ref(false)
  const summary = ref<ReportSummary>({ ...EMPTY_SUMMARY })
  const charts = ref<ReportCharts>({ ...EMPTY_CHARTS })
  const fishes = ref<ReportFishRow[]>([])
  const fishesTotal = ref(0)
  const fishesLoading = ref(false)
  const fishesPage = ref(1)
  const fishesPageSize = ref(20)

  async function loadSummary(): Promise<void> {
    try {
      const resp = await http.get('/api/reports/fish-quality/summary', {
        params: buildQuery(query()),
      })
      summary.value = resp.data || { ...EMPTY_SUMMARY }
    } catch (err) {
      Message.error(toErrorMessage(err))
    }
  }

  async function loadCharts(): Promise<void> {
    try {
      const resp = await http.get('/api/reports/fish-quality/charts', {
        params: buildQuery(query()),
      })
      charts.value = resp.data || { ...EMPTY_CHARTS }
    } catch (err) {
      Message.error(toErrorMessage(err))
    }
  }

  async function loadFishes(
    page: number = 1,
    pageSize: number = 20
  ): Promise<void> {
    fishesLoading.value = true
    try {
      const resp = await http.get('/api/reports/fish-quality/fishes', {
        params: {
          ...buildQuery(query()),
          page,
          page_size: pageSize,
        },
      })
      const data = (resp.data || {}) as Partial<ReportFishesResp>
      fishes.value = Array.isArray(data.items) ? data.items : []
      fishesTotal.value = data.total || fishes.value.length
      fishesPage.value = page
      fishesPageSize.value = pageSize
    } catch (err) {
      fishes.value = []
      fishesTotal.value = 0
      Message.error(toErrorMessage(err))
    } finally {
      fishesLoading.value = false
    }
  }

  async function loadAll(): Promise<void> {
    loading.value = true
    try {
      await Promise.all([loadSummary(), loadCharts()])
    } finally {
      loading.value = false
    }
  }

  watch(query, () => {
    loadAll()
    loadFishes(1, fishesPageSize.value)
  })

  return {
    loading,
    summary,
    charts,
    fishes,
    fishesTotal,
    fishesLoading,
    fishesPage,
    fishesPageSize,
    loadSummary,
    loadCharts,
    loadFishes,
    loadAll,
  }
}
