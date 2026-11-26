"项目构建配置"
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import { visualizer } from 'rollup-plugin-visualizer'
import vue from '@vitejs/plugin-vue'
import vueJsx from '@vitejs/plugin-vue-jsx'
import stylelintPlugin from 'vite-plugin-stylelint'
import path from 'path'


export default defineConfig({
  // Both Vite and the packaged Electron renderer are served over HTTP at the
  // origin root. An absolute base keeps assets valid after SPA deep links such
  // as /dashboard or /admin/data-report are opened directly.
  base: '/',
  build: {
    outDir: './docs',
    assetsDir: 'assets',
    rollupOptions: {
      output: {
        chunkFileNames: 'js/[name]-[hash].js', // 引入文件名的名称
        entryFileNames: 'js/[name]-[hash].js', // 包的入口文件名称
        assetFileNames: 'assets/[name].[ext]', // 资源文件名称
        //把 node_modules 里的库拆分成独立 chunk,浏览器缓存更好,首屏加载更快,大屏项目对性能要求高
        manualChunks(id) {
          if (id.includes('node_modules')) {
            return id
              .toString()
              .split('node_modules/')[1]
              .split('/')[0]
              .toString()
          }
        },
      },
    },
  },
  plugins: [
    vue(),
    vueJsx(),
    stylelintPlugin({
      //启动时对样式做代码规范检查
      configBasedir: path.resolve(__dirname),
      include: ['src/**/*.{vue,scss,sass,css}'],
      exclude: ['src/assets/fontawesome/**', 'node_modules/**'],
    }),
    visualizer({ open: true }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  css: {
    preprocessorOptions: {
      scss: {
        // api: 'modern-compiler',
      },
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    open: false,
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/static': {
        target: process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
