import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { readFileSync } from 'node:fs';
import { readCustomization } from './customization.mjs';

const platform = fileURLToPath(new URL('.', import.meta.url));
const source = process.env.YADRENO_CUSTOM_WEB ?? platform;
const metadata = readCustomization(source);
const dependencies = Object.keys(JSON.parse(readFileSync(resolve(platform, 'package.json'), 'utf8')).dependencies);

export default defineConfig({
  base: process.env.YADRENO_UI_BASE ?? '/',
  root: source, publicDir: resolve(source, 'public'),
  cacheDir: process.env.YADRENO_VITE_CACHE ?? '../tests/.tmp/vite-app', plugins: [react()],
  define: { __UI_BUILD__: JSON.stringify({ version: process.env.YADRENO_UI_CUSTOMIZATION ?? metadata.version,
    build_version: process.env.YADRENO_UI_BUILD ?? 'development', instance_id: process.env.YADRENO_UI_INSTANCE ?? 'development',
    asset_base: process.env.YADRENO_UI_BASE ?? '/' }) },
  resolve: { alias: [{ find: '@ui', replacement: resolve(source, 'src') },
    ...dependencies.map(name => ({ find: name, replacement: resolve(platform, 'node_modules', name) }))],
    dedupe: ['react', 'react-dom', 'lucide-react'] },
  server: { host: '127.0.0.1', port: 5174, strictPort: true },
  build: { outDir: process.env.YADRENO_UI_OUT ?? '../tests/.tmp/web-core-w2/app-dist', emptyOutDir: true, assetsInlineLimit: 0, target: ['chrome130', 'firefox128', 'safari17.4'],
    rollupOptions: { input: { app: resolve(source, 'index.html'), preview: resolve(source, 'preview.html') } } },
});
