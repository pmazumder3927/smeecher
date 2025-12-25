import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

// https://vite.dev/config/
export default defineConfig({
  plugins: [svelte()],

  // Build output to static directory for FastAPI to serve
  build: {
    outDir: '../static',
    emptyOutDir: true,
  },

  // Proxy API requests to FastAPI during development
  server: {
    proxy: {
      '/search': 'http://localhost:8000',
      '/search-index': 'http://localhost:8000',
      '/graph': 'http://localhost:8000',
      '/clusters': 'http://localhost:8000',
      '/stats': 'http://localhost:8000',
      '/realtime-session': 'http://localhost:8000',
      '/voice-vocab': 'http://localhost:8000',
      '/voice-session-config': 'http://localhost:8000',
      '/unit-items': 'http://localhost:8000',
      '/unit-build': 'http://localhost:8000',
    }
  }
})
