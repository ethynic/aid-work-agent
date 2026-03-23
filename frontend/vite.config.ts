import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src')
    }
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        // 关键：禁用 HTTP 缓冲以支持 SSE 流式响应
        proxyTimeout: 0,  // 禁用代理超时
        // 自定义配置
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq, req) => {
            // 确保请求头正确
            req.headers['Accept'] = 'text/event-stream'
          })
          proxy.on('proxyRes', (proxyRes, req, res) => {
            // SSE 流式响应需要特殊处理
            if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
              // 禁用缓冲
              proxyRes.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
              proxyRes.headers['X-Accel-Buffering'] = 'no'
              proxyRes.headers['Connection'] = 'keep-alive'
              // 确保 Transfer-Encoding 是 chunked
              if (!proxyRes.headers['transfer-encoding']) {
                proxyRes.headers['Transfer-Encoding'] = 'chunked'
              }
            }
          })
        }
      }
    }
  }
})
