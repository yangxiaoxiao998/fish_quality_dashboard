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
// 1920x1080 固定缩放仅由 Dashboard.vue 局部控制 (useFixedScreen)，
// 不在这里做全局 autofit.init，避免影响 /admin 等页面的布局。
// 详见 deploy/CLIENT_DEPLOY_README.md 和 src/utils/useFixedScreen.ts。