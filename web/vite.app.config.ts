import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { customizationPlugin } from './customization.mjs';

export default defineConfig({
  base: process.env.YADRENO_UI_BASE ?? '/',
  root: fileURLToPath(new URL('.', import.meta.url)), publicDir: false,
  cacheDir: process.env.YADRENO_VITE_CACHE ?? '../tests/.tmp/vite-app', plugins: [customizationPlugin(process.env.YADRENO_CUSTOM_WEB ?? resolve('..', 'custom_web')), react()],
  resolve: { alias: { '@ui': fileURLToPath(new URL('src', import.meta.url)) }, dedupe: ['react', 'react-dom', 'lucide-react'] },
  server: { host: '127.0.0.1', port: 5174, strictPort: true },
  build: { outDir: process.env.YADRENO_UI_OUT ?? '../tests/.tmp/web-core-w2/app-dist', emptyOutDir: true, assetsInlineLimit: 0, target: ['chrome130', 'firefox128', 'safari17.4'],
    rollupOptions: { input: { app: fileURLToPath(new URL('index.html', import.meta.url)), preview: fileURLToPath(new URL('preview.html', import.meta.url)) } } },
});
