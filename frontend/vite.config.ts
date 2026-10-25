import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev server proxies /api and /files to the FastAPI backend, so the app uses the
// same relative URLs in development and when FastAPI serves the built dist/.
const backend = process.env.VITE_BACKEND ?? 'http://127.0.0.1:8010'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    strictPort: true,
    proxy: { '/api': backend, '/files': backend },
  },
})
