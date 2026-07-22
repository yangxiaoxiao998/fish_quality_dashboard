<template>
  <div class="layout-page">
    <!-- 顶部 -->
    <LayoutHeader />

    <!-- 主体 -->
    <div class="layout-main" :class="{ 'no-left': !hasLeftSlot }">
      <!-- 左侧：有 slot 才渲染 -->
      <div v-if="hasLeftSlot" class="main-left">
        <slot name="left" />
      </div>

      <!-- 中间：视频流区域 -->
      <div class="main-middle">
        <slot name="middle" />
        <LayoutLoading :loading="loading" />
      </div>

      <!-- 右侧 -->
      <div class="main-right">
        <slot name="right" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, useSlots } from "vue";
import LayoutHeader from "./LayoutHeader.vue";
import LayoutFooter from "./LayoutFooter.vue";
import LayoutLoading from "./LayoutLoading.vue";

const slots = useSlots();

/**
 * 是否提供 left slot：
 * - 用户要求删掉左侧面板时，Dashboard 不传 left
 * - Layout 自动收缩 left 列，让 middle 更宽
 */
const hasLeftSlot = computed(() => {
  // Vue 的 slot 在运行时是函数，存在即代表可能会渲染内容
  // 这里严格判断：存在且执行后有节点
  const fn = slots.left;
  if (!fn) return false;
  const vnodes = fn();
  return Array.isArray(vnodes) && vnodes.length > 0;
});

/**
 * loading 状态：
 * - 先保留结构；后续你接“开始处理/连接流”时，从外部注入或用全局 store 管理
 * - 现在默认不遮罩（isLoading=false）
 */
const loading = reactive({
  isLoading: false,
  text: "正在加载资源",
  subText: "初次加载资源可能需要较长时间,请耐心等待",
});
</script>

<style lang="scss" scoped>
.layout-page {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;

  // 背景是图片assets/images/page_bg.png，重复平铺，覆盖整个页面
  background: url("../assets/images/page_bg.png") repeat
}
.layout-main {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 0.4fr 1fr 0.4fr;
  gap: 16px;
  padding: 0 12px 12px;
  box-sizing: border-box;
  overflow: hidden;
}
.layout-main.no-left {
  grid-template-columns: 1fr 0.4fr;
}
.main-left,
.main-middle,
.main-right {
  height: 100%;
  min-height: 0;
  overflow: hidden;
}
.main-middle {
  position: relative;
}
</style>