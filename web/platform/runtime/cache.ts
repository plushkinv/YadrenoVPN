import { ApiError, type Api } from '../api/client';
import type { Page, Subscription } from '../api/contracts';
import { snapshot } from './storage';

export class AccountApi implements Api {
  private accountId: number | null = null;
  private generation = 0;
  constructor(private transport: Api) {}
  setAccount(id: number | null) { this.generation++; this.accountId = id; }
  async request<T>(path: string, method: 'GET' | 'POST' = 'GET', body?: unknown, key?: string): Promise<T> {
    const owner = this.accountId, generation = this.generation;
    const firstPage = path === '/subscriptions' || path === '/subscriptions?limit=50&offset=0';
    try {
      const result = await this.transport.request<T>(path, method, body, key);
      if (generation !== this.generation) throw new ApiError('authentication_required');
      if (owner != null && generation === this.generation && method === 'GET' && firstPage) {
        snapshot(owner, 'subscriptions', result);
        window.dispatchEvent(new Event('subscriptions-synchronized'));
      }
      return result;
    } catch (error) {
      if (owner != null && generation === this.generation && method === 'GET' && error instanceof ApiError && (error.code === 'network_unavailable' || error.status >= 500)) {
        const cached = snapshot<Page<Subscription>>(owner, 'subscriptions');
        if (cached) {
          let result: unknown;
          if (firstPage) result = cached.data;
          else if (/^\/subscriptions\/\d+$/.test(path)) result = cached.data.items.find(item => String(item.id) === path.split('/').pop());
          if (result) {
            window.dispatchEvent(new CustomEvent('account-cache-used', { detail: cached.updated }));
            return result as T;
          }
        }
      }
      throw error;
    }
  }
}
