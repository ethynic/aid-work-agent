import { defineConfig } from 'vitest/config'
export default defineConfig({ test: { environment: 'jsdom', include: ['desktop/features/screen-capture/__tests__/*.test.ts'] } })
