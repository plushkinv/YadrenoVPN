import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const platform = fileURLToPath(new URL('.', import.meta.url));
export default defineConfig({
  root: resolve(platform, 'platform'), publicDir: false,
  base: process.env.YADRENO_PLATFORM_BASE ?? '/', plugins: [react()],
  cacheDir: process.env.YADRENO_VITE_CACHE ?? '../tests/.tmp/vite-platform',
  build: { outDir: process.env.YADRENO_PLATFORM_OUT ?? '../../tests/.tmp/platform-dist', emptyOutDir: true,
    assetsInlineLimit: 0, target: ['chrome130', 'firefox128', 'safari17.4'],
    rollupOptions: { input: { index: resolve(platform, 'platform/index.html'), frame: resolve(platform, 'platform/frame.html') } } },
});
