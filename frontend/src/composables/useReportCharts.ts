/**
 * composables/useReportCharts.ts
 *
 * 统一渲染 summary + charts 到 echarts。
 * 返回 destroy() 用于卸载。
 *
 * 规范（本轮）：
 * - 分布类柱状图：纵轴统一为"占比（%）"，柱子原始 count 转换为 ratio（0~1），
 *   图表内部数据统一使用 ratio，yAxis 显示百分比；tooltip 同时显示占比 + 数量。
 * - 趋势图：纵轴仍是"视频数量（个）"或实际业务指标，不强制改为占比。
 */
import * as echarts from 'echarts'
import { nextTick, onBeforeUnmount, ref, watch, type Ref } from 'vue'
import type { ReportCharts, ReportSummary } from './useReportData'

type BarDatum = { label: string; count: number }

function countToPercent(values: number[]): number[] {
  const total = values.reduce((s, v) => s + (Number.isFinite(v) ? v : 0), 0)
  if (total <= 0) return values.map(() => 0)
  return values.map((v) => (Number.isFinite(v) ? v : 0) / total)
}

function buildPercentBarOption(
  data: BarDatum[],
  title: string,
  xAxisName: string,
  xUnitLabel: string,
): any {
  const counts = data.map((d) => Number(d.count) || 0)
  const percents = countToPercent(counts).map((r) => Math.round(r * 10000) / 100) // 0~100
  return {
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const arr = Array.isArray(params) ? params : [params]
        const p = arr[0]
        if (!p || p.dataIndex == null) return ''
        const idx = p.dataIndex
        const datum = data[idx]
        if (!datum) return ''
        const pct = Number(p.value ?? 0)
        const lines = [
          `${xAxisName}：${datum.label}${xUnitLabel}`,
          `占比：${pct.toFixed(2)}%`,
          `数量：${datum.count} 条`,
        ]
        return lines.join('<br/>')
      },
    },
    grid: { left: 50, right: 16, top: 24, bottom: 56 },
    title: title
      ? { text: title, left: 'center', top: 4, textStyle: { fontSize: 13, color: '#1d2129' } }
      : undefined,
    xAxis: {
      type: 'category',
      name: xAxisName,
      nameLocation: 'middle',
      nameGap: 42,
      data: data.map((d) => d.label),
      axisLabel: { interval: 0, rotate: data.length > 6 ? 25 : 0 },
      nameTextStyle: { color: '#4e5969', fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      name: '占比（%）',
      nameLocation: 'middle',
      nameGap: 46,
      min: 0,
      max: 100,
      axisLabel: { formatter: '{value}%', color: '#4e5969', fontSize: 11 },
      splitLine: { lineStyle: { color: '#e5e6eb' } },
      nameTextStyle: { color: '#4e5969', fontSize: 11 },
    },
    series: [
      {
        type: 'bar',
        name: title || xAxisName,
        data: percents,
        barMaxWidth: 36,
        label: {
          show: percents.some((v) => v >= 4),
          formatter: (p: any) => `${Number(p.value || 0).toFixed(1)}%`,
          color: '#1d2129',
          fontSize: 11,
          position: 'top',
        },
        itemStyle: { color: '#2f7bff' },
      },
    ],
  }
}

function buildTrendOption(trend: { bucket: string; count: number }[]): any {
  return {
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const arr = Array.isArray(params) ? params : [params]
        const p = arr[0]
        if (!p) return ''
        return `${p.axisValue}<br/>视频数量：${Number(p.value || 0)} 个`
      },
    },
    grid: { left: 50, right: 16, top: 24, bottom: 40 },
    xAxis: {
      type: 'category',
      name: '日期',
      nameLocation: 'middle',
      nameGap: 32,
      data: trend.map((d) => d.bucket),
      axisLabel: { rotate: trend.length > 6 ? 30 : 0 },
      nameTextStyle: { color: '#4e5969', fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      name: '视频数量（个）',
      nameLocation: 'middle',
      nameGap: 44,
      minInterval: 1,
      axisLabel: { color: '#4e5969', fontSize: 11 },
      splitLine: { lineStyle: { color: '#e5e6eb' } },
      nameTextStyle: { color: '#4e5969', fontSize: 11 },
    },
    series: [{ type: 'line', data: trend.map((d) => d.count), areaStyle: {} }],
  }
}

function buildVideoRankOption(rank: { video_id: string; count: number }[]): any {
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 40, right: 16, top: 20, bottom: 40 },
    xAxis: { type: 'value', minInterval: 1 },
    yAxis: {
      type: 'category',
      data: rank.map((d) => d.video_id),
      axisLabel: { fontSize: 11 },
    },
    series: [{ type: 'bar', data: rank.map((d) => d.count) }],
  }
}

export function useReportCharts(
  summary: Ref<ReportSummary>,
  charts: Ref<ReportCharts>,
  refs: {
    trend?: Ref<HTMLElement | undefined>
    lengthDist?: Ref<HTMLElement | undefined>
    noTailDist?: Ref<HTMLElement | undefined>
    heightDist?: Ref<HTMLElement | undefined>
    weightDist?: Ref<HTMLElement | undefined>
    conditionDist?: Ref<HTMLElement | undefined>
    damageDist?: Ref<HTMLElement | undefined>
    videoRank?: Ref<HTMLElement | undefined>
    statusPie?: Ref<HTMLElement | undefined>
  }
) {
  const rendered = ref<boolean>(false)
  let chartInstances: echarts.ECharts[] = []

  function destroy() {
    chartInstances.forEach((c) => c.dispose())
    chartInstances = []
    rendered.value = false
  }

  async function render(): Promise<void> {
    destroy()
    await nextTick()
    const s = summary.value
    const c = charts.value

    if (refs.trend?.value && c.fish_count_trend?.length) {
      const trend = c.fish_count_trend.map((d) => ({ bucket: d.bucket, count: d.count }))
      const inst = echarts.init(refs.trend.value)
      inst.setOption(buildTrendOption(trend))
      chartInstances.push(inst)
    }

    if (refs.lengthDist?.value && c.body_length_distribution?.length) {
      const inst = echarts.init(refs.lengthDist.value)
      inst.setOption(buildPercentBarOption(c.body_length_distribution, '', '体长区间（cm）', ' cm'))
      chartInstances.push(inst)
    }
    if (refs.noTailDist?.value && c.body_length_without_tail_distribution?.length) {
      const inst = echarts.init(refs.noTailDist.value)
      inst.setOption(buildPercentBarOption(c.body_length_without_tail_distribution, '', '无尾体长区间（cm）', ' cm'))
      chartInstances.push(inst)
    }
    if (refs.heightDist?.value && c.body_height_distribution?.length) {
      const inst = echarts.init(refs.heightDist.value)
      inst.setOption(buildPercentBarOption(c.body_height_distribution, '', '体高区间（cm）', ' cm'))
      chartInstances.push(inst)
    }
    if (refs.weightDist?.value && c.body_weight_distribution?.length) {
      const inst = echarts.init(refs.weightDist.value)
      inst.setOption(buildPercentBarOption(c.body_weight_distribution, '', '体重区间（g）', ' g'))
      chartInstances.push(inst)
    }
    if (refs.conditionDist?.value && c.condition_factor_distribution?.length) {
      const inst = echarts.init(refs.conditionDist.value)
      inst.setOption(buildPercentBarOption(c.condition_factor_distribution, '', '肥满度区间', ''))
      chartInstances.push(inst)
    }
    if (refs.damageDist?.value && c.surface_damage_distribution?.length) {
      const inst = echarts.init(refs.damageDist.value)
      inst.setOption(buildPercentBarOption(c.surface_damage_distribution, '', '损伤比例区间（%）', '%'))
      chartInstances.push(inst)
    }
    if (refs.videoRank?.value && c.video_fish_rank?.length) {
      const inst = echarts.init(refs.videoRank.value)
      inst.setOption(buildVideoRankOption(c.video_fish_rank))
      chartInstances.push(inst)
    }

    if (refs.statusPie?.value) {
      const statusData = [
        { name: '有效', value: s.valid_fish || 0 },
        { name: '异常', value: s.abnormal_fish || 0 },
        { name: '破损', value: s.surface_damage_count || 0 },
      ].filter((x) => x.value > 0)
      const total = statusData.reduce((sum, x) => sum + Number(x.value || 0), 0)
      const inst = echarts.init(refs.statusPie.value)
      inst.setOption({
        tooltip: {
          trigger: 'item',
          formatter: (p: any) => {
            const v = Number(p.value || 0)
            const pct = total > 0 ? (v * 100) / total : 0
            return `${p.name}<br/>数量：${v} 条<br/>占比：${pct.toFixed(2)}%`
          },
        },
        legend: { bottom: 0 },
        series: [
          {
            type: 'pie',
            radius: ['38%', '70%'],
            data: statusData,
            label: {
              formatter: (p: any) => {
                const v = Number(p.value || 0)
                const pct = total > 0 ? (v * 100) / total : 0
                return `${p.name} ${pct.toFixed(1)}%`
              },
            },
          },
        ],
      })
      chartInstances.push(inst)
    }

    rendered.value = chartInstances.length > 0
  }

  function handleResize() {
    chartInstances.forEach((c) => c.resize())
  }

  watch(
    [() => summary.value, () => charts.value],
    () => render(),
    { deep: true }
  )

  if (typeof window !== 'undefined') {
    window.addEventListener('resize', handleResize)
  }

  onBeforeUnmount(() => {
    destroy()
    if (typeof window !== 'undefined') {
      window.removeEventListener('resize', handleResize)
    }
  })

  return { render, destroy, rendered, handleResize }
}
