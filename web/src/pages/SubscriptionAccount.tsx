import { useRef, useState } from 'react';
import type { Operation, Page, Subscription, TrialOffer } from '../api/contracts';
import { mutationKey } from '../api/client';
import { Button, Dialog, PageHeading } from '../components/Ui';
import { Empty, Failure, Field, Form, Resource } from '../components/Forms';
import { Home } from './Home';
import { Subscriptions } from './Subscriptions';
import { Connect } from './Connect';
import { appText as t } from '../i18n/app';
import { ru } from '../i18n/ru';
import { useAction, useApp, useResource } from '../runtime/context';
import { clients } from '../runtime/environment';
import { date, subscriptionView } from '../runtime/format';
import { remember, stored } from '../runtime/storage';
import { NativeConnection } from '../components/NativeConnection';

function useClientChoice() {
  const { session } = useApp();
  const key = `${session?.account_id}.client`;
  const [id, setId] = useState(() => stored(key, 'hiddify'));
  return { client: clients.find(value => value.id === id) ?? clients[0], select: (value: string) => { setId(value); remember(key, value); } };
}

export function HomeAccount() {
  const { environment } = useApp();
  return <><NativeConnection bridge={environment.native} /><HomeAccountContent /></>;
}

function HomeAccountContent() {
  const { navigate, bootstrap, session, environment } = useApp();
  const state = useResource<Page<Subscription>>('/subscriptions');
  const { client, select } = useClientChoice();
  const [choose, setChoose] = useState(false);
  const selectedId = stored<number | null>(`${session?.account_id}.selected`, null);
  const selected = state.data?.items.find(item => item.id === selectedId) ?? state.data?.items[0];
  const empty = { id: '', name: '', plan: '', state: 'active' as const, expires: t.unknown, remaining: t.unknown, traffic: null, trafficPercent: null, devices: null };
  if (environment.native && selected) return <><PageHeading title={t.subscriptions} /><section className="panel form-panel"><h2>{selected.name || selected.tariff_name}</h2><p>{subscriptionView(selected).expires}</p><Button onClick={() => navigate('connect/' + selected.id)}>Добавить подписку</Button><Button tone="secondary" onClick={() => navigate('subscriptions')}>{t.subscriptions}</Button></section></>;
  if (state.error) return <Failure error={state.error} retry={state.retry} />;
  if (selected && (selected.access_status !== 'ready' || selected.state === 'disabled')) return <>
    <PageHeading title={t.subscription} /><section className="panel form-panel"><h2>{selected.access_status === 'pending' ? t.accessPending : selected.state === 'disabled' ? t.disabled : t.unconfigured}</h2>
      <p>{selected.name || selected.tariff_name}</p><Button onClick={() => navigate('subscription/' + selected.id)}>{t.details}</Button><Button tone="quiet" onClick={state.retry}>{t.refresh}</Button></section>
  </>;
  return <><Home state={state.loading ? 'loading' : selected ? subscriptionView(selected).state : 'new'} subscription={selected ? subscriptionView(selected) : empty}
    hasMultiple={(state.data?.items.length ?? 0) > 1} client={client} onSubscriptions={() => navigate('subscriptions')}
    onConnect={() => navigate('connect/' + selected?.id)} onGuide={() => navigate('connect/' + selected?.id)} onClient={() => setChoose(true)}
    onRenew={() => navigate('renewal/' + selected?.id)} onBuy={() => navigate('purchase')} onTrial={() => navigate('trials')} onRetry={state.retry}
    trialAvailable={bootstrap.features.trial} renewAvailable={selected?.actions['key.renew.start']?.allowed ?? false} />
    {selected?.state === 'first_use' && <p className="notice">{t.firstUse}</p>}
    {selected && <Button tone="quiet" onClick={() => navigate('subscription/' + selected.id)}>{t.details}</Button>}
    {choose && <Dialog title={t.chooseClient} onClose={() => setChoose(false)}>{clients.map(item => <Button key={item.id} tone="secondary" onClick={() => { select(item.id); setChoose(false); }}>{item.name} · {item.platforms}</Button>)}</Dialog>}
  </>;
}

export function SubscriptionList() {
  const { navigate, session } = useApp();
  const [offset, setOffset] = useState(0);
  const state = useResource<Page<Subscription>>('/subscriptions?limit=50&offset=' + offset);
  return <Resource state={state}>{data => <><Subscriptions items={data.items.map(subscriptionView)} selected={String(stored(`${session?.account_id}.selected`, ''))}
    onSelect={id => { remember(`${session?.account_id}.selected`, Number(id)); navigate('subscription/' + id); }} onBuy={() => navigate('purchase')} />
    <div className="button-row">{offset > 0 && <Button tone="quiet" onClick={() => setOffset(offset - 50)}>{t.back}</Button>}{data.items.length === 50 && <Button onClick={() => setOffset(offset + 50)}>{t.continue}</Button>}</div></>}</Resource>;
}

export function SubscriptionDetails() {
  const { api, param, navigate, refresh } = useApp();
  const state = useResource<Subscription>('/subscriptions/' + encodeURIComponent(param));
  const action = useAction();
  const [edit, setEdit] = useState<'rename' | 'configure' | 'replace' | 'delete' | null>(null);
  const [name, setName] = useState('');
  const [server, setServer] = useState<number>();
  const [key, setKey] = useState(mutationKey);
  function start(next: typeof edit) { setEdit(next); setKey(mutationKey()); setName(state.data?.name ?? ''); setServer(state.data?.servers[0]?.id); action.clear(); }
  async function mutate() {
    await action.run(async () => {
      const payload = edit === 'rename' ? { name } : edit === 'configure' || edit === 'replace' ? { server_id: server } : {};
      const result = await api.request<Operation>(`/subscriptions/${encodeURIComponent(param)}/${edit}`, 'POST', payload, key);
      setEdit(null); refresh();
      navigate(edit === 'delete' ? 'subscriptions' : 'operation/' + encodeURIComponent(result.operation_id));
    });
  }
  return <><PageHeading title={t.subscription} back={() => navigate('subscriptions')} /><Resource state={state}>{item => <section className="panel form-panel">
    <h2>{item.name || item.tariff_name || `${t.subscription} ${item.id}`}</h2><p>{item.tariff_name || t.unknown}</p>
    <dl className="detail-list"><div><dt>{t.server}</dt><dd>{item.server_name || t.unknown}</dd></div><div><dt>{t.subscription}</dt><dd>{ru.status[subscriptionView(item).state]}</dd></div><div><dt>Действует до</dt><dd>{item.state === 'first_use' ? t.firstUse : date(item.expires_at)}</dd></div></dl>
    <div className="action-list">
      {item.access_status === 'ready' && <Button onClick={() => navigate('connect/' + item.id)}>{t.connect}</Button>}
      {item.actions['key.renew.start']?.allowed && <Button tone="secondary" onClick={() => navigate('renewal/' + item.id)}>{t.renew}</Button>}
      {(['rename', 'configure', 'replace', 'delete'] as const).filter(verb => item.actions[verb === 'delete' ? 'key.delete' : `key.${verb}.start`]?.allowed).map(verb => <Button tone="secondary" key={verb} onClick={() => start(verb)}>{t[verb]}</Button>)}
      {item.devices_available && <Button tone="quiet" onClick={() => navigate('devices/' + item.id)}>{t.devices}</Button>}
      <Button tone="quiet" onClick={() => navigate('hosts/' + item.id)}>{t.hosts}</Button><Button tone="quiet" onClick={() => navigate('history/' + item.id)}>{t.history}</Button>
      {item.pending_operations.map(operation => <Button tone="quiet" key={operation.id} onClick={() => navigate('operation/' + encodeURIComponent(operation.id))}>{t.checkOperation}</Button>)}
    </div>
  </section>}</Resource>
    {edit && <Dialog title={edit === 'delete' ? t.confirmDelete : edit === 'replace' ? t.confirmReplace : t[edit]} onClose={() => setEdit(null)}>
      <Form onSubmit={mutate} busy={action.busy} submit={t[edit]}>
        {edit === 'rename' && <Field label={t.name}><input required maxLength={30} value={name} onChange={e => setName(e.target.value)} /></Field>}
        {(edit === 'configure' || edit === 'replace') && <Field label={t.server}><select required value={server ?? ''} onChange={e => setServer(Number(e.target.value))}>{state.data?.servers.map(value => <option key={value.id} value={value.id}>{value.name}</option>)}</select></Field>}
      </Form><Failure error={action.error} />
    </Dialog>}
  </>;
}

export function Connection() {
  const { api, param, environment, navigate } = useApp();
  const subscription = useResource<Subscription>('/subscriptions/' + encodeURIComponent(param));
  const access = useResource<{ url: string }>('/subscriptions/' + encodeURIComponent(param) + '/access');
  const { client, select } = useClientChoice();
  const [choose, setChoose] = useState(false);
  const [opened, setOpened] = useState(false);
  const action = useAction();
  if (environment.native) return <><PageHeading title={t.connect} /><NativeConnection bridge={environment.native} importProfile={async () => {
    const fresh = await api.request<{ url: string }>('/subscriptions/' + encodeURIComponent(param) + '/access');
    await environment.importSubscription('native', fresh.url);
  }} /></>;
  return <Resource state={subscription}>{item => <><Resource state={access}>{value => <Connect subscription={subscriptionView(item)} client={client} accessUrl={value.url}
    onBack={() => navigate('subscription/' + item.id)} onClient={() => setChoose(true)} onOpen={() => action.run(async () => {
      // Explicitly fetch current access, never reuse a different account's URL.
      const latest = await api.request<{ url: string }>(`/subscriptions/${item.id}/access`);
      await environment.importSubscription(client.id, latest.url); setOpened(true);
    })} onInstall={() => environment.openLink(client.install)} copy={environment.copy} />}</Resource>
    {opened && <p className="notice" role="status">{t.clientOpened}</p>}<Failure error={action.error} />
    {choose && <Dialog title={t.chooseClient} onClose={() => setChoose(false)}>{clients.map(value => <Button key={value.id} tone="secondary" onClick={() => { select(value.id); setChoose(false); }}>{value.name} · {value.platforms}</Button>)}</Dialog>}
  </>}</Resource>;
}

export function Trials() {
  const { api, refresh, navigate } = useApp();
  const state = useResource<{ offers: TrialOffer[] }>('/trial-offers');
  const action = useAction();
  const keys = useRef<Record<number, string>>({});
  return <><PageHeading title={t.trialTitle} /><Resource state={state}>{data => <section className="panel form-panel">
    {!data.offers.length && <Empty />}{data.offers.map((offer, index) => <div className="action-list" key={offer.offer?.offer_id ?? index}><h2>{offer.offer?.tariff_name || t.trialTitle}</h2><p>{offer.offer?.duration_days ?? t.unknown} дн.</p>
      <Button disabled={!offer.eligible || !offer.offer || action.busy} onClick={() => action.run(async () => { const id = offer.offer!.offer_id; const result = await api.request<Operation>(`/trials/${id}/activate`, 'POST', {}, keys.current[id] ??= mutationKey()); refresh(); navigate(result.key_id ? 'subscription/' + result.key_id : 'subscriptions'); })}>{t.trial}</Button>
    </div>)}<Failure error={action.error} />
  </section>}</Resource></>;
}

export function KeyOperation() {
  const { param, navigate } = useApp();
  const state = useResource<Operation>('/key-operations/' + encodeURIComponent(param));
  return <><PageHeading title={t.subscription} /><Resource state={state}>{value => <section className="panel form-panel"><h2>{value.state === 'completed' ? t.saved : value.state === 'failed' ? t.operationFailed : t.operationPending}</h2>
    {value.result?.error && <Failure error={{ code: value.result.error }} />}
    <Button onClick={state.retry}>{t.checkOperation}</Button>{value.key_id && <Button tone="secondary" onClick={() => navigate('subscription/' + value.key_id)}>{t.details}</Button>}
  </section>}</Resource></>;
}

export function Devices() {
  const { api, param, navigate } = useApp();
  const state = useResource<{ devices: { id: string; device_model: string; device_os: string; last_seen: number | null }[] }>(`/subscriptions/${encodeURIComponent(param)}/devices`);
  const [selected, setSelected] = useState<string>();
  const [key, setKey] = useState(mutationKey);
  const action = useAction();
  return <><PageHeading title={t.devices} back={() => navigate('subscription/' + param)} /><Resource state={state}>{data => <section className="panel form-panel">{!data.devices.length && <Empty />}{data.devices.map(device => <div className="list-row" key={device.id}><span>{device.device_model || device.device_os || t.unknown}<small>{date(device.last_seen)}</small></span><Button tone="quiet" onClick={() => { setSelected(device.id); setKey(mutationKey()); }}>{t.deleteDevice}</Button></div>)}</section>}</Resource>
    {selected && <Dialog title={t.confirmDevice} onClose={() => setSelected(undefined)}><Button disabled={action.busy} onClick={() => action.run(async () => { await api.request(`/subscriptions/${encodeURIComponent(param)}/devices/${encodeURIComponent(selected)}/delete`, 'POST', {}, key); setSelected(undefined); state.retry(); })}>{t.deleteDevice}</Button><Failure error={action.error} /></Dialog>}
  </>;
}

export function Hosts() {
  const { api, param, navigate } = useApp();
  const state = useResource<{ hosts: { id: number; custom_name: string | null; tariff_name: string | null }[] }>(`/subscriptions/${encodeURIComponent(param)}/host-candidates`);
  const action = useAction();
  const keys = useRef<Record<number, string>>({});
  return <><PageHeading title={t.hosts} back={() => navigate('subscription/' + param)} /><Resource state={state}>{data => <section className="panel form-panel">{!data.hosts.length && <Empty />}{data.hosts.map(host => <div className="list-row" key={host.id}><span>{host.custom_name || host.tariff_name || t.subscription}</span><Button disabled={action.busy} onClick={() => action.run(async () => { await api.request(`/subscriptions/${encodeURIComponent(param)}/host`, 'POST', { host_id: host.id }, keys.current[host.id] ??= mutationKey()); navigate('subscription/' + param); })}>{t.bind}</Button></div>)}<Button tone="quiet" onClick={() => navigate('subscription/' + param)}>{t.skip}</Button><Failure error={action.error} /></section>}</Resource></>;
}
