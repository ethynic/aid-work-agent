import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

// 版本号 - 每次发版时手动更新
const VERSION = '202603062045'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src')
    }
  },
  build: {
    target: 'es2015',
    rollupOptions: {
      output: {
        // JS 文件添加版本参数
        entryFileNames: `assets/[name].${VERSION}.js`,
        // CSS 文件添加版本参数
        chunkFileNames: `assets/[name].${VERSION}.js`,
        // 异步 chunk 也添加版本参数
        assetFileNames: (assetInfo) => {
          if (assetInfo.name && /\.css$/.test(assetInfo.name)) {
            return `assets/[name].${VERSION}.css`
          }
          return `assets/[name].${VERSION}.[ext]`
        }
      }
    }
  },
  server: {
    port: 8082,
    open: true
  }
})
