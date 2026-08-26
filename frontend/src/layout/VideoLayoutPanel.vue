<template>
  <div class="layout-panel animate animate-pop-in">
    <div class="panel-header">
      <div class="panel-header-title">{{ title }}</div>
    </div>
    <div class="panel-body">
      <slot></slot>
    </div>
  </div>
</template>
<script setup lang="ts">
interface PropsType {
  title?: string | number
}
const props = defineProps<PropsType>()
</script>
<style lang="scss" scoped>
.layout-panel {
  position: relative;
  height: 100%;
  color: #fff;
  background-image: url('@/assets/images/panel_body_bg.png');
  background-repeat: no-repeat;
  background-size: 100% 100%;
    .panel-header {
    display: flex;
    align-items: center;
      height: 65px;
    font-family: DouyuFont;
    background-image: url('@/assets/images/panel_title_bg.png');
    background-repeat: no-repeat;
    background-size: 100% 100%;
    .panel-header-title {
      position: relative;
      top: -8px;
        left: 250px;
        font-size: 18px;
    }
  }
  .panel-body {
    box-sizing: border-box;

    // height: 220px;
    height: calc(100% - 65px);
    padding: 0 10px 10px;
    overflow: hidden;
  }
}
.animate-pop-in {
  animation: layout-pop-in 0.45s ease-out both;
}

@keyframes layout-pop-in {
  from {
    transform: scale(0.85);
    opacity: 0;
  }
  to {
    transform: scale(1);
    opacity: 1;
  }
}
</style>
