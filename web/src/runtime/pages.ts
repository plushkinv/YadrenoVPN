import type { ComponentType } from 'react';
import { text } from '../i18n/runtime';
import declarations from './view-registry.json';

export interface PageDefinition { id: string; title: string; component: ComponentType; public?: boolean; feature?: string; module_id?: string; }
const modules = import.meta.glob<Record<string, ComponentType>>('../pages/**/*.tsx', { eager: true });
export const basePages: PageDefinition[] = declarations.pages.map(page => ({
  id: page.id, get title() { return 'title' in page && typeof page.title === 'string' ? page.title : text('app.' + page.title_key); },
  component: modules[page.file.replace('src/', '../')][page.export],
  ...(page.public ? { public: true } : {}), ...(page.feature ? { feature: page.feature } : {}),
  ...('module_id' in page ? { module_id: String(page.module_id) } : {}),
}));
