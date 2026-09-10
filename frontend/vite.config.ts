import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    // MapLibre alone is ~800 kB; it is split into its own chunk deliberately.
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        // MapLibre is by far the largest dependency; splitting it keeps the
        // initial app bundle small and lets the map load in parallel.
        manualChunks: {
          maplibre: ['maplibre-gl'],
          react: ['react', 'react-dom'],
          query: ['@tanstack/react-query'],
        },
      },
    },
  },
});
