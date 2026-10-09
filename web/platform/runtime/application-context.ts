import { ApiError } from '../api/client';
import type { ApplicationRuntime, RuntimeState } from './contract';
import type { AppContextValue } from './context';

/** The SDK exposes the same operations regardless of the rendering surface. */
export function applicationContext(runtime: ApplicationRuntime, state: RuntimeState): AppContextValue {
  const call: ApplicationRuntime['call'] = async (method, args) => {
    try { return await runtime.call(method, args); }
    catch (error) {
      if (error && typeof error === 'object' && 'code' in error) {
        const value = error as ApiError;
        throw new ApiError(value.code, value.retryable, value.status, value.details, value.operationId);
      }
      throw error;
    }
  };
  const fire = (method: Parameters<typeof call>[0], args: unknown[] = []) => { void call(method, args).catch(() => {}); };
  return {
    ...state,
    api: { request: <T>(path: string, method = 'GET', body?: unknown, key?: string) => call('api', [path, method, body, key]) as Promise<T> },
    environment: { ...state.environment, native: runtime.native,
      copy: value => call('copy', [value]) as Promise<boolean>, share: url => call('share', [url]) as Promise<boolean>,
      openLink: url => fire('openLink', [url]), importSubscription: (client, url, importUrl) => call('importSubscription', [client, url, importUrl]) as Promise<boolean>,
      payment: (url, presentation) => call('payment', [url, presentation]) as Promise<void>,
      back: () => () => {},
    },
    storage: {
      get: <T>(key: string, fallback: T): T => Object.hasOwn(state.storage, key) ? state.storage[key] as T : fallback,
      set: (key, value) => { state.storage[key] = value; fire('storage.set', [key, value]); },
    },
    navigate: route => fire('navigate', [route]), back: () => fire('back'), refresh: () => fire('refresh'),
    authenticated: session => fire('authenticated', [session]), logout: () => call('logout', []) as Promise<void>,
    loginWithTelegram: () => call('loginWithTelegram', []) as Promise<void>,
    setTheme: theme => fire('setTheme', [theme]), update: () => call('update', []) as Promise<void>, reload: () => fire('reload'),
  };
}

export function transportError(error: unknown) {
  return error instanceof ApiError ? { code: error.code, retryable: error.retryable, status: error.status,
    details: error.details, operationId: error.operationId } : { code: 'request_failed', retryable: false, status: 0 };
}
