import { useSyncExternalStore } from 'react';
import { createTranslator, type Catalog } from '@ui/i18n';
export { availableLanguages, direction } from '@ui/i18n';

// All dictionaries are bundled with this editable application; translation does not fetch data.
const translator = createTranslator(import.meta.glob<Catalog>('./locales/*.json', { eager: true, import: 'default' }), document.documentElement.lang || 'ru');
let customized = false;
export const text = translator.text;
export const getLanguage = translator.getLanguage;
export function bindInstallationLanguage(language = 'ru') {
  if (!customized) translator.setLanguage(language);
}
/** Optional application-only customization. The stock UI has no language switch. */
export function setApplicationLanguage(language: string) {
  translator.setLanguage(language);
  customized = true;
}
export function useLanguage() { return useSyncExternalStore(translator.subscribe, translator.getLanguage); }
