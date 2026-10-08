import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The backend, started by `make dev`.
const backend = 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Send the API and audio URLs to the backend, so the browser sees one site
  // (no CORS setup needed in the backend).
  server: {
    proxy: {
      '/profile': backend,
      '/episodes': backend,
      '/metrics': backend,
      '/media': backend,
    },
  },
})
