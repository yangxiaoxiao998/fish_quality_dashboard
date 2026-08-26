<template>
  <a-modal v-model:visible="visible" title="完整性设置" :footer="false" :width="460" :mask-closable="false">
    <div class="integrity-dialog">
      <div class="integrity-question">请问是否需要检测鱼的完整性？</div>
      <a-space direction="vertical" fill class="integrity-actions">
        <a-radio-group v-model="needsIntegrity" direction="vertical">
          <a-radio :value="true">是，需要检测完整性</a-radio>
          <a-radio :value="false">否，不需要检测完整性</a-radio>
        </a-radio-group>
      </a-space>
      <div class="integrity-skip">
        <a-checkbox v-model="skip">不再提示</a-checkbox>
        <a-select v-model="scope" :disabled="!skip" placeholder="选择有效期" style="width: 120px">
          <a-option value="once">当次</a-option><a-option value="day">当日</a-option>
          <a-option value="week">本周</a-option><a-option value="forever">永久</a-option>
        </a-select>
        <a-button type="primary" @click="confirmChoice">确定</a-button>
      </div>
    </div>
  </a-modal>
</template>
<script setup lang="ts">
import { ref } from 'vue'
const visible = defineModel<boolean>('visible', { default: false })
const emit = defineEmits<{ (e: 'confirm', value: { forceAllComplete: boolean; scope: string | null }): void }>()
const skip = ref(false)
const scope = ref<string | null>(null)
const needsIntegrity = ref<boolean | null>(null)
function confirmChoice(): void {
  if (needsIntegrity.value === null) return
  emit('confirm', { forceAllComplete: !needsIntegrity.value, scope: skip.value ? scope.value : null })
  visible.value = false
}
</script>
<style scoped>
.integrity-dialog { color: #1d2129; font-size: 15px; }
.integrity-question { margin-bottom: 20px; font-size: 18px; font-weight: 600; text-align: center; }
.integrity-actions { margin-bottom: 22px; }
.integrity-actions :deep(.arco-radio) { font-size: 15px; }
.integrity-skip { display: flex; align-items: center; justify-content: flex-end; gap: 12px; font-size: 15px; }
.integrity-skip :deep(.arco-checkbox-label), .integrity-skip :deep(.arco-select-view-value) { font-size: 15px; }
</style>
