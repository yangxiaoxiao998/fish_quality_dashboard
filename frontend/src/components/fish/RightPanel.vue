<!-- src/components/fish/RightPanel.vue -->
<script setup lang="ts">
import { ref, onMounted } from 'vue'; // --- 新增 onMounted ---
import FishDetailPanel from './FishDetailPanel.vue';
import StatsPanel from './StatsPanel.vue';
import { getStats } from '../../services/api'; // --- 新增 ---
import type { StatsBucket } from '../../types/fish'; // --- 新增 ---

const props = defineProps<{
  detail: any; // Use FishDetail once imported and defined
}>();

const statsData = ref<StatsBucket[]>([]); // --- 新增 ---

// --- 加载统计 ---
const loadStats = async () => {
  try {
    // 可以根据当前时间或选定的videoId来查询
    const now = new Date();
    const endTime = now.toISOString();
    const startTime = new Date(now.getTime() - 60 * 60 * 1000).toISOString(); // 1小时前

    const data = await getStats({ start_time: startTime, end_time: endTime, interval: '5m' });
    statsData.value = data;
  } catch (error) {
    console.error("Failed to load stats:", error);
    statsData.value = [];
  }
};

onMounted(() => { // --- 新增 ---
    loadStats();
});
</script>

<template>
  <div class="right-panel">
    <FishDetailPanel :detail="props.detail" />
    <StatsPanel class="stats-panel" :data="statsData" />
  </div>
</template>

<style scoped>
/* --- 保留所有原有样式，不做任何修改 --- */
</style>