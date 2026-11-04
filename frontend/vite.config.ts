import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8765' } },
  build: {
    outDir: '../src/borethm_workbench/static',
    emptyOutDir: true,
    rollupOptions: { output: { manualChunks: id => id.includes('plotly.js-dist-min') ? 'plotly' : id.includes('/three/') ? 'three' : undefined } }
  }
});
