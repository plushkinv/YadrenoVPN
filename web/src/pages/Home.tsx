import { ArrowRight, ArrowUpRight, ChevronDown, Clock3, Layers2, Smartphone, WifiOff } from 'lucide-react';
import { Badge, Button, PageHeading, RowButton } from '../components/Ui';
import { SubscriptionMetrics } from '../components/SubscriptionMetrics';
import { ru } from '../i18n/ru';
import type { ClientView, HomeState, SubscriptionView } from '../model';

export function Home({ state, subscription, hasMultiple, client, onSubscriptions, onConnect, onGuide, onClient, onRenew, onBuy, onTrial, onRetry,
  trialAvailable = true, renewAvailable = true, connecting = false, connectLabel, onSelectSubscription, onDevices, onDetails, canConnect = true }: {
  state: HomeState; subscription: SubscriptionView; hasMultiple: boolean; client: ClientView;
  onSubscriptions: () => void; onConnect: () => void; onGuide: () => void; onClient: () => void;
  onRenew: () => void; onBuy: () => void; onTrial: () => void; onRetry: () => void;
  trialAvailable?: boolean; renewAvailable?: boolean; connecting?: boolean; connectLabel?: string;
  onSelectSubscription?: () => void; onDevices?: () => void; onDetails?: () => void; canConnect?: boolean;
}) {
  if (state === 'loading' || state === 'offline' || state === 'unavailable') return <>
    <PageHeading title={ru.mySubscription} /><section className="panel empty-state" aria-busy={state === 'loading'}>
      {state === 'loading' ? <><span className="loader" /><h2>{ru.loading}</h2></> : <><WifiOff size={28} aria-hidden="true" />
        <h2>{state === 'offline' ? ru.offlineTitle : ru.unavailableTitle}</h2><p>{state === 'offline' ? ru.offlineCaption : ru.unavailableCaption}</p><Button onClick={onRetry}>{ru.retry}</Button></>}
    </section></>;
  if (state === 'new') return <><PageHeading title={ru.welcome} caption={ru.noSubscriptions} />
    <section className="panel welcome-card"><span className="icon-box"><Layers2 size={28} aria-hidden="true" /></span>
      <h2>{ru.newTitle}</h2><p>{ru.newCaption}</p><div className="action-list">
        {trialAvailable && <Button onClick={onTrial}>{ru.try}<ArrowRight size={20} aria-hidden="true" /></Button>}
        <Button tone={trialAvailable ? 'secondary' : 'primary'} onClick={onBuy}>{ru.buy}</Button>
      </div></section></>;
  const renewal = (state === 'expired' || state === 'traffic') && renewAvailable;
  const devices = state === 'devices';
  const healthy = ['active', 'trial', 'expiring'].includes(state);
  const label = renewal ? ru.renew : devices ? ru.manageDevices : !canConnect ? ru.openSubscription : connectLabel ?? ru.openClient(client.name);
  const action = renewal ? onRenew : devices ? onDevices ?? onDetails ?? onSubscriptions : !canConnect ? onDetails ?? onSubscriptions : onConnect;
  return <div className="home-grid"><section className="panel focus-card" data-ui="home.connection">
    <p className="section-kicker">{ru.mySubscription}</p><div className="subscription-heading"><h1>{subscription.name}</h1>
      {hasMultiple && <Button tone="secondary" className="icon-button subscription-switch" aria-label={ru.changeSubscription} aria-haspopup={onSelectSubscription ? 'dialog' : undefined}
        onClick={onSelectSubscription ?? onSubscriptions}><ChevronDown size={22} aria-hidden="true" /></Button>}</div>
    <div className="status-line"><Badge tone={healthy ? 'success' : 'warning'}>{ru.status[subscription.state]}</Badge></div>
    <p className="expiry">{ru.expires}: {subscription.expires}</p>
    {state === 'unknown' && <p className="state-hint">{ru.unknownCaption}</p>}
    {state === 'expired' && <p className="state-hint">{ru.expiredCaption}</p>}
    {state === 'traffic' && <p className="state-hint">{ru.trafficCaption}</p>}
    {devices && <p className="state-hint">{ru.devicesCaption}</p>}
    <Button className="wide main-action" disabled={connecting} onClick={action} data-ui="home.connect.primary">{label}{renewal || devices || !canConnect ? <ArrowRight size={20} aria-hidden="true" /> : <ArrowUpRight size={20} aria-hidden="true" />}</Button>
    {canConnect && !renewal && !devices && <><RowButton data-ui="home.client.change" icon={<Smartphone size={21} aria-hidden="true" />} title={client.name} caption={ru.client} onClick={onClient} />
      <Button tone="secondary" className="wide" onClick={onGuide}>{ru.connectionHelp}<ArrowRight size={20} aria-hidden="true" /></Button></>}
    {!canConnect && <Button tone="secondary" className="wide separated" onClick={onRetry}>{ru.refresh}</Button>}
  </section><div className="home-aside"><SubscriptionMetrics subscription={subscription} onDevices={onDevices} />
    {renewAvailable && !renewal && <section className="panel" data-ui="home.renew"><RowButton icon={<Clock3 size={22} aria-hidden="true" />} title={ru.renew} caption={ru.renewalCompactCaption} onClick={onRenew} /></section>}
  </div></div>;
}
