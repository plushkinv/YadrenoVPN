import { ApiError } from '../api/client';
import type { ClientView } from '../model';

export type ConnectionState = 'unavailable' | 'disconnected' | 'permission' | 'denied' | 'connecting' | 'connected' | 'error';
export interface NativeBridge {
  version: 1; trustedOrigin: string; simulator?: boolean;
  state: () => ConnectionState; hasProfile: () => boolean;
  importSubscription: (url: string) => Promise<void>;
  connect: () => Promise<void>; disconnect: () => Promise<void>; cancel: () => Promise<void>;
  subscribe: (listener: () => void) => () => void;
}
interface TelegramApp {
  initData: string; colorScheme?: 'light' | 'dark'; viewportStableHeight?: number;
  safeAreaInset?: { top: number; right: number; bottom: number; left: number };
  contentSafeAreaInset?: { top: number; right: number; bottom: number; left: number };
  ready: () => void; expand: () => void;
  openLink: (url: string) => void; openTelegramLink: (url: string) => void;
  openInvoice?: (url: string, closed: (status: string) => void) => void;
  onEvent: (name: string, listener: () => void) => void; offEvent: (name: string, listener: () => void) => void;
  BackButton?: { show: () => void; hide: () => void; onClick: (listener: () => void) => void; offClick: (listener: () => void) => void };
}
declare global { interface Window { Telegram?: { WebApp?: TelegramApp }; YadrenoNativeBridge?: NativeBridge; } }
export interface Environment {
  kind: 'browser' | 'telegram' | 'native'; initData?: string; native?: NativeBridge; initialTheme?: 'light' | 'dark';
  copy: (value: string) => Promise<boolean>; share: (url: string) => Promise<boolean>;
  openLink: (url: string) => void; importSubscription: (client: string, url: string, importUrl?: string) => Promise<boolean>;
  payment: (url: string, presentation?: string) => Promise<void>;
  back: (visible: boolean, listener: () => void) => () => void;
  observe?: (listener: (theme?: 'light' | 'dark') => void) => () => void;
}
export const clients: (ClientView & { install: string; scheme?: (url: string) => string })[] = [
  { id: 'happ', name: 'Happ', platforms: 'Android, iOS, Windows, macOS, Linux, Android TV, Apple TV', description: 'Для телефонов, компьютеров и телевизоров. На сайте Happ выберите версию для своего устройства.', install: 'https://www.happ.su/main/ru', scheme: url => 'happ://add/' + url },
  { id: 'incy', name: 'INCY', platforms: 'Android, iOS, Windows, macOS, Linux, Android TV, Apple TV', description: 'Для телефонов, компьютеров и телевизоров. Сборки для Windows и Linux, а также DMG для macOS пока предварительные; версия macOS доступна и в App Store.', install: 'https://incy.cc/', scheme: url => 'incy://import/' + url },
  { id: 'v2raytun', name: 'v2RayTun', platforms: 'Android, iOS, Windows, macOS', description: 'Для телефона и компьютера. Ссылки на магазины и установочные файлы собраны на сайте v2RayTun.', install: 'https://v2raytun.com/', scheme: url => 'v2raytun://import/' + url },
  { id: 'hiddify', name: 'Hiddify', platforms: 'Android, iOS, Windows, macOS, Linux', description: 'Клиент с открытым исходным кодом для телефона и компьютера. Выберите свою систему на странице загрузки.', install: 'https://hiddify.com/app/', scheme: url => 'hiddify://import/' + url },
  { id: 'v2rayng', name: 'v2rayNG', platforms: 'Android', description: 'Клиент для Android. APK для установки доступен на странице последнего релиза разработчика.', install: 'https://github.com/2dust/v2rayNG/releases/latest', scheme: url => 'v2rayng://install-config?url=' + encodeURIComponent(url) },
  { id: 'v2rayn', name: 'v2rayN', platforms: 'Windows, macOS, Linux', description: 'Клиент для компьютера. После установки подписку нужно добавить вручную по скопированной ссылке.', install: 'https://github.com/2dust/v2rayN/releases/latest' },
  { id: 'streisand', name: 'Streisand', platforms: 'iOS, macOS', description: 'Клиент для устройств Apple. Устанавливается из App Store.', install: 'https://apps.apple.com/app/streisand/id6450534064', scheme: url => 'streisand://import/' + url },
  { id: 'shadowrocket', name: 'Shadowrocket', platforms: 'iOS, macOS, Apple TV', description: 'Платный клиент для устройств Apple. Покупка и установка — через App Store.', install: 'https://apps.apple.com/app/shadowrocket/id932747118', scheme: url => 'sub://' + btoa(new URL(url).href) },
  { id: 'exclave', name: 'Exclave', platforms: 'Android', description: 'Клиент для Android. Установочный APK доступен в релизах разработчика на GitHub.', install: 'https://github.com/ExclaveNetwork/Exclave/releases', scheme: url => 'exclave://subscription?url=' + encodeURIComponent(url) },
  { id: 'v2box', name: 'V2Box', platforms: 'Android, iOS, macOS', description: 'Для Android и устройств Apple. Выберите магазин своего устройства.', install: 'https://apps.apple.com/app/v2box-v2ray-client/id6446814690', installLinks: [
    { label: 'Google Play · Android', url: 'https://play.google.com/store/apps/details?id=dev.hexasoftware.v2box' },
    { label: 'App Store · iOS, macOS', url: 'https://apps.apple.com/app/v2box-v2ray-client/id6446814690' },
  ], scheme: url => 'v2box://install-sub?url=' + encodeURIComponent(url) },
];

function externalUrl(value: string, schemes = ['https:']): URL {
  const url = new URL(value);
  if (!schemes.includes(url.protocol) || !url.hostname || url.username || url.password) throw new ApiError('invalid_request');
  return url;
}
export async function loadEnvironment(): Promise<Environment> {
  // The regular browser/offline shell does not depend on the Telegram SDK.
  const launch = new URLSearchParams(location.hash.slice(1));
  if (!window.Telegram && launch.has('tgWebAppData')) {
    await new Promise<void>(resolve => {
      const script = document.createElement('script');
      const timer = window.setTimeout(resolve, 8000);
      script.src = 'https://telegram.org/js/telegram-web-app.js';
      script.onload = script.onerror = () => { clearTimeout(timer); resolve(); };
      document.head.append(script);
    });
  }
  return createEnvironment();
}
export function createEnvironment(hostIntegration = true): Environment {
  const tg = hostIntegration && window.Telegram?.WebApp?.initData ? window.Telegram.WebApp : undefined;
  const candidate = hostIntegration && window.top === window ? window.YadrenoNativeBridge : undefined;
  const native = candidate?.version === 1 && candidate.trustedOrigin === location.origin
    && (['state', 'hasProfile', 'importSubscription', 'connect', 'disconnect', 'cancel', 'subscribe'] as const).every(name => typeof candidate[name] === 'function') ? candidate : undefined;
  const copy = async (value: string) => { try { await navigator.clipboard.writeText(value); return true; } catch { return false; } };
  if (tg) { tg.ready(); tg.expand(); }
  return {
    kind: native ? 'native' : tg ? 'telegram' : 'browser', initData: tg?.initData, native, initialTheme: tg?.colorScheme,
    copy,
    share: async url => {
      externalUrl(url, ['http:', 'https:']);
      if (navigator.share) { try { await navigator.share({ url }); return true; } catch { return false; } }
      return copy(url);
    },
    openLink: value => {
      const url = externalUrl(value);
      if (tg && ['t.me', 'telegram.me'].includes(url.hostname)) tg.openTelegramLink(url.href);
      else if (tg) tg.openLink(url.href);
      else window.open(url.href, '_blank', 'noopener,noreferrer');
    },
    importSubscription: async (client, url, importUrl) => {
      // Existing panels can expose HTTP subscription links. The interface must
      // preserve that core result when importing into an external/native client.
      externalUrl(url, ['http:', 'https:']);
      if (native) { await native.importSubscription(url); return true; }
      const scheme = clients.find(item => item.id === client)?.scheme;
      if (!scheme) return false;
      if (importUrl) {
        const target = externalUrl(importUrl);
        if (target.origin !== location.origin || target.pathname !== '/open-client') throw new ApiError('invalid_request');
        const parameters = new URLSearchParams(target.hash.slice(1));
        parameters.set('client', client);
        target.hash = parameters.toString();
        if (tg) tg.openLink(target.href);
        else window.open(target.href, '_blank', 'noopener,noreferrer');
        return false;
      }
      // Keep the released two-argument contract for saved source customizations
      // and older cores. Current stock UI supplies the signed HTTPS handoff.
      window.open(scheme(url), '_blank', 'noopener,noreferrer');
      // Dispatch only: the web platform cannot confirm an installed app or tunnel.
      return false;
    },
    payment: async (value, presentation) => {
      // Preserve the released payment-provider URL contract, including tg://.
      const url = externalUrl(value, ['http:', 'https:', 'tg:']);
      if (tg?.openInvoice && (presentation === 'telegram_invoice' || url.hostname === 't.me' && url.pathname.startsWith('/$'))) {
        await new Promise<void>(resolve => tg.openInvoice!(url.href, () => resolve()));
      } else {
        // Same-tab navigation retains the order in the application's route on return.
        location.assign(url.href);
      }
    },
    back: (visible, listener) => {
      if (!tg?.BackButton) return () => {};
      if (visible) tg.BackButton.show(); else tg.BackButton.hide();
      tg.BackButton.onClick(listener);
      return () => tg.BackButton?.offClick(listener);
    },
    observe: listener => {
      if (!tg) return () => {};
      const update = () => {
        const style = document.documentElement.style;
        if (Number.isFinite(tg.viewportStableHeight)) style.setProperty('--tg-viewport-stable-height', tg.viewportStableHeight + 'px');
        for (const [name, values] of [['safe-area-inset', tg.safeAreaInset], ['content-safe-area-inset', tg.contentSafeAreaInset]] as const) {
          for (const side of ['top', 'right', 'bottom', 'left'] as const) {
            const value = values?.[side];
            if (value != null && Number.isFinite(value) && value >= 0) style.setProperty(`--tg-${name}-${side}`, value + 'px');
          }
        }
        listener(tg.colorScheme);
      };
      const events = ['themeChanged', 'viewportChanged', 'safeAreaChanged', 'contentSafeAreaChanged'];
      events.forEach(name => tg.onEvent(name, update)); update();
      return () => events.forEach(name => tg.offEvent(name, update));
    },
  };
}
