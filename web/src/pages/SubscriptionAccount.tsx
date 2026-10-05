import { ArrowUpRight, History, Pencil, RefreshCw, Settings2, Trash2 } from 'lucide-react';
import { useRef, useState } from 'react';
import type { Operation, Page, Subscription, TrialOffer } from '../api/contracts';
import { mutationKey } from '../api/client';
import { Badge, Button, Dialog, PageHeading, RowButton } from '../components/Ui';
import { Empty, Failure, Field, Form, Resource } from '../components/Forms';
import { Home } from './Home';
import { Subscriptions } from './Subscriptions';
import { Connect } from './Connect';
import { appText as t } from '../i18n/app';
import { ru } from '../i18n/ru';
import { useAction, useApp, useBack, useResource } from '../runtime/context';
import { clients } from '../runtime/environment';
import { date, subscriptionView } from '../runtime/format';
import { remember, stored } from '../runtime/storage';
import { NativeConnection } from '../components/NativeConnection';
import { SubscriptionHosts } from '../components/SubscriptionHosts';
import { ClientPicker } from '../components/ClientPicker';
import { SubscriptionPicker } from '../components/SubscriptionPicker';
import { SubscriptionMetrics } from '../components/SubscriptionMetrics';
import { selectedSubscriptionId, useSelectedSubscription } from '../runtime/selected-subscription';

// The opaque preview cannot use browser storage. Keep only its client choice
// for this frame's lifetime, including navigation and presentation changes.
let previewClientId = clients[0].id;

function useClientChoice() {
  const { session, preview } = useApp();
  const key = `${session?.account_id}.client`;
  const [id, setId] = useState(() => preview ? previewClientId : stored(key, clients[0].id));
  return { client: clients.find(value => value.id === id) ?? clients[0], select: (value: string) => {
    setId(value);
    if (preview) previewClientId = value; else remember(key, value);
  } };
}

function useClientImport(client: (typeof clients)[number]) {
  const { api, environment } = useApp();
  const action = useAction();
  const [notice, setNotice] = useState<string>();
  return { ...action, notice, add: (id: string | number) => action.run(async () => {
    setNotice(undefined);
    // Both entrypoints request current owned access at the time of the click.
    const access = await api.request<{ url: string }>('/subscriptions/' + encodeURIComponent(id) + '/access');
    if (client.scheme) await environment.importSubscription(client.id, access.url);
    else setNotice(await environment.copy(access.url) ? ru.copied : ru.copyFailed);
  }) };
}

export function HomeAccount() {
  const { environment } = useApp();
  return <><NativeConnection bridge={environment.native} /><HomeAccountContent /></>;
}

function HomeAccountContent() {
  const { navigate, bootstrap, environment } = useApp();
  const state = useSelectedSubscription();
  const { selected } = state;
  const { client, select } = useClientChoice();
  const clientImport = useClientImport(client);
  const [choose, setChoose] = useState(false), [chooseSubscription, setChooseSubscription] = useState(false);
  const empty = { id: '', name: '', plan: '', state: 'active' as const, expires: t.unknown, remaining: t.unknown, traffic: null, trafficPercent: null, devices: null };
  if (environment.native && selected) return <><PageHeading title={t.subscriptions} /><section className="panel form-panel"><h2>{selected.name || selected.tariff_name}</h2><p>{subscriptionView(selected).expires}</p><Button onClick={() => navigate('connect/' + selected.id)}>Добавить подписку</Button><Button tone="secondary" onClick={() => navigate('subscriptions')}>{t.subscriptions}</Button></section></>;
  if (state.error) return <Failure error={state.error} retry={state.retry} />;
  return <><Home state={state.loading ? 'loading' : selected ? subscriptionView(selected).state : 'new'} subscription={selected ? subscriptionView(selected) : empty}
    hasMultiple={state.hasMultiple} client={client} onSubscriptions={() => navigate('subscriptions')} onSelectSubscription={() => setChooseSubscription(true)}
    onDetails={() => navigate('subscription/' + selected?.id)}
    onDevices={selected?.devices_available ? () => navigate('devices/' + selected.id) : undefined}
    canConnect={selected?.access_status === 'ready' && selected.state !== 'disabled'}
    onConnect={() => { if (selected) void clientImport.add(selected.id); }} onGuide={() => navigate('connect/' + selected?.id)} onClient={() => setChoose(true)}
    onRenew={() => navigate('renewal/' + selected?.id)} onBuy={() => navigate('purchase')} onTrial={() => navigate('trials')} onRetry={state.retry}
    trialAvailable={bootstrap.features.trial} renewAvailable={selected?.actions['key.renew.start']?.allowed ?? false}
    connecting={clientImport.busy} connectLabel={client.scheme ? undefined : ru.copyForClient(client.name)} />
    <Failure error={clientImport.error} />{clientImport.notice && <p role="status" className="notice">{clientImport.notice}</p>}
    {choose && <ClientPicker selected={client.id} onSelect={id => { select(id); if (selected) navigate('connect/' + selected.id); }} onClose={() => setChoose(false)} />}
    {chooseSubscription && <SubscriptionPicker selected={selected?.id} onSelect={state.select} onClose={() => setChooseSubscription(false)} />}
  </>;
}

export function SubscriptionList() {
  const { navigate, session, preview } = useApp();
  const [offset, setOffset] = useState(0);
  const state = useResource<Page<Subscription>>('/subscriptions?limit=50&offset=' + offset);
  return <Resource state={state}>{data => <><Subscriptions items={data.items.map(subscriptionView)} selected={String(selectedSubscriptionId(session?.account_id, preview) ?? (offset === 0 ? data.items[0]?.id : undefined) ?? '')}
    onSelect={id => navigate('subscription/' + id)} onBuy={() => navigate('purchase')} />
    <div className="button-row separated">{offset > 0 && <Button tone="secondary" onClick={() => setOffset(offset - 50)}>{t.back}</Button>}{data.items.length === 50 && <Button tone="secondary" onClick={() => setOffset(offset + 50)}>{t.continue}</Button>}</div></>}</Resource>;
}

export function SubscriptionDetails() {
  const back = useBack();
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
  return <><PageHeading title={state.data?.name || t.subscription} caption={state.data?.tariff_name ?? undefined} back={back} /><Resource state={state}>{item => <div className="detail-layout">
    <div className="stack"><section className="panel detail-hero">
      <Badge tone={['active', 'trial', 'expiring'].includes(subscriptionView(item).state) ? 'success' : 'warning'}>{ru.status[subscriptionView(item).state]}</Badge>
      {item.access_status === 'ready' && <Button className="wide" onClick={() => navigate('connect/' + item.id)}>{t.connect}<ArrowUpRight size={20} aria-hidden="true" /></Button>}
      {item.actions['key.renew.start']?.allowed && <Button className="wide" tone="secondary" onClick={() => navigate('renewal/' + item.id)}>{ru.renew}</Button>}
    </section><SubscriptionMetrics subscription={subscriptionView(item)} onDevices={item.devices_available ? () => navigate('devices/' + item.id) : undefined} />
      <section className="panel padded"><dl className="detail-list"><div><dt>{t.server}</dt><dd>{item.server_name || t.unknown}</dd></div>
        <div><dt>{ru.tariff}</dt><dd>{item.tariff_name || t.unknown}</dd></div><div><dt>{ru.expires}</dt><dd>{date(item.expires_at)}</dd></div></dl></section>
    </div><div className="stack">
      {(['rename', 'configure', 'replace'] as const).some(verb => item.actions['key.' + verb + '.start']?.allowed) && <section className="panel row-group">
        {(['rename', 'configure', 'replace'] as const).filter(verb => item.actions['key.' + verb + '.start']?.allowed).map(verb => {
          const Icon = { rename: Pencil, configure: Settings2, replace: RefreshCw }[verb];
          return <RowButton key={verb} icon={<Icon size={21} aria-hidden="true" />} title={t[verb]} onClick={() => start(verb)} />;
        })}
      </section>}
      <section className="panel row-group"><RowButton icon={<History size={21} aria-hidden="true" />} title={t.history} onClick={() => navigate('history/' + item.id)} />
        {item.actions['key.delete']?.allowed && <RowButton danger icon={<Trash2 size={21} aria-hidden="true" />} title={t.delete} onClick={() => start('delete')} />}
      </section>
      {item.pending_operations.length > 0 && <section className="panel row-group">{item.pending_operations.map(operation => <RowButton icon={<RefreshCw size={21} aria-hidden="true" />} key={operation.id} title={t.checkOperation} onClick={() => navigate('operation/' + encodeURIComponent(operation.id))} />)}</section>}
    </div>
  </div>}</Resource>
    {edit && <Dialog title={edit === 'delete' ? t.confirmDelete : edit === 'replace' ? t.confirmReplace : t[edit]} onClose={() => setEdit(null)}>
      <Form onSubmit={mutate} busy={action.busy} submit={t[edit]}>
        {edit === 'rename' && <Field label={t.name}><input required maxLength={30} value={name} onChange={e => setName(e.target.value)} /></Field>}
        {(edit === 'configure' || edit === 'replace') && <Field label={t.server}><select required value={server ?? ''} onChange={e => setServer(Number(e.target.value))}>{state.data?.servers.map(value => <option key={value.id} value={value.id}>{value.name}</option>)}</select></Field>}
      </Form><Failure error={action.error} />
    </Dialog>}
  </>;
}

export function Connection() {
  const back = useBack();
  const { api, param, environment } = useApp();
  const subscription = useResource<Subscription>('/subscriptions/' + encodeURIComponent(param));
  const access = useResource<{ url: string }>('/subscriptions/' + encodeURIComponent(param) + '/access');
  const { client, select } = useClientChoice();
  const clientImport = useClientImport(client);
  const [choose, setChoose] = useState(false);
  if (environment.native) return <><PageHeading title={t.connect} back={back} /><NativeConnection bridge={environment.native} importProfile={async () => {
    const fresh = await api.request<{ url: string }>('/subscriptions/' + encodeURIComponent(param) + '/access');
    await environment.importSubscription('native', fresh.url);
  }} /></>;
  return <Resource state={subscription}>{item => <><Resource state={access}>{value => <Connect subscription={subscriptionView(item)} client={client} accessUrl={value.url}
    onBack={back} onClient={() => setChoose(true)} canImport={Boolean(client.scheme)} importing={clientImport.busy}
    onOpen={() => void clientImport.add(item.id)} onInstall={url => environment.openLink(url ?? client.install)} copy={environment.copy} />}</Resource>
    <Failure error={clientImport.error} />
    {choose && <ClientPicker selected={client.id} onSelect={select} onClose={() => setChoose(false)} />}
  </>}</Resource>;
}

export function Trials() {
  const back = useBack();
  const { api, refresh, navigate } = useApp();
  const state = useResource<{ offers: TrialOffer[] }>('/trial-offers');
  const action = useAction();
  const keys = useRef<Record<number, string>>({});
  return <><PageHeading title={t.trialTitle} back={back} /><Resource state={state}>{data => <section className="panel form-panel">
    {!data.offers.length && <Empty />}{data.offers.map((offer, index) => <div className="action-list" key={offer.offer?.offer_id ?? index}><h2>{offer.offer?.tariff_name || t.trialTitle}</h2><p>{offer.offer?.duration_days ?? t.unknown} дн.</p>
      <Button disabled={!offer.eligible || !offer.offer || action.busy} onClick={() => action.run(async () => {
        const id = offer.offer!.offer_id;
        const result = await api.request<Operation>(`/trials/${id}/activate`, 'POST', {}, keys.current[id] ??= mutationKey());
        const selection = result.composition && (!result.composition.ok || result.composition.status === 'selection_required');
        refresh(); navigate(result.key_id ? (selection ? 'hosts/' : 'subscription/') + result.key_id : 'subscriptions');
      })}>{t.trial}</Button>
    </div>)}<Failure error={action.error} />
  </section>}</Resource></>;
}

export function KeyOperation() {
  const back = useBack();
  const { param, navigate } = useApp();
  const state = useResource<Operation>('/key-operations/' + encodeURIComponent(param));
  return <><PageHeading title={t.subscription} back={back} /><Resource state={state}>{value => <><section className="panel form-panel"><h2>{value.state === 'completed' ? t.saved : value.state === 'failed' ? t.operationFailed : t.operationPending}</h2>
    {value.result?.error && <Failure error={{ code: value.result.error }} />}
    <Button onClick={state.retry}>{t.checkOperation}</Button>{value.key_id && <Button tone="secondary" onClick={() => navigate('subscription/' + value.key_id)}>{t.details}</Button>}
  </section>{value.state === 'completed' && value.key_id && value.result?.composition &&
    (!value.result.composition.ok || value.result.composition.status === 'selection_required') &&
    <SubscriptionHosts keyId={value.key_id} />}</>}</Resource></>;
}

export function Devices() {
  const back = useBack();
  const { api, param } = useApp();
  const state = useResource<{ devices: { id: string; device_model: string; device_os: string; last_seen: number | null }[] }>(`/subscriptions/${encodeURIComponent(param)}/devices`);
  const [selected, setSelected] = useState<string>();
  const [key, setKey] = useState(mutationKey);
  const action = useAction();
  return <><PageHeading title={t.devices} back={back} /><Resource state={state}>{data => <section className="panel form-panel">{!data.devices.length && <Empty />}{data.devices.map(device => <div className="list-row" key={device.id}><span>{device.device_model || device.device_os || t.unknown}<small>{date(device.last_seen)}</small></span><Button tone="quiet" onClick={() => { setSelected(device.id); setKey(mutationKey()); }}>{t.deleteDevice}</Button></div>)}</section>}</Resource>
    {selected && <Dialog title={t.confirmDevice} onClose={() => setSelected(undefined)}><Button disabled={action.busy} onClick={() => action.run(async () => { await api.request(`/subscriptions/${encodeURIComponent(param)}/devices/${encodeURIComponent(selected)}/delete`, 'POST', {}, key); setSelected(undefined); state.retry(); })}>{t.deleteDevice}</Button><Failure error={action.error} /></Dialog>}
  </>;
}

export function Hosts() {
  const back = useBack();
  const { param, navigate } = useApp();
  return <><PageHeading title={t.subscription} back={back} />
    <SubscriptionHosts keyId={param} showEmpty onClose={() => navigate('subscription/' + param)} /></>;
}
