import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import path from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'web')
    }
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['web/__tests__/setup.ts'],
    include: ['web/__tests__/**/*.test.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      include: ['web/**/*.{ts,vue}'],
      exclude: ['web/main.ts', 'web/vite-env.d.ts']
    }
  }
})
