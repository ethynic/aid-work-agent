import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

function desktopModuleManifest() {
  return {
    name: 'desktop-module-manifest',
    generateBundle(_: unknown, bundle: Record<string, { type: string; modules?: Record<string, unknown> }>) {
      const modules = new Set<string>()
      for (const output of Object.values(bundle)) {
        if (output.type !== 'chunk' || !output.modules) continue
        for (const moduleId of Object.keys(output.modules)) {
          const cleanId = moduleId.split('?')[0]
          if (path.isAbsolute(cleanId)) {
            modules.add(path.relative(process.cwd(), cleanId).replaceAll('\\', '/'))
          }
        }
      }
      this.emitFile({
        type: 'asset',
        fileName: 'desktop-module-manifest.json',
        source: `${JSON.stringify([...modules].sort(), null, 2)}\n`
      })
    }
  }
}

export default defineConfig(() => {
  return {
    base: '/',
    plugins: [vue(), desktopModuleManifest()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, 'web')
      }
    },
    build: {
      outDir: 'dist-desktop',
      emptyOutDir: true,
      manifest: true,
      cssCodeSplit: true,
      rollupOptions: {
        input: path.resolve(__dirname, 'desktop.html'),
        output: {
          manualChunks: {
            'vue-vendor': ['vue', 'vue-router'],
            'markdown-renderer': ['marked', 'marked-highlight', 'highlight.js'],
            'ui-libs': ['vue-toastification'],
            'http-client': ['axios'],
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
