import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pageTitles } from '../page-titles.mjs';

/** Serve the same platform entry points in Vite; custom sources own no HTML boot. */
export function developmentApplication(platform, source, version) {
  const moduleUrl = name => '/@fs/' + resolve(platform, 'platform', name).replaceAll('\\', '/');
  return {
    name: 'platform-development-entry', apply: 'serve',
    configureServer(server) {
      server.middlewares.use(async (request, response, next) => {
        const path = new URL(request.url ?? '/', 'http://localhost').pathname;
        if (!['/', '/index.html', '/frame.html', '/preview.html'].includes(path)) return next();
        try {
          const frame = path === '/frame.html' || path === '/preview.html';
          const registry = JSON.parse(readFileSync(resolve(source, 'src/runtime/view-registry.json'), 'utf8'));
          const language = process.env.YADRENO_UI_LANGUAGE ?? 'ru';
          const titles = pageTitles(source, registry);
          const pages = titles.pages.map(page => ({ ...page, title: titles.localized?.languages[language]?.[page.id] ?? page.title }));
          const descriptor = { entry: moduleUrl('app-entry.tsx'), styles: [], pages, language,
            version, build_version: 'development', instance_id: 'development', asset_base: '/',
            platform_version: 'development', frame_url: '/frame.html', mode: path === '/preview.html' ? 'preview' : 'live' };
          const html = readFileSync(resolve(platform, 'platform', frame ? 'frame.html' : 'index.html'), 'utf8')
            .replace('__APPLICATION_DESCRIPTOR__', JSON.stringify(descriptor).replaceAll('<', '\\u003c'))
            .replace('lang="ru"', `lang="${language}" dir="${['ar', 'fa'].includes(language) ? 'rtl' : 'ltr'}"`)
            .replace('__PAGE_TITLE__', 'Yadreno VPN')
            .replace(frame ? './frame.ts' : './main.tsx', moduleUrl(frame ? 'frame.ts' : 'main.tsx'));
          response.setHeader('Content-Type', 'text/html; charset=utf-8');
          response.setHeader('Cache-Control', 'no-store');
          response.end(await server.transformIndexHtml(path, html));
        } catch (error) { next(error); }
      });
    },
  };
}
