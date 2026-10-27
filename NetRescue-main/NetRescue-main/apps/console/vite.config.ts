/**
 * Vite config for the NetRescue dashboard.
 *
 * The API base URL comes from VITE_API_BASE_URL so the same build can point at a
 * different backend without a code change. It defaults to the local API, and the
 * dev server proxies nothing — the browser talks to the API directly, which is
 * what CORS in apps/api is for.
 */
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  server: {
    port: 5173,
    strictPort: true,
    host: '127.0.0.1',
  },
  preview: { port: 4173, host: '127.0.0.1' },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
