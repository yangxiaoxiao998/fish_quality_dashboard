import { createApp } from 'vue'
import App from './App.vue'
import './style.css'
import '@/assets/fonts/DincorosBlack/result.css'
import '@/assets/fonts/DouyuFont/result.css'
import '@/assets/fonts/SarasaMonoSC/result.css'
import '@/assets/fontawesome/css/all.css'
import router from './router'
import ArcoVue from '@arco-design/web-vue'
import '@arco-design/web-vue/dist/arco.css'

const app = createApp(App)
app.use(router)
app.use(ArcoVue)
app.mount('#app')
// 页面布局使用浏览器视口原生尺寸；ECharts 通过 ResizeObserver 适配容器变化。
