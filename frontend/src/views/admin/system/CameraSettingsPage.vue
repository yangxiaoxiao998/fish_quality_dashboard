<template>
  <div class="camera-settings-page">
    <div class="page-header">
      <h2>摄像头设置</h2>
      <p>
        配置摄像头连接参数、推流参数、状态检查与设备重启等。
      </p>
    </div>
    <div class="page-body">
      <CameraSettingsPanel :auto-poll="true" @applied="onApplied" @cancel="onCancel" />
    </div>
  </div>
</template>

<script setup lang="ts">
// 数据管理 - 系统设置 - 摄像头设置 子页签。
// 复用 CameraSettingsPanel，不再使用 a-modal。

import { Message } from '@arco-design/web-vue'
import CameraSettingsPanel, { type CameraConfig } from '@/components/camera/CameraSettingsPanel.vue'

function onApplied(_cfg: CameraConfig) {
  // 系统设置页：applied 仅做提示，不关闭页面（页面无 modal）。
  // 配置已由 CameraSettingsPanel 保存到后端。
  Message.success('摄像头设置已应用')
}

function onCancel() {
  // 系统设置页：cancel 不关闭页面（页面无 modal）。
  // 仅给用户一个轻提示；实际内容由 CameraSettingsPanel 自身维护。
  Message.info('已忽略本次改动')
}
</script>

<style lang="scss" scoped>
.camera-settings-page {
  width: 100%;
  height: 100%;
  min-height: 0;
  box-sizing: border-box;
  padding: 18px 22px;
  background: #fff;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.page-header {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-bottom: 8px;
  border-bottom: 1px solid #e5e6eb;
  h2 {
    margin: 0;
    font-size: 18px;
    font-weight: 700;
    color: #1d2129;
  }
  p {
    margin: 0;
    color: #86909c;
    font-size: 13px;
  }
}
.page-body {
  flex: 1;
  min-height: 0;
}
</style>
