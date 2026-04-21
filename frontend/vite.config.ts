import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, 'src')
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
