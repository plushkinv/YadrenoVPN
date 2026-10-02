import base from './vite.config.ts';
import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';
import { customizationPlugin } from './customization.mjs';

export default defineConfig({ ...base, plugins: [...base.plugins ?? [], customizationPlugin(process.env.YADRENO_CUSTOM_WEB ?? fileURLToPath(new URL('../custom_web', import.meta.url)))], server: { ...base.server, host: '127.0.0.1', port: 5175, strictPort: true },
  build: { ...base.build, outDir: '../.tmp/web-core-w2/simulator-dist',
    rollupOptions: { input: fileURLToPath(new URL('../tests/web_core_ui/simulator.html', import.meta.url)) } } });
