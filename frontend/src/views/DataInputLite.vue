<template>
  <main class="data-input-placeholder">
    <section class="data-input-placeholder-panel">
      <h1 class="data-input-placeholder-title">数据录入页面待开发</h1>
      <p class="data-input-placeholder-desc">
        当前页面为临时占位页，后续将接入摄像头录入功能。
      </p>
      <div class="data-input-placeholder-actions">
        <button
          type="button"
          class="data-input-placeholder-btn"
          @click="goHome"
        >
          返回首页
        </button>
        <button
          type="button"
          class="data-input-placeholder-btn"
          @click="goDashboard"
        >
          进入原完整首页
        </button>
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()

function goHome(): void {
  router.push('/')
}

function goDashboard(): void {
  router.push('/dashboard')
}
</script>

<style scoped>
.data-input-placeholder {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  min-height: 100dvh;
  overflow: hidden;
  color: #fff;
  background:
    radial-gradient(circle at 50% 35%, rgb(0 176 255 / 18%), transparent 34%),
    linear-gradient(135deg, #031533 0%, #061f48 52%, #020a18 100%);
}
.data-input-placeholder-panel {
  width: min(680px, calc(100vw - 48px));
  padding: 42px 48px;
  border: 1px solid rgb(120 210 255 / 32%);
  border-radius: 24px;
  text-align: center;
  background: rgb(6 24 56 / 54%);
  box-shadow:
    0 20px 60px rgb(0 0 0 / 28%),
    inset 0 0 32px rgb(0 180 255 / 8%);
  backdrop-filter: blur(10px);
}
.data-input-placeholder-title {
  margin: 0;
  color: #fff;
  font-size: clamp(28px, 3vw, 42px);
  font-weight: 800;
  letter-spacing: 4px;
  text-shadow: 0 0 16px rgb(0 180 255 / 32%);
}
.data-input-placeholder-desc {
  margin: 18px 0 0;
  color: rgb(220 244 255 / 78%);
  font-size: 16px;
  line-height: 1.8;
}
.data-input-placeholder-actions {
  display: flex;
  gap: 18px;
  justify-content: center;
  margin-top: 32px;
}
.data-input-placeholder-btn {
  min-width: 136px;
  height: 40px;
  padding: 0 22px;
  border: 1px solid rgb(120 210 255 / 46%);
  border-radius: 999px;
  color: rgb(238 250 255 / 94%);
  font-size: 15px;
  font-weight: 600;
  background: rgb(8 42 92 / 54%);
  cursor: pointer;
  transition:
    background 0.2s ease,
    border-color 0.2s ease,
    transform 0.2s ease;
}
.data-input-placeholder-btn:hover {
  border-color: rgb(120 230 255 / 76%);
  background: rgb(13 70 138 / 68%);
  transform: translateY(-2px);
}
.data-input-placeholder-btn:active {
  transform: translateY(0);
}

@media (width <= 768px) {
  .data-input-placeholder-panel {
    padding: 32px 24px;
  }
  .data-input-placeholder-actions {
    flex-direction: column;
    align-items: center;
  }
  .data-input-placeholder-btn {
    width: min(220px, 100%);
  }
}
</style>
