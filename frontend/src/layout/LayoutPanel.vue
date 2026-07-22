<template>
  <div class="layout-panel animate animate__bounceIn">
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
      left: 90px;
      font-size: 18px;
    }
  }
  .panel-body {
    box-sizing: border-box;

    // height: 220px;
    height: calc(100% - 65px);
    padding: 0 10px 5px; // 增加顶部内边距，为向上偏移的图片提供更多空间。
    // padding: 20px 10px 10px 10px; 表示上、右、下、左四个方向的 padding 分别为 20px、10px、10px、10px
    overflow: hidden;
  }
}
</style>