<template>
  <div class="layout-header">
    <div class="header-midden">
      <div class="cn">鱼质检测仪数据大屏</div>
      <div class="en">Fish Quality Monitoring Dashboard</div>
    </div>
    <div class="header-right">
      <button 
        class="nav-btn" 
        :class="{ active: isDashboard }" 
        @click="goToDashboard"
      >
        <i class="fa-solid fa-desktop"></i>
         返回首页
      </button>
      <button 
        class="nav-btn" 
        :class="{ active: isAdmin }" 
        @click="goToAdmin"
      >
        <i class="fa-solid fa-gear"></i>
        数据管理
      </button>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'

const router = useRouter()
const route = useRoute()

const isDashboard = computed(() => route.path === '/dashboard')
const isAdmin = computed(() => route.path.startsWith('/admin'))

function goToDashboard() {
  router.push('/')
}

function goToAdmin() {
  router.push('/admin/data-report')
}
</script>
<style lang="scss" scoped>
@mixin font-color {
  background: linear-gradient(0deg, #b9cfff 0%, #fff 99%);
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

@keyframes light-go {
  from {
    left: 500px;
  }
  to {
    left: 1100px;
    opacity: 0;
  }
}
.layout-header {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
   height: 80px;
  background-image: url(@/assets/images/title_bg.png);
  background-repeat: no-repeat;
  background-position: center top;
  background-size: 100% 100%;
  &::after {
    position: absolute;
    bottom: -55px;
    left: 500px;
    width: 500px;
    height: 100px;
    content: '';
    background-image: url(@/assets/images/light_bg.png);
    background-repeat: no-repeat;
    background-size: contain;
    animation: light-go 3s ease-in-out infinite forwards;
  }
  .header-midden {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    overflow-x: hidden;
    font-family: DouyuFont;
    color: #fff;
    .cn {
      font-size: 30px;

      @include font-color;
    }
    .en {
      position: relative;
      font-size: 10px;

      @include font-color;
    }
  }
  .header-right {
    position: absolute;
    top: 50%;
    right: 30px;
    transform: translateY(-50%);
    display: flex;
    gap: 20px;
    .nav-btn {
      padding: 10px 24px;
      border-radius: 20px;
      border: 1px solid rgb(0, 168, 232, 50%);
      background: rgb(26, 26, 46, 60%);
      color: #fff;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 14px;
      transition: all 0.3s ease;
      &:hover {
        background: rgb(0, 168, 232, 30%);
      }
      &.active {
        background: linear-gradient(135deg, #00a8e8 0%, #00f5d4 100%);
        border-color: rgb(0, 168, 232, 60%);
      }
    }
  }
}
</style>
