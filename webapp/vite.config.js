import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true
    // Se NON abiliti CORS nel coordinator, puoi usare questo proxy dev:
    // proxy: {
    //   '/key': { target: 'http://localhost:8000', changeOrigin: true },
    //   '/sharding': { target: 'http://localhost:8000', changeOrigin: true },
    //   '/health': { target: 'http://localhost:8000', changeOrigin: true }
    // }
  },
  preview: {
    port: 4173,
    strictPort: true
  }
})