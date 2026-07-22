<template>
  <a-modal
    v-model:visible="visible"
    title="摄像头设置"
    :width="640"
    :closable="true"
    :mask-closable="true"
    :footer="false"
    @cancel="handleClose"
  >
    <CameraSettingsPanel
      :auto-poll="visible"
      @applied="handleApplied"
      @cancel="handleClose"
    />
  </a-modal>
</template>

<script setup lang="ts">
// 摄像头设置 - 薄 a-modal 包装。
// 实际内容已抽到 CameraSettingsPanel.vue，可被系统设置页（CameraSettingsPage.vue）复用。
// 保留 v-model 接口以兼容旧调用方。

import { computed } from 'vue'
import CameraSettingsPanel, { type CameraConfig } from './CameraSettingsPanel.vue'

const props = defineProps<{
  modelValue: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'applied', config: CameraConfig): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

function handleClose() {
  visible.value = false
}

function handleApplied(cfg: CameraConfig) {
  emit('applied', cfg)
  visible.value = false
}
</script>