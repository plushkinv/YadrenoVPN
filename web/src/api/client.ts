export class ApiError extends Error {
  constructor(public code: string, public retryable = false, public status = 0,
    public details: Record<string, unknown> = {}, public operationId?: string) { super(code); }
}
export interface Api {
  request<T>(path: string, method?: 'GET' | 'POST', body?: unknown, idempotencyKey?: string): Promise<T>;
}

/** No mutation retry, speculative completion, or offline mutation queue. */
export class HttpApi implements Api {
  private csrf = document.cookie.split('; ').find(value => value.startsWith('__Host-yadreno_csrf=')) ?? '';
  async request<T>(path: string, method: 'GET' | 'POST' = 'GET', body?: unknown, idempotencyKey?: string): Promise<T> {
    if (!path.startsWith('/') || path.startsWith('//') || path.includes('\\') || path.includes('#')) throw new ApiError('invalid_request');
    const headers: Record<string, string> = { Accept: 'application/json' };
    const currentCookie = () => document.cookie.split('; ').find(value => value.startsWith('__Host-yadreno_csrf=')) ?? '';
    const checkIdentity = () => {
      const cookie = currentCookie();
      if (cookie !== this.csrf) { this.csrf = cookie; window.dispatchEvent(new Event('account-changed')); throw new ApiError('authentication_required'); }
    };
    checkIdentity();
    if (method === 'GET' && !navigator.onLine) throw new ApiError('network_unavailable', true);
    if (method === 'POST') {
      if (!navigator.onLine) throw new ApiError('offline');
      if (!(body instanceof FormData)) headers['Content-Type'] = 'application/json';
      const csrf = document.cookie.split('; ').find(value => value.startsWith('__Host-yadreno_csrf='));
      if (csrf) headers['X-CSRF-Token'] = decodeURIComponent(csrf.slice(csrf.indexOf('=') + 1));
      if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;
    }
    let response: Response;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      response = await fetch('/api/v1' + path, { method, headers, credentials: 'same-origin', cache: 'no-store',
        signal: controller.signal,
        body: method === 'POST' ? body instanceof FormData ? body : JSON.stringify(body ?? {}) : undefined });
    } catch { throw new ApiError('network_unavailable', true); }
    finally { clearTimeout(timeout); }
    if (method === 'POST' && ['/auth/login', '/auth/register', '/auth/telegram', '/account/credentials', '/auth/logout', '/auth/password/reset'].includes(path)) this.csrf = currentCookie();
    else checkIdentity();
    let value: unknown;
    try { value = await response.json(); } catch { throw new ApiError('invalid_response', true, response.status); }
    if (!response.ok) {
      const error = value as { code?: string; retryable?: boolean; details?: Record<string, unknown>; operation_id?: string };
      if (response.status === 401 && ['authentication_required', 'reauthentication_required'].includes(error.code ?? '')) window.dispatchEvent(new Event('account-expired'));
      throw new ApiError(error.code ?? 'request_failed', Boolean(error.retryable), response.status, error.details, error.operation_id);
    }
    if (method === 'POST' && value && typeof value === 'object' && 'ok' in value && value.ok === false) {
      const rejected = value as { reason?: string; error?: string; retryable?: boolean };
      throw new ApiError(rejected.reason ?? rejected.error ?? 'action_unavailable', Boolean(rejected.retryable), response.status);
    }
    return value as T;
  }
}

export function mutationKey(): string { return crypto.randomUUID(); }
