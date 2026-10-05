import { useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import type { Page, Subscription } from '../api/contracts';
import { useApp, useResource } from './context';
import { remember, stored } from './storage';

// Opaque demos have their own module instance and never persist account choices.
let previewSelection: number | null = null;
export function selectedSubscriptionId(accountId: number | undefined, preview: boolean): number | null {
  const value = preview ? previewSelection : stored<unknown>(`${accountId}.selected`, null);
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? value : null;
}

export function useSelectedSubscription() {
  const { api, revision, session, preview } = useApp();
  const [id, setId] = useState(() => selectedSubscriptionId(session?.account_id, preview));
  const first = useResource<Page<Subscription>>('/subscriptions?limit=50&offset=0');
  const listed = first.data?.items.find(item => item.id === id);
  const [detail, setDetail] = useState<{ id: number | null; data?: Subscription; error?: unknown }>({ id: null });
  useEffect(() => {
    let active = true;
    setDetail({ id });
    if (id && first.data && !listed) {
      api.request<Subscription>('/subscriptions/' + id).then(
        data => { if (active) setDetail({ id, data }); },
        error => { if (active) setDetail({ id, error }); },
      );
    }
    return () => { active = false; };
  }, [api, revision, id, listed, first.data, session?.account_id]);
  const current = detail.id === id ? detail : undefined;
  const missing = current?.error instanceof ApiError && current.error.code === 'subscription_not_found';
  const selected = id == null || missing ? first.data?.items[0] : listed ?? (current?.data?.id === id ? current.data : undefined);
  function select(value: number | null) {
    setId(value);
    if (preview) previewSelection = value; else remember(`${session?.account_id}.selected`, value);
  }
  useEffect(() => {
    if (first.data && (id == null || missing)) {
      const next = first.data.items[0]?.id ?? null;
      if (next !== id) select(next);
    }
  }, [first.data, id, missing]);
  return { selected, select, error: first.error ?? (!missing ? current?.error : undefined),
    loading: first.loading || Boolean(id && !selected && !missing && !current?.error),
    hasMultiple: (first.data?.items.length ?? 0) > 1 || Boolean(selected && first.data?.items.length && !listed),
    retry: () => { first.retry(); } };
}
