import { createTranslator, direction, type Catalog } from './catalog';

const translator = createTranslator(import.meta.glob<Catalog>('./locales/*.json', { eager: true, import: 'default' }), document.documentElement.lang || 'ru');
export const text = translator.text;
export const getLanguage = translator.getLanguage;
export function bindInstallationLanguage(language = 'ru') {
  translator.setLanguage(language);
  document.documentElement.lang = language;
  document.documentElement.dir = direction(language);
}
