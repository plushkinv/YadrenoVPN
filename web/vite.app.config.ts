import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { readFileSync, writeFileSync, unlinkSync, existsSync } from 'node:fs';
import { readCustomization } from './customization.mjs';
import { developmentApplication } from './platform/development.mjs';

const platform = fileURLToPath(new URL('.', import.meta.url));
const source = process.env.YADRENO_CUSTOM_WEB ?? platform;
const metadata = readCustomization(source);
const dependencies = Object.keys(JSON.parse(readFileSync(resolve(platform, 'package.json'), 'utf8')).dependencies);
const output = process.env.YADRENO_UI_OUT ?? resolve(platform, '../tests/.tmp/web-core-w2/app-dist');
const sdk = [
  ['@ui/context', 'runtime/context.tsx'], ['@ui/client', 'api/client.ts'], ['@ui/contracts', 'api/contracts.ts'],
  ['@ui/environment', 'runtime/environment.ts'], ['@ui/storage', 'runtime/application-storage.ts'],
].map(([find, name]) => ({ find, replacement: resolve(platform, 'platform', name) }));

export default defineConfig({
  base: process.env.YADRENO_UI_BASE ?? '/',
  root: source, publicDir: resolve(source, 'public'),
  cacheDir: process.env.YADRENO_VITE_CACHE ?? '../tests/.tmp/vite-app', plugins: [react(), developmentApplication(platform, source, metadata.version), {
    name: 'application-descriptor', apply: 'build',
    closeBundle() {
      const path = resolve(output, 'application.vite.json');
      const manifest = JSON.parse(readFileSync(path, 'utf8'));
      const entry = Object.values(manifest).find((item: any) => item.isEntry) as any;
      if (!entry) throw new Error('Application entry missing');
      const styles = new Set<string>();
      const collect = (item: any) => { for (const key of item.imports ?? []) collect(manifest[key]); for (const file of item.css ?? []) styles.add(file); };
      collect(entry);
      const registry = JSON.parse(readFileSync(resolve(source, 'src/runtime/view-registry.json'), 'utf8'));
      const labelPath = resolve(source, 'src/i18n/app.ts');
      const labels = existsSync(labelPath) ? readFileSync(labelPath, 'utf8') : '';
      const pages = registry.pages.map((page: any) => ({ id: page.id,
        title: page.title ?? (page.title_key ? new RegExp('\\b' + page.title_key + ":\\s*'([^']*)'").exec(labels)?.[1] : undefined) ?? page.id,
        ...(page.preview_parameter ? { preview_parameter: page.preview_parameter } : {}) }));
      writeFileSync(resolve(output, 'application.json'), JSON.stringify({ format_version: 1, entry: entry.file, styles: [...styles], pages }));
      unlinkSync(path);
    },
  }],
  define: { __UI_BUILD__: JSON.stringify({ version: process.env.YADRENO_UI_CUSTOMIZATION ?? metadata.version,
    build_version: process.env.YADRENO_UI_BUILD ?? 'development', instance_id: process.env.YADRENO_UI_INSTANCE ?? 'development',
    asset_base: process.env.YADRENO_UI_BASE ?? '/' }) },
  resolve: { alias: [...sdk, { find: '@application', replacement: resolve(source, 'src') }, { find: '@ui', replacement: resolve(source, 'src') },
    ...dependencies.map(name => ({ find: name, replacement: resolve(platform, 'node_modules', name) }))],
    dedupe: ['react', 'react-dom', 'lucide-react'] },
  server: { host: '127.0.0.1', port: 5174, strictPort: true, fs: { allow: [platform, source] },
    cors: { origin: ['null', /^https?:\/\/(?:localhost|127\.0\.0\.1)(?::\d+)?$/] } },
  build: { outDir: output, emptyOutDir: true, assetsInlineLimit: 0, manifest: 'application.vite.json', target: ['chrome130', 'firefox128', 'safari17.4'],
    rollupOptions: { input: { application: resolve(platform, 'platform/app-entry.tsx') }, preserveEntrySignatures: 'strict' } },
});
