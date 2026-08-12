import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The build lands in web/dist, which core/api/app.py serves as static files —
// ONE ORIGIN, one deploy, no CORS, and the frontend can never ship at a
// version that disagrees with the API. In development the proxy points /api at
// the local FastAPI process so the frontend code is identical in both modes.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Pin IPv4: on Windows, Node resolves localhost to ::1, so an unpinned dev
    // server binds IPv6 only and http://127.0.0.1:5173 serves nothing. Browsers
    // fall back to 127.0.0.1 for localhost, so pinning IPv4 makes both URLs work.
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
