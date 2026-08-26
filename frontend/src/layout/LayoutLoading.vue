<!-- 1.Template 部分 -->
   <!-- layout-loading是你在 style 里写的样式选择器，用来让这个层全屏覆盖并居中显示 -->
  <!-- animate animate-slide-down：自定义入场动画，让 加载loading 层出现时有一个“从上滑入”的动效 -->

    <!--外层 v-if="loading.isLoading"：只有当 loading.isLoading = true 时，这整个 loading 组件才会被创建出来；
      当它变成 false 时，这个 div 会被 Vue 从 DOM 中彻底移除。 -->
    <!-- v-if：元素会被创建/销毁 -->
      <!-- 在 Loading 这里选择 v-if，通常是因为：Loading 层只在启动阶段出现一次，销毁掉也没成本；
          销毁后它不会再占 DOM，也不会挡住点击（更“干净”） -->
<template>
  <div
    class="layout-loading animate animate-slide-down"
    v-if="loading.isLoading"
  >
  <!-- class="loading-gif"用 CSS 背景图显示 GIF,这样写的好处：背景图可以用 background-size: 100% 100% 强制铺满固定区域，控制更直接 -->
    <div class="loading-gif"></div>
    <div class="loading-tip">
      {{ loading.text }} {{ loading.subText }}
    </div>
  </div>
</template>

<!-- 2.Script 部分 -->
<!-- setup：Vue3 的组合式 API 语法糖。写在这里面的变量/函数默认能被模板访问。
     lang="ts"：表示用 TypeScript，这样可以有类型提示、编译期检查。 -->
<script setup lang="ts">
// interface PropsType 这里定义了一个 props 的类型结构，告诉 TS：
// 这个组件必须接收一个 loading 对象；
// 这个对象里必须有：
// total: number
// loaded: number
// isLoading: boolean
// 写这个的目的不是运行时（运行时 Vue 并不会看 interface），而是：
// 让你在父组件传参时有智能提示；
// 让你在模板里写 loading.loaded 不会被 TS 报“可能不存在”。
interface PropsType {
  loading: {
    isLoading: boolean
    text: string
    subText: string
    loaded?: number
    total?: number
  }
}
// const props:<script setup> 的标准写法：声明这个组件的 props
// 这里的 props 变量本身没被用到（只是调用 defineProps 的返回值存了一下），即使写成 defineProps<PropsType>() 也行。
// 作者保留 props 多半是习惯写法或后续要用。
const props = defineProps<PropsType>()
</script>

<!-- 3.Style 部分 -->
 <!-- scoped 表示：这些 CSS 只会作用到本组件的 DOM，不会污染全局 -->
  <!-- Vue 会在编译时给本组件元素加一个类似 data-v-xxxx 的属性选择器，从而把样式“隔离” -->
<style lang="scss" scoped>
.layout-loading {
  // position: absolute + top/left + width/height 100%
  // 让这个层覆盖整个父级（也就是 middle 的 div（Layout.vue 的 .main-middle）），并且内容居中显示
  position: absolute;
  top: 0;
  left: 0;

  // z-index: 99999;
  // 把 loading 的 z-index 拉很高保证它压在所有 3D canvas、标签、面板之上
  z-index: 99999;
  display: flex;

  // flex-direction: column：让 gif 在上，文字在下，按列排列。中间一个大 GIF + 下面一行提示
  flex-direction: column;

  // align-items: center：横向居中
  align-items: center;

  // justify-content: center：主轴）居中
  justify-content: center;
  width: 100%;
  height: 100%;
  background: #000;
  .loading-gif {
    // 假设：设计稿：1920×1080。1000px 是在“1920×1080 设计稿坐标系下”的 1000px
    // 如果屏幕放大 1.333 倍，真实显示宽度就变成：1000 × 1.333 ≈ 1333px
    width: 1000px;
    height: 250px;
    background-image: url('@/assets/images/loading.gif');
    background-repeat: no-repeat;

    // background-size: 100% 100% 会把 GIF 强行缩放到 1000×250 这个固定区域里
    background-size: 100% 100%;
  }
  .loading-tip {
    // 让提示文字用 DouyuFont、斜体、加粗，颜色是蓝绿色
    // font-family: DOUYUFont;
    // font-style: italic;
    // font-weight: bolder;
    color: #5fb9c7;
  }
}
.animate-slide-down {
  animation: loading-slide-down 0.45s ease-out both;
}

@keyframes loading-slide-down {
  from {
    transform: translateY(-100%);
    opacity: 0;
  }
  to {
    transform: translateY(0);
    opacity: 1;
  }
}
</style>