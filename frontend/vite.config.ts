/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Dev convenience: forward API + health calls to the local backend so no
      // absolute backend URL is hard-coded in the browser bundle.
      '/api': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/ready': 'http://localhost:8000',
    },
  },
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
  },
})