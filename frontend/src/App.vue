<template>
  <router-view />
  <a-modal
    v-model:visible="debugPromptVisible"
    title="技术调试模式访问验证"
    :mask-closable="false"
    @ok="confirmDebugAccess"
    @cancel="cancelDebugAccess"
  >
    <p>请输入技术调试模式密码。</p>
    <a-input
      v-model="debugPassword"
      type="password"
      placeholder="请输入密码"
      allow-clear
      @press-enter="confirmDebugAccess"
    />
  </a-modal>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { useRoute, useRouter } from 'vue-router'
import {
  clearTechnicalDebugAccessRequest,
  grantTechnicalDebugAccess,
  getTechnicalDebugAccessRequest,
  TECHNICAL_DEBUG_PASSWORD,
  setAdministrator,
} from '@/utils/technicalDebugAuth'

const router = useRouter()
const route = useRoute()
const debugPromptVisible = ref(false)
const debugPassword = ref('')
const requestedPath = ref<string | null>(null)
let lastRequestId = 0

watch(
  () => getTechnicalDebugAccessRequest().id,
  () => {
    const request = getTechnicalDebugAccessRequest()
    if (request.id === lastRequestId || !request.path) return
    lastRequestId = request.id
    requestedPath.value = request.path
    debugPassword.value = ''
    debugPromptVisible.value = true
  },
  { immediate: true },
)

function confirmDebugAccess(): void {
  if (!debugPassword.value.trim()) {
    Message.warning('请输入技术调试模式密码。')
    return
  }
  if (debugPassword.value !== TECHNICAL_DEBUG_PASSWORD) {
    Message.error('密码错误，暂无法进入技术调试模式，请核对密码或联系管理人员。')
    return
  }
  setAdministrator(true)
  debugPromptVisible.value = false
  const target = requestedPath.value
  requestedPath.value = null
  clearTechnicalDebugAccessRequest()
  if (target) {
    grantTechnicalDebugAccess(target)
    void router.push(target)
  }
}

function cancelDebugAccess(): void {
  debugPromptVisible.value = false
  requestedPath.value = null
  clearTechnicalDebugAccessRequest()
  if (route.path === '/admin/system-ops' || route.path === '/admin/system-settings') {
    void router.replace('/admin/data-report')
  }
}
</script>
