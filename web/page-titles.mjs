import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';

/** Optional signed resource; old complete custom trees keep their original labels. */
export function pageTitles(source, registry) {
  const folder = resolve(source, 'src/i18n/locales');
  const labels = resolve(source, 'src/i18n/app.ts');
  const legacy = existsSync(labels) ? readFileSync(labels, 'utf8') : '';
  const title = (page, catalog = {}) => page.title ?? catalog['app.' + page.title_key]
    ?? (page.title_key ? new RegExp('\\b' + page.title_key + ":\\s*'([^']*)'").exec(legacy)?.[1] : undefined) ?? page.id;
  const catalogs = existsSync(folder) ? Object.fromEntries(readdirSync(folder).filter(name => /^[a-z]{2}(?:-[A-Z]{2})?\.json$/.test(name))
    .map(name => [name.slice(0, -5), JSON.parse(readFileSync(resolve(folder, name), 'utf8'))])) : {};
  return {
    pages: registry.pages.map(page => ({ id: page.id, title: title(page, catalogs.ru),
      ...(page.preview_parameter ? { preview_parameter: page.preview_parameter } : {}) })),
    localized: Object.keys(catalogs).length ? { format_version: 1, languages: Object.fromEntries(Object.entries(catalogs)
      .map(([language, catalog]) => [language, Object.fromEntries(registry.pages.map(page => [page.id, title(page, catalog)]))])) } : undefined,
  };
}
