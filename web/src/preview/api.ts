/** Isolated read-model fixtures. No business execution or network transport exists here. */
import { ApiError, type Api } from '../api/client';
import type { Subscription } from '../api/contracts';
import type { InstallationPreview, PreviewContext } from './contracts';

export class PreviewApi implements Api {
  constructor(readonly installation: InstallationPreview, readonly context: PreviewContext) {}
  async request<T>(path: string, method = 'GET'): Promise<T> {
    if (method !== 'GET') throw new ApiError('preview_only');
    const { installation: config, context } = this;
    const auth = { phone_format: 'E.164', verification_available: false, verification_required: false, verification_method: 'ucaller' as const,
      password_recovery_available: false, unverified_phone_warning_required: true };
    if (path === '/bootstrap') return { api_version: 1, auth, features: config.features, modules: config.modules ?? [] } as T;
    if (path === '/ui/settings') return { ...config.settings, preset: context.preset, theme: context.theme } as T;
    if (path === '/auth/session') return { account_id: -1, telegram_id: null, source: 'preview', expires_at: 0 } as T;
    if (context.scenario === 'loading') return new Promise<T>(() => {});
    if (context.scenario === 'error') throw new ApiError('temporarily_unavailable', true);
    const date = new Date((config.captured_at + 30 * 86400) * 1000).toISOString();
    const tariff = config.tariffs[0];
    const state = context.scenario === 'expired' ? 'expired' : context.scenario === 'traffic' ? 'exhausted' : context.scenario === 'disabled' ? 'disabled' : context.scenario === 'unconfigured' ? 'unconfigured' : 'active';
    const subscription: Subscription = { id: 1, name: 'Подписка для просмотра', tariff_id: tariff?.id ?? 1,
      tariff_name: tariff?.name ?? null, server_id: 1, server_name: 'Сервер для просмотра',
      expires_at: date, created_at: date, state, access_status: context.scenario === 'issuing' ? 'pending' : state === 'unconfigured' ? 'unconfigured' : 'ready',
      traffic: { known: context.scenario !== 'unknown', used_bytes: context.scenario === 'unknown' ? null : 0,
        limit_bytes: null, updated_at: null, source: 'preview' }, devices_available: true,
      actions: Object.fromEntries(['key.rename.start', 'key.renew.start', 'key.delete', 'key.configure.start', 'key.replace.start'].map(id => [id, { allowed: true, reason: null }])),
      pending_operations: [], servers: [{ id: 1, name: 'Сервер для просмотра' }] };
    const empty = context.scenario === 'empty';
    const route = path.split('?')[0];
    const page = (items: unknown[]) => ({ items: empty ? [] : items, limit: 50, offset: 0 });
    let result: unknown;
    if (route === '/catalog') result = { purpose: 'key_purchase', subscription_id: null, groups: [], tariffs: config.tariffs.map(item => ({ ...item,
      base_currency: config.currency, nominal_amount_minor: item.price_minor, payable_amount_minor: item.price_minor,
      payment_methods: [{ id: 'preview', presentation: 'placeholder' }], available: true, reason: null })) };
    else if (route === '/subscriptions') result = page([subscription]);
    else if (/^\/subscriptions\/[^/]+$/.test(route)) result = subscription;
    else if (route.endsWith('/access')) result = { subscription_id: 1, url: 'https://example.invalid/sub/preview-only' };
    else if (route === '/trial-offers') result = { offers: config.trial_offers.map(offer => ({ eligible: true, reason: null, scope: 'preview', offer })) };
    else if (route.startsWith('/orders/')) result = { order_id: 'preview-order', purpose: 'key_purchase',
      status: ['issuing', 'ready'].includes(context.scenario) ? 'paid' : 'pending',
      provider_confirmed: ['confirmed', 'issuing', 'ready'].includes(context.scenario),
      fulfillment_status: context.scenario === 'ready' ? 'completed' : 'pending',
      access_status: context.scenario === 'ready' ? 'ready' : 'pending', subscription_id: 1,
      base_currency: config.currency, nominal_amount_minor: tariff?.price_minor ?? 0,
      payable_amount_minor: tariff?.price_minor ?? 0, balance_deduct_minor: 0, payment_type: 'preview' };
    else if (route === '/me') result = { account_id: -1, telegram_id: null, first_name: 'Аккаунт для просмотра', username: null,
      last_name: null, created_at: null, credentials: { present: true, phone: '+12125550123', phone_verified: false } };
    else if (route.endsWith('/devices')) result = { devices: empty ? [] : [{ id: 'preview-device', device_model: 'Устройство для просмотра', device_os: 'Android', last_seen: null }] };
    else if (route.endsWith('/host-candidates')) result = { hosts: empty ? [] : [2, 3].map(id => ({ id, custom_name: `Основная подписка ${id - 1}`, tariff_name: tariff?.name ?? null })) };
    else if (route.startsWith('/key-operations/')) result = { operation_id: 'preview-operation', key_id: 1, state: context.scenario === 'ready' ? 'completed' : 'pending', result: context.scenario === 'ready' ? { composition: { ok: true, status: 'selection_required' } } : null };
    else if (route === '/balance') result = { amount_minor: 0, currency: config.currency, history: [], limit: 50, offset: 0 };
    else if (route === '/payments') result = page([{ order_id: 'preview-order', purpose: 'key_purchase', created_at: date, payable_amount_minor: tariff?.price_minor ?? 0, base_currency: config.currency }]);
    else if (route.endsWith('/history')) result = page([]);
    else if (route === '/referrals') result = { enabled: config.features.referrals, conditions_html: 'Сценарий просмотра. Вознаграждения не начисляются.', site_url: 'https://example.invalid/?ref=preview', statistics: [] };
    else if (route === '/help') result = { content_html: 'Сценарий просмотра страницы помощи.', links: [] };
    else throw new ApiError('preview_only');
    return result as T;
  }
}
