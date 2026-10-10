import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { frontendAliases } from './config/aliases'
import { moduleManifest } from './config/moduleManifest'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [vue(), moduleManifest('web-module-manifest.json')],
    resolve: {
      alias: frontendAliases,
    },
    build: {
      cssCodeSplit: true,
      rollupOptions: {
        output: {
          manualChunks: {
            'vue-vendor': ['vue', 'vue-router'],
            'markdown-renderer': ['marked', 'marked-highlight', 'highlight.js'],
            'ui-libs': ['vue-toastification'],
            'http-client': ['axios'],
            // Office PPT 预览懒加载库：仅动态 import 引用，仍按需加载；
            // 显式命名避免与 echarts 等依赖合并成无名 index chunk，便于分包核验。
            // echarts（~1MB，pptx-preview 硬依赖）单独拆 chunk：
            // 合并后 pptx chunk 超 1000 kB，vite 经 npm 管道输出时尺寸带千分位逗号
            // （"1,352.26 kB"），会破坏构建产物解析
            'pptx-preview': ['pptx-preview'],
            'echarts': ['echarts'],
          }
        }
      }
    },
    server: {
      host: '0.0.0.0',
      port: parseInt(env.DEV_PORT || '3000', 10), //从前端 env 中获取端口号，默认3000
      proxy: {
        '/api': { // 代理到后端容器的接口
          target: 'http://127.0.0.1:8000',
          changeOrigin: true
        }
      }
    }
  }
})
