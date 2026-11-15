import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'
import { buildInfo } from './scripts/build-info.mjs'

const API = process.env.VITE_API_TARGET || 'http://127.0.0.1:8100'

export default defineConfig({
  plugins: [vue(), { name: 'jucheng-build-info', generateBundle() {
    this.emitFile({ type: 'asset', fileName: 'build-info.json', source: JSON.stringify({ ...buildInfo(), built_at: new Date().toISOString() }) })
  } }],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    host: '0.0.0.0',
    port: Number(process.env.PORT) || 5180,
    proxy: { '/api': API, '/mcp': API, '/health': API },
  },
  build: {
    outDir: process.env.VITE_OUT_DIR || 'dist',
    chunkSizeWarningLimit: 1600,
    rollupOptions: {
      output: {
        manualChunks: { echarts: ['echarts'], 'element-plus': ['element-plus'] },
      },
    },
  },
})
