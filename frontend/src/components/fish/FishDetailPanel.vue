<template>
  <div class="fish-detail-panel">
    <!-- 图像展示区域：算法新字段 crop_image_url 优先 -->
    <div class="image-container">
      <img v-if="displayImageUrl" :src="displayImageUrl" alt="Fish Crop Image" />
      <img v-else src="/src/assets/images/test-thumb.jpg" alt="Default Fish Image" />
    </div>

    <!-- 检测数据 -->
    <div class="data-section">
      <h4>检测数据</h4>
      <div class="data-item">
        <span>序号:</span>
        <span>{{ displayId ?? '--' }}</span>
      </div>
      <div class="data-item">
        <span>体长:</span>
        <span>{{ lengthCm != null ? `${lengthCm.toFixed(2)} cm` : 'N/A' }}</span>
      </div>
      <div class="data-item">
        <span>无尾体长:</span>
        <span>{{ noTailCm != null ? `${noTailCm.toFixed(2)} cm` : 'N/A' }}</span>
      </div>
      <div class="data-item">
        <span>体高:</span>
        <span>{{ heightCm != null ? `${heightCm.toFixed(2)} cm` : 'N/A' }}</span>
      </div>
      <div class="data-item">
        <span>体重:</span>
        <span>{{ weightG != null ? `${weightG.toFixed(2)} g` : 'N/A' }}</span>
      </div>
      <div class="data-item">
        <span>肥满度:</span>
        <span>{{ condition != null ? `${condition}` : 'N/A' }}</span>
      </div>
      <div class="data-item">
        <span>体表损伤占比:</span>
        <span>{{ damagePercent }}</span>
      </div>
      <div class="data-item" v-if="errorMessage">
        <span>错误信息:</span>
        <span style="color:#ff7875">{{ errorMessage }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import type { FishDetail } from '@/types/fish'
import {
  getFishImageUrl,
  getFishLengthCm,
  getFishNoTailLengthCm,
  getFishHeightCm,
  getFishWeightG,
  getFishDamagePercent,
  getFishConditionFactor,
  getFishErrorMessage,
  getFishDisplayId,
} from '@/utils/fishFields'

const props = defineProps({
  detail: {
    type: Object as PropType<FishDetail>,
    required: true,
  },
})

const displayImageUrl = computed(() => getFishImageUrl(props.detail))
const displayId = computed(() => getFishDisplayId(props.detail))
const lengthCm = computed(() => getFishLengthCm(props.detail) as number | null)
const noTailCm = computed(() => getFishNoTailLengthCm(props.detail) as number | null)
const heightCm = computed(() => getFishHeightCm(props.detail) as number | null)
const weightG = computed(() => getFishWeightG(props.detail) as number | null)
const condition = computed(() => getFishConditionFactor(props.detail) as number | null)
const damagePercent = computed(() => getFishDamagePercent(props.detail))
const errorMessage = computed(() => getFishErrorMessage(props.detail))
</script>

<style scoped>
.fish-detail-panel {
  width: 100%;
  height: 100%;
  padding: 10px;
  box-sizing: border-box;
}
.image-container {
  width: 100%;
  height: 300px;
  margin-bottom: 12px;
  overflow: hidden;
  position: relative;
}
.image-container img {
  width: 100%;
  height: 100%;
  object-fit: scale;
  transform: translateY(0%);
  border-radius: 8px;
}
.data-section {
  width: 100%;
  height: 37%;
  background-color: rgb(255, 255, 255, 10%);
  border-radius: 8px;
}
.data-section h4 {
  margin-top: 0;
  margin-bottom: 10px;
  color: #fff;
  padding: 10px 0 0 10px;
}
.data-item {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 14px;
  color: #fff;
  margin-left: 15px;
  margin-right: 15px;
}
</style>
