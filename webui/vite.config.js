import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8768',
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    // Three.js ayrı ve tembel yüklenen bir chunk; ilk ekran paketini şişirmez.
    chunkSizeWarningLimit: 600,
  },
})
