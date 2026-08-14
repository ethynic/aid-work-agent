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
        input: path.resolve(__dirname, legacyDevelopmentEntry ? 'desktop.legacy.html' : 'desktop.html'),
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
