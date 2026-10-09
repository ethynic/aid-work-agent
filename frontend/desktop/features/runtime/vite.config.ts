import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Independently compile the feature before H3 adds it to the global product entry.
export default defineConfig({
  plugins: [vue()],
  build: {
    lib: { entry: 'desktop/features/runtime/index.ts', formats: ['es'], fileName: 'runtime-feature' },
    rollupOptions: { external: ['vue'] },
  },
})
