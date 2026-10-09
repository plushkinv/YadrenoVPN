import { ApiError } from '../api/client';
import type { AppContextValue } from './context';
import type { RuntimeMethod } from './contract';
import type { Session } from '../api/contracts';

const route = /^[a-z][a-z0-9_.-]*(?:\/[a-zA-Z0-9_.:-]+)?$/;
/** Only ordinary account operations cross this boundary. Authority stays HTTP-owned. */
function userPath(value: unknown): string {
  if (typeof value !== 'string' || value.length > 4096) throw new ApiError('invalid_request');
  const [encoded] = value.split('?');
  let segments: string[];
  try { segments = encoded.split('/').map(decodeURIComponent); } catch { throw new ApiError('invalid_request'); }
  const path = segments.join('/');
  if (!encoded.startsWith('/') || segments.slice(1).some(part => !part || ['.', '..'].includes(part) || /[/\\%\x00-\x20]/.test(part)) || value.includes('#') || value.includes('\\'))
    throw new ApiError('invalid_request');
  const prefix = path.split('/')[1];
  if (!['auth', 'account', 'bootstrap', 'me', 'ui', 'catalog', 'trial-offers', 'trials', 'subscriptions', 'subscription-imports',
    'key-operations', 'balance', 'payments', 'referrals', 'help', 'quotes', 'orders', 'promotions', 'promo', 'modules'].includes(prefix)
    || prefix === 'auth' && !['/auth/settings', '/auth/session', '/auth/login', '/auth/register', '/auth/logout',
      '/auth/verification/request', '/auth/verification/status', '/auth/verification/verify', '/auth/password/reset'].includes(path)
    || prefix === 'ui' && path !== '/ui/settings') throw new ApiError('access_denied');
  return value;
}
function text(value: unknown): string { if (typeof value !== 'string') throw new ApiError('invalid_request'); return value; }
function publicResult(value: unknown): unknown {
  // The server's ordinary session DTO may include CSRF after authentication.
  // SDK callers need only its public identity fields.
  if (value && typeof value === 'object' && !Array.isArray(value) && 'account_id' in value && 'source' in value) {
    const { csrf: _csrf, token: _token, ...identity } = value as Record<string, unknown>;
    return identity;
  }
  return value;
}
export async function dispatch(value: AppContextValue, method: RuntimeMethod, args: unknown[]): Promise<unknown> {
  if (!Array.isArray(args) || args.length > 4) throw new ApiError('invalid_request');
  const [first, second, third, fourth] = args;
  switch (method) {
    case 'api': {
      if (!['GET', 'POST'].includes(String(second))) throw new ApiError('invalid_request');
      const result = await value.api.request(userPath(first), second as 'GET' | 'POST', third, fourth === undefined ? undefined : text(fourth));
      if (first === '/account/telegram/link/finish' && result && typeof result === 'object' && 'session' in result) {
        const session = result.session as Session;
        value.authenticated(session);
        return { ...result, session: publicResult(session) };
      }
      return publicResult(result);
    }
    case 'navigate': if (!route.test(text(first))) throw new ApiError('invalid_request'); return value.navigate(first as string);
    case 'back': return value.back();
    case 'refresh': return value.refresh();
    case 'authenticated': return value.authenticated(await value.api.request('/auth/session'));
    case 'logout': return value.logout();
    case 'loginWithTelegram': return value.loginWithTelegram();
    case 'setTheme': if (first !== 'light' && first !== 'dark') throw new ApiError('invalid_request'); return value.setTheme(first);
    case 'storage.set': return value.storage.set(text(first), second);
    case 'copy': return value.environment.copy(text(first));
    case 'share': return value.environment.share(text(first));
    case 'openLink': return value.environment.openLink(text(first));
    case 'importSubscription': return value.environment.importSubscription(text(first), text(second), third === undefined ? undefined : text(third));
    case 'payment': return value.environment.payment(text(first), second === undefined ? undefined : text(second));
    case 'update': return value.update();
    case 'reload': return value.reload();
    default: throw new ApiError('invalid_request');
  }
}
