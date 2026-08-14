import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { frontendAliases } from './config/aliases'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: frontendAliases,
  },
  test: {
    projects: [
      {
        extends: true,
        test: {
          name: 'web',
          globals: true,
          environment: 'jsdom',
          setupFiles: ['web/__tests__/setup.ts'],
          include: ['web/__tests__/**/*.test.ts'],
        },
      },
      {
        extends: true,
        test: {
          name: 'shared',
          globals: true,
          environment: 'jsdom',
          include: ['shared/__tests__/**/*.test.ts'],
        },
      },
      {
        extends: true,
        test: {
          name: 'desktop',
          globals: true,
          environment: 'jsdom',
          setupFiles: ['desktop/__tests__/setup.ts'],
          include: ['desktop/__tests__/**/*.test.ts'],
        },
      },
    ],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      include: ['web/**/*.{ts,vue}', 'shared/**/*.{ts,vue}', 'desktop/**/*.{ts,vue}'],
      exclude: ['web/main.ts', 'web/vite-env.d.ts']
    }
  }
})
