import { useRef, useState } from 'react';
import { mutationKey } from '../api/client';
import { appText as t } from '../i18n/app';
import { useAction, useApp, useResource } from '../runtime/context';
import { Empty, Failure, Resource } from './Forms';
import { Button } from './Ui';

interface Host { id: number; custom_name: string | null; tariff_name: string | null; }

/** Optional continuation of a configured key; eligibility belongs to core. */
export function SubscriptionHosts({ keyId, onClose, showEmpty = false }: {
  keyId: number | string; onClose?: () => void; showEmpty?: boolean;
}) {
  const { api } = useApp();
  const state = useResource<{ hosts: Host[] }>(`/subscriptions/${encodeURIComponent(keyId)}/host-candidates`);
  const action = useAction();
  const keys = useRef<Record<number, string>>({});
  const [dismissed, setDismissed] = useState(false);
  function close() { setDismissed(true); onClose?.(); }
  if (dismissed || !showEmpty && state.data?.hosts.length === 0) return null;
  return <Resource state={state}>{data => <section className="panel form-panel" data-ui="subscription.hosts">
    {data.hosts.length ? <><h2>{t.hosts}</h2><p>{t.hostsCaption}</p></> : <Empty />}
    {data.hosts.map(host => <div className="list-row" key={host.id}>
      <span>{host.custom_name || host.tariff_name || `${t.subscription} ${host.id}`}</span>
      <Button disabled={action.busy} onClick={() => action.run(async () => {
        await api.request(`/subscriptions/${encodeURIComponent(keyId)}/host`, 'POST', { host_id: host.id }, keys.current[host.id] ??= mutationKey());
        close();
      })}>{t.bind}</Button>
    </div>)}
    <Button tone="quiet" disabled={action.busy} onClick={close}>{t.skip}</Button>
    <Failure error={action.error} />
  </section>}</Resource>;
}
