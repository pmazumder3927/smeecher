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
      // Match API paths (exclude @vite, node_modules, src, and files with extensions)
      '^/(?!@|node_modules|src)[^.]+$': {
        target: 'http://localhost:8000',
      }
    }
  }
})
