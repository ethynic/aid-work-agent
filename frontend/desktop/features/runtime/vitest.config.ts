import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
export default defineConfig({ plugins: [vue()], test: { environment: 'jsdom', include: ['desktop/features/runtime/__tests__/*.test.ts'] } })
