<template>
  <div
    class="screen-home"
    :style="{ backgroundImage: `url(${screenBackground})` }"
  >
    <div class="screen-content">
      <img
        class="university-logo"
        :src="universityLogo"
        alt="山东大学"
      />
      <div
        class="settings-button"
        role="button"
        tabindex="0"
        :aria-label="'系统设置'"
        @click="goSystemSettings"
        @keydown.enter="goSystemSettings"
        @keydown.space.prevent="goSystemSettings"
      >
        <icon-settings class="settings-icon" />
      </div>
      <div class="system-title-text">鱼品质智能检测系统</div>
      <div class="action-buttons">
        <div
          class="action-button"
          role="button"
          tabindex="0"
          @click="goDataInput"
          @keydown.enter="goDataInput"
          @keydown.space.prevent="goDataInput"
        >
          <img
            class="action-button-background"
            :src="baseButton"
            alt=""
            aria-hidden="true"
          />
          <span class="main-button-text">录入</span>
        </div>
        <div
          class="action-button"
          role="button"
          tabindex="0"
          @click="goReportView"
          @keydown.enter="goReportView"
          @keydown.space.prevent="goReportView"
        >
          <img
            class="action-button-background"
            :src="baseButton"
            alt=""
            aria-hidden="true"
          />
          <span class="main-button-text">报表</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import { IconSettings } from '@arco-design/web-vue/es/icon'
import { requestTechnicalDebugAccess } from '@/utils/technicalDebugAuth'

import screenBackground from '@/assets/images/screen_fish_quality_bg.png'
import baseButton from '@/assets/images/btn_base_dark_blue.png'
import universityLogo from '@/assets/images/shandong_university_logo.png'

const router = useRouter()

function goDataInput(): void {
  router.push('/data-input')
}

function goReportView(): void {
  router.push('/admin/data-report')
}

function goSystemSettings(): void {
  requestTechnicalDebugAccess('/admin/system-settings')
}
</script>

<style scoped>
.screen-home {
  width: 100%;
  height: 100%;
  min-height: 100dvh;
  overflow: hidden;
  position: relative;
  background-color: #06122b;
  background-repeat: no-repeat;
  background-position: center;
  background-size: cover;
}
.screen-content {
  width: 100%;
  height: 100%;
  position: relative;
}
.system-title-text {
  position: absolute;
  top: clamp(18%, 26%, 280px);
  left: 50%;
  width: min(80vw, 1200px);
  transform: translateX(-50%);
  z-index: 1;
  color: #4094dc;
  font-family: AlibabaPuHuiTi, "Noto Sans SC", sans-serif;
  font-size: clamp(32px, 4.8vw, 88px);
  font-weight: 700;
  letter-spacing: clamp(4px, 0.7vw, 14px);
  line-height: 1.2;
  text-align: center;
  white-space: nowrap;
  user-select: none;
  pointer-events: none;
}
.university-logo {
  position: absolute;
  top: clamp(20px, 3.7vh, 40px);
  left: clamp(24px, 2.6vw, 50px);
  z-index: 2;
  display: block;
  width: min(12.5vw, 240px);
  height: auto;
  user-select: none;
  pointer-events: none;
  filter: drop-shadow(0 2px 8px rgb(0 0 0 / 35%));
}
.action-buttons {
  position: absolute;
  top: 58%;
  left: 50%;
  display: flex;
  gap: clamp(24px, 4.2vw, 80px);
  align-items: center;
  justify-content: center;
  transform: translateX(-50%);
  z-index: 1;
}
.action-button {
  position: relative;
  width: clamp(140px, 10.5vw, 200px);
  height: clamp(64px, 8.3vh, 90px);
  padding: 0 clamp(28px, 4.2vw, 80px);
  border: 1px solid rgb(96 181 255 / 80%);
  border-radius: 18px;
  background: #4094dc;
  box-shadow: 0 8px 24px rgb(0 28 85 / 35%);
  cursor: pointer;
  user-select: none;
  transition:
    transform 0.25s ease,
    background 0.25s ease,
    box-shadow 0.25s ease;
}
.action-button-background {
  display: none;
}
.main-button-text {
  position: absolute;
  top: 48%;
  left: 50%;
  font-family: AlibabaPuHuiTi, "Noto Sans SC", sans-serif;
  color: #fff;
  font-size: clamp(24px, 2.1vw, 40px);
  font-weight: 600;
  letter-spacing: 4px;
  white-space: nowrap;
  transform: translate(-50%, -50%);

  /*
   * 增强深蓝按钮上的文字识别
   */
  text-shadow:
    0 0 1px rgb(0, 0, 0, 40%),
    0 1px 3px rgb(0, 0, 0, 60%),
    0 3px 6px rgb(0, 0, 0, 40%);
  -webkit-text-stroke: 0.4px rgb(0, 0, 0, 35%);
}
.action-button:hover {
  transform: scale(1.04);
  background: #55a5e5;
  box-shadow: 0 10px 28px rgb(0 50 140 / 48%);
}
.action-button:active {
  transform: scale(0.98);
  background: #327dbb;
}
.settings-button {
  position: absolute;
  top: 40px;
  right: 50px;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 48px;
  height: 48px;
  border: 1px solid rgb(140 195 255 / 50%);
  border-radius: 12px;
  background: rgb(0 0 0 / 25%);
  cursor: pointer;
  user-select: none;
  transition:
    background 0.2s ease,
    border-color 0.2s ease;
}
.settings-button:hover {
  background: rgb(0 0 0 / 40%);
  border-color: rgb(140 195 255 / 80%);
}
.settings-icon {
  color: #fff;
  font-size: 24px;
}

@media (width <= 768px) {
  .system-title-text {
    top: 18%;
    width: 90vw;
    font-size: 32px;
    letter-spacing: 4px;
  }
  .action-buttons {
    top: 64%;
    gap: 24px;
  }
  .action-button {
    width: fit-content;
    height: 72px;
    padding: 0 36px;
  }
  .main-button-text {
    font-size: 20px;
    letter-spacing: 3px;
  }
  .settings-button {
    top: 20px;
    right: 24px;
    width: 44px;
    height: 44px;
  }
  .university-logo {
    top: 20px;
    left: 24px;
    width: 128px;
  }
  .settings-icon {
    font-size: 20px;
  }
}
</style>
