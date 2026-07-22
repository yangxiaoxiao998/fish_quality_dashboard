<!-- src/components/fish/StatsPanel.vue -->
<script setup lang="ts">
import { ref, watch, onMounted } from 'vue';
import type { StatsBucket } from '../../types/fish';
import * as echarts from 'echarts';

const props = defineProps<{
  data: StatsBucket[];
}>();

const chartRef = ref<HTMLDivElement | null>(null);
let myChart: echarts.ECharts | null = null;

const renderChart = (data: StatsBucket[]) => {
  if (!chartRef.value) return;
  if (!myChart) {
    myChart = echarts.init(chartRef.value);
  }

  const timeBuckets = data.map(item => new Date(item.bucket_start).toLocaleTimeString());
  const counts = data.map(item => item.count);
  const meanLengths = data.map(item => item.mean_body_length);
  const meanWeights = data.map(item => item.mean_body_weight);

  const option: echarts.EChartsOption = {
    title: {
      // text: '近一小时统计',
      // subtext: '按5分钟间隔',
      // left: 'center'
    },
    tooltip: {
      trigger: 'axis'
    },
    legend: {
      data: ['数量', '平均身长', '平均体重'],
      top: '0%',
      textStyle: {
        color: '#fff' // 设置图例文字颜色为白色
      }
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '3%',
      top: '10%',
      containLabel: true
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: timeBuckets,
      axisLabel: {
        color: '#fff' // 设置X轴标签颜色为白色
      },
    },
    yAxis: [
      {
        type: 'value',
        name: '数量',
        position: 'left',
        min: 0,
        axisLine: { show: true, onZero: true },
        axisLabel: {
          color: '#fff' // 设置Y轴标签颜色为白色
        },
        nameTextStyle: {
          color: '#fff' // 设置Y轴名称颜色为白色
        },
      // --- 添加以下配置以控制左侧Y轴的网格线 ---
        splitNumber: 5, // 强制分为5段，可以根据需要调整
        splitLine: {
          show: true,
          lineStyle: {
            color: '#333', // 设置网格线颜色，可以是深色以适应深色背景
            width: 1,
            type: 'solid' // 可以是 solid, dashed, dotted
          }
        }
      },
      {
        type: 'value',
        name: '长度/重量',
        position: 'right',
        axisLine: { show: true, onZero: true  },
        axisLabel: {
          color: '#fff' // 设置Y轴标签颜色为白色
        },
        nameTextStyle: {
          color: '#fff' // 设置Y轴名称颜色为白色
        },
        splitNumber: 5, // 强制分为5段，可以根据需要调整
        splitLine: {
          show: true,
          lineStyle: {
            color: '#333', // 设置网格线颜色
            width: 1,
            type: 'solid'
          }
        }
      }
    ],
    series: [
      {
        name: '数量',
        type: 'bar',
        yAxisIndex: 0,
        data: counts,
        barWidth: '50%',
        itemStyle: {
          color: {
            type: 'linear',
            x: 0, // 若将此值设为1，表示从右到左渐变
            y: 1, // 若将此值设为1，表示从上到下渐变
            x2: 0, // 左
            y2: 0, // 上
            colorStops: [
              {
                offset: 0,
                color: '#192060' // 0% 处的颜色
              },
              {
                offset: 0.9,
                color: '#00C0FF' // 90% 处的颜色
              }
            ]
          }
        }
      },
      {
        name: '平均身长',
        type: 'line',
        yAxisIndex: 1,
        data: meanLengths,
        smooth: true,
        itemStyle: { color: '#FFD600' }
      },
      {
        name: '平均体重',
        type: 'line',
        yAxisIndex: 1,
        data: meanWeights,
        smooth: true,
        itemStyle: { color: '#00C0FF' }
      }
    ]
  };

  myChart.setOption(option);
};

watch(() => props.data, (newData) => {
  renderChart(newData);
}, { immediate: true }); // 初始渲染

onMounted(() => {
  // 图表初始化会在 watch 的 immediate: true 中完成
});
</script>

<template>
  <div class="stats-panel">
    <div ref="chartRef" style="width: 100%; height: 280px;"></div>
  </div>
</template>

<style scoped>
.stats-panel {
  display: flex;
  justify-content: space-between;
  margin-bottom: 15px;
  font-size: 14px;
  color: #fff;
  margin-left: 15px;          /* 保持或调整左边距 */
  margin-right: 15px;         /* 添加右边距 */
}
</style>