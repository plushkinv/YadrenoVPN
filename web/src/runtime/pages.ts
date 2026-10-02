import type { ComponentType } from 'react';
import * as account from '../pages/Account';
import * as checkout from '../pages/Checkout';
import * as information from '../pages/Information';
import * as subscriptions from '../pages/SubscriptionAccount';
import { appText as t } from '../i18n/app';
import declarations from './view-registry.json';

export interface PageDefinition { id: string; title: string; component: ComponentType; public?: boolean; feature?: string; module_id?: string; }
const modules: Record<string, Record<string, ComponentType>> = {
  'src/pages/Account.tsx': account, 'src/pages/Checkout.tsx': checkout,
  'src/pages/Information.tsx': information, 'src/pages/SubscriptionAccount.tsx': subscriptions,
};
export const basePages: PageDefinition[] = declarations.pages.map(page => ({
  id: page.id, title: page.title ?? t[page.title_key as keyof typeof t], component: modules[page.file][page.export],
  ...(page.public ? { public: true } : {}), ...(page.feature ? { feature: page.feature } : {}),
}));
