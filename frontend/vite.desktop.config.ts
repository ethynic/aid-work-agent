import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'
import { frontendAliases } from './config/aliases'
import { moduleManifest } from './config/moduleManifest'

export default defineConfig(({ command }) => {
  const legacyDevelopmentEntry = command === 'serve' && process.env.VITE_DESKTOP_LEGACY_UI === '1'
  return {
    base: '/',
    plugins: [vue(), moduleManifest('desktop-module-manifest.json')],
    resolve: {
      alias: frontendAliases,
    },
    build: {
      outDir: 'dist-desktop',
      emptyOutDir: true,
      manifest: true,
      cssCodeSplit: true,
      rollupOptions: {
        // 多入口：desktop 管理界面 + screen 隐藏采集页（设计 6.1）。采集页不引用共享 API，
        // 也不触碰 window.agentDesktop，因此不受上面 VITE_API_BASE_URL define 的影响
        input: {
          desktop: path.resolve(__dirname, legacyDevelopmentEntry ? 'desktop.legacy.html' : 'desktop.html'),
          screen: path.resolve(__dirname, 'screen.html'),
        },
        output: {
          manualChunks: {
            'vue-vendor': ['vue', 'vue-router'],
          }
        }
      }
    },
    define: {
      // preload 在任何 renderer 脚本之前注入经过 main 严格校验的地址。
      // 这样现有 API 模块无需回退到 aidagent://app/api，也不会把发行地址固化进 bundle。
      'import.meta.env.VITE_API_BASE_URL': 'window.agentDesktop.runtime.apiBaseUrl',
      'import.meta.env.VITE_API_BASE': 'window.agentDesktop.runtime.apiOrigin',
      'import.meta.env.VITE_DESKTOP_TARGET': JSON.stringify('true')
    }
  }
})
