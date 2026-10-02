import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

const here = fileURLToPath(new URL('.', import.meta.url));
const prototype = fileURLToPath(new URL('../tests/web_core_ui', import.meta.url));
const cache = fileURLToPath(new URL('../tests/.tmp/vite-w2', import.meta.url));

// W2.0 has an explicit test entry. It is not a production frontend publication.
export default defineConfig({
  root: prototype,
  publicDir: false,
  cacheDir: cache,
  plugins: [react()],
  resolve: {
    alias: {
      '@ui': fileURLToPath(new URL('./src', import.meta.url)),
      react: fileURLToPath(new URL('./node_modules/react', import.meta.url)),
      'react-dom': fileURLToPath(new URL('./node_modules/react-dom', import.meta.url)),
      'lucide-react': fileURLToPath(new URL('./node_modules/lucide-react', import.meta.url)),
      '@fontsource-variable/manrope': fileURLToPath(new URL('./node_modules/@fontsource-variable/manrope', import.meta.url)),
    },
  },
  server: { fs: { allow: [here, prototype, cache] } },
  build: {
    outDir: fileURLToPath(new URL('../tests/.tmp/web-core-w2/ui-dist', import.meta.url)),
    emptyOutDir: true,
    target: ['chrome130', 'firefox128', 'safari17.4'],
  },
});
