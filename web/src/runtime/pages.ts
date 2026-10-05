import type { ComponentType } from 'react';
import { appText as t } from '../i18n/app';
import declarations from './view-registry.json';

export interface PageDefinition { id: string; title: string; component: ComponentType; public?: boolean; feature?: string; module_id?: string; }
const modules = import.meta.glob<Record<string, ComponentType>>('../pages/**/*.tsx', { eager: true });
export const basePages: PageDefinition[] = declarations.pages.map(page => ({
  id: page.id, title: page.title ?? t[page.title_key as keyof typeof t], component: modules[page.file.replace('src/', '../')][page.export],
  ...(page.public ? { public: true } : {}), ...(page.feature ? { feature: page.feature } : {}),
}));
