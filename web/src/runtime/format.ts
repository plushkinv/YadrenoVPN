import type { Subscription } from '../api/contracts';
import type { SubscriptionView } from '../model';
import { appText as t } from '../i18n/app';

export function money(minor: number | null | undefined, currency: string): string {
  if (minor == null) return t.unknown;
  if (currency === 'XTR') return `${minor} ⭐`;
  try {
    const digits = new Intl.NumberFormat('ru', { style: 'currency', currency }).resolvedOptions().maximumFractionDigits ?? 2;
    return new Intl.NumberFormat('ru', { style: 'currency', currency, minimumFractionDigits: 0 }).format(minor / 10 ** digits);
  } catch { return `${minor} ${currency}`; }
}
export function date(value: string | number | null | undefined): string {
  if (value == null) return t.unknown;
  const instant = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(instant.getTime()) ? t.unknown : instant.toLocaleDateString('ru', { day: 'numeric', month: 'short', year: 'numeric' });
}
export function bytes(value: number | null): string {
  return value == null ? t.unknown : new Intl.NumberFormat('ru', { maximumFractionDigits: 1 }).format(value / 1024 ** 3) + ' ГБ';
}
export function subscriptionView(item: Subscription): SubscriptionView {
  const days = item.expires_at ? Math.ceil((new Date(item.expires_at).getTime() - Date.now()) / 86400000) : null;
  const state = item.access_status === 'pending' ? 'pending' : item.state === 'unconfigured' ? 'unconfigured' : item.state === 'disabled' ? 'disabled' : item.state === 'exhausted' ? 'traffic' : item.state === 'expired' ? 'expired' : item.state !== 'active' ? 'unknown' : days != null && days > 0 && days <= 3 ? 'expiring' : 'active';
  const used = item.traffic.known ? item.traffic.used_bytes : null;
  const limit = item.traffic.limit_bytes;
  return { id: String(item.id), name: item.name || `${t.subscription} ${item.id}`, plan: item.tariff_name ?? t.unknown,
    state, expires: date(item.expires_at), remaining: days == null ? t.unknown : `${Math.max(0, days)} дн.`,
    traffic: used == null && limit == null ? null : `${bytes(used)} / ${limit ? bytes(limit) : t.unlimited}`,
    trafficPercent: used != null && limit ? Math.min(100, used / limit * 100) : null, devices: null };
}
