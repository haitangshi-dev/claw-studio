import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    strictPort: false,
    proxy: { '/api': 'http://127.0.0.1:8090' },
  },
  build: {
    outDir: 'dist',
    rollupOptions: {
      input: { main: 'index.html', admin: 'admin.html', demo: 'demo.html', preview: 'preview.html' },
    },
  },
})
