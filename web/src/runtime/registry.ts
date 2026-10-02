import customization from 'virtual:yadreno-customization';
import { basePages, type PageDefinition } from './pages';

const pages = new Map(basePages.map(page => [page.id, page]));
for (const page of customization.pages) {
  const base = pages.get(page.id);
  pages.set(page.id, { ...base, ...page, title: page.title ?? base?.title ?? page.id });
}
export const registeredPages: PageDefinition[] = [...pages.values()];
export { customization };
