// frontend/src/utils/distributionChart.ts
//
// 归一化 + 设置 Dashboard 左侧四个分布图的 ECharts option。
// 横轴：动态 label（后端给出）
// 纵轴：占比 ratio（0~1，显示为百分比）
// tooltip：区间 / 数量 / 占比 %

import * as echarts from 'echarts'

export type DistributionItem = {
  label: string
  count: number
  ratio?: number
}

/**
 * 把后端给的数据归一化为 ratio 之和严格 = 1。
 * - 如果后端有 ratio：直接用，但再做一次 normalize 修正浮点误差。
 * - 如果后端没 ratio：用 count/total 兜底。
 * - 空数据：返回 []。
 */
export function normalizeDistribution(
  items: DistributionItem[] | null | undefined
): { label: string; count: number; ratio: number }[] {
  const list = Array.isArray(items) ? items : []
  const totalCount = list.reduce(
    (s, x) => s + (Number(x?.count) || 0),
    0
  )

  const rawRatios = list.map((x) => {
    const r = Number(x?.ratio)
    if (Number.isFinite(r) && (r as number) >= 0) return r as number
    if (totalCount > 0) return (Number(x?.count) || 0) / totalCount
    return 0
  })

  const ratioSum = rawRatios.reduce((s, x) => s + x, 0)
  return list.map((x, idx) => ({
    label: x?.label || '-',
    count: Number(x?.count) || 0,
    ratio: ratioSum > 0 ? (rawRatios[idx] ?? 0) / ratioSum : 0,
  }))
}

/**
 * 设置分布图 ECharts option。
 * - 横轴 labels：直接使用后端 label（区间字符串）
 * - 纵轴：占比（百分比）
 * - tooltip：区间 / 数量 / 占比 %
 */
export function setDistributionChartOption(
  chart: echarts.ECharts,
  rawData: DistributionItem[] | null | undefined,
  color: [string, string],
  xAxisName = '区间',
) {
  const data = normalizeDistribution(rawData)
  const labels = data.map((d) => d.label)
  const ratios = data.map((d) => d.ratio)
  const counts = data.map((d) => d.count)
  const maxRatio = ratios.length > 0 ? Math.max(...ratios) : 0

  // y 轴最大值：非空时 max(0.1, maxRatio * 1.15)，不超过 1
  const yMax =
    maxRatio <= 0
      ? 1
      : Math.min(1, Math.max(0.1, Math.round(maxRatio * 1.15 * 1000) / 1000))

  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgb(22, 33, 62, 0.95)',
      borderColor: '#00a8e8',
      textStyle: { color: '#fff' },
      formatter: (params: any) => {
        const arr = Array.isArray(params) ? params : [params]
        const p = arr[0]
        if (!p || p.dataIndex == null) return ''
        const item = data[p.dataIndex]
        if (!item) return ''
        return [
          `${xAxisName}：${item.label}`,
          `数量：${item.count}`,
          `占比：${(item.ratio * 100).toFixed(2)}%`,
        ].join('<br/>')
      },
    },
    grid: { top: 32, right: 12, bottom: 56, left: 50 },
    xAxis: {
      type: 'category',
      name: xAxisName,
      nameLocation: 'middle',
      nameGap: 42,
      nameTextStyle: { color: 'rgb(255,255,255,0.7)', fontSize: 11 },
      data: labels,
      axisLine: { lineStyle: { color: 'rgb(255,255,255,0.2)' } },
      axisLabel: {
        color: 'rgb(255,255,255,0.6)',
        fontSize: 10,
        rotate: labels.length > 6 ? 30 : 0,
      },
    },
    yAxis: {
      type: 'value',
      name: '占比（%）',
      nameLocation: 'middle',
      nameGap: 46,
      nameTextStyle: { color: 'rgb(255,255,255,0.7)', fontSize: 11 },
      min: 0,
      max: yMax,
      axisLine: { lineStyle: { color: 'rgb(255,255,255,0.2)' } },
      axisLabel: {
        color: 'rgb(255,255,255,0.6)',
        fontSize: 10,
        formatter: (v: number) => `${Math.round(Number(v) * 100)}%`,
      },
      splitLine: { lineStyle: { color: 'rgb(255,255,255,0.1)' } },
    },
    series: [
      {
        data: ratios,
        type: 'bar',
        barWidth: '60%',
        itemStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: color[0] },
            { offset: 1, color: color[1] },
          ]),
        },
        animationDuration: 800,
      },
    ],
  })

  // 给消费者一个 sanity check：sum ratio 应严格 = 1（如果非空）
  if (ratios.length > 0) {
    const sum = ratios.reduce((s, x) => s + x, 0)
    if (Math.abs(sum - 1.0) > 1e-6) {
      // eslint-disable-next-line no-console
      console.warn(
        `[distributionChart] ratio sum = ${sum.toFixed(6)} (expected 1.0)`
      )
    }
  }
  void counts
}
