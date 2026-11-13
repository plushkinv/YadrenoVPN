/** Pure translator shared by the SDK; application and system own separate catalogs. */
import languages from '../../../locales/languages.json';

export type Message = string | Partial<Record<Intl.LDMLPluralRule, string>> & { other: string };
export type Catalog = Record<string, Message>;
export type Parameters = Record<string, string | number>;
export const availableLanguages = languages;
export function direction(language: string): 'ltr' | 'rtl' { return language === 'ar' || language === 'fa' ? 'rtl' : 'ltr'; }

export function createTranslator(modules: Record<string, Catalog>, initial = 'ru') {
  const catalogs = Object.fromEntries(Object.entries(modules).map(([path, value]) => [path.split('/').pop()!.replace(/\.json$/, ''), value]));
  let language = Object.hasOwn(catalogs, initial) ? initial : 'ru';
  const listeners = new Set<() => void>();
  const rules = new Map<string, Intl.PluralRules>();
  return {
    getLanguage: () => language,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    setLanguage(value: string) {
      if (!Object.hasOwn(catalogs, value)) throw new Error(`Unsupported language: ${value}`);
      if (language === value) return;
      language = value; listeners.forEach(listener => listener());
    },
    text(key: string, parameters: Parameters = {}): string {
      const message = catalogs[language]?.[key];
      if (message === undefined) throw new Error(`Missing translation: ${language}/${key}`);
      let template: string;
      if (typeof message === 'string') template = message;
      else {
        if (typeof parameters.count !== 'number') throw new Error(`Plural count required: ${key}`);
        if (!rules.has(language)) rules.set(language, new Intl.PluralRules(language));
        template = message[rules.get(language)!.select(parameters.count)] ?? message.other;
      }
      return template.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, (marker, name: string) => {
        if (!Object.hasOwn(parameters, name)) return marker;
        const value = String(parameters[name]);
        // Isolate inserted names/numbers from surrounding RTL prose without HTML injection.
        return direction(language) === 'rtl' ? `\u2068${value}\u2069` : value;
      });
    },
  };
}
