import { ArrowRight, CalendarDays, ChartNoAxesColumnIncreasing, CircleHelp, Clock3, Layers2, MonitorSmartphone, ShieldCheck, Smartphone, WifiOff } from 'lucide-react';
import { Badge, Button, ExternalArrow, PageHeading, RowButton } from '../components/Ui';
import { ru } from '../i18n/ru';
import type { ClientView, HomeState, SubscriptionView } from '../model';

export function Home({ state, subscription, hasMultiple, client, onSubscriptions, onConnect, onGuide, onClient, onRenew, onBuy, onTrial, onRetry, trialAvailable = true, renewAvailable = true }: {
  state: HomeState; subscription: SubscriptionView; hasMultiple: boolean; client: ClientView;
  onSubscriptions: () => void; onConnect: () => void; onGuide: () => void; onClient: () => void; onRenew: () => void;
  onBuy: () => void; onTrial: () => void; onRetry: () => void;
  trialAvailable?: boolean; renewAvailable?: boolean;
}) {
  const unavailable = state === 'offline' || state === 'unavailable';
  if (unavailable || state === 'loading') return <>
    <PageHeading title={ru.homeTitle} caption={ru.homeCaption} />
    <section className="panel empty-state" aria-busy={state === 'loading'}>
      {state === 'loading' ? <><span className="loader" /><h2>{ru.loading}</h2><div className="skeleton-line" /><div className="skeleton-line short" /></> : <>
        <div className="state-symbol muted"><WifiOff size={34} /></div>
        <h2>{state === 'offline' ? ru.offlineTitle : ru.unavailableTitle}</h2>
        <p>{state === 'offline' ? ru.offlineCaption : ru.unavailableCaption}</p>
        <Button onClick={onRetry}>{ru.retry}<ArrowRight size={18} /></Button>
      </>}
    </section>
  </>;
  if (state === 'new') return <>
    <PageHeading title={ru.homeTitle} caption={ru.homeCaption} />
    <section className="panel welcome-card" data-ui="home.welcome">
      <div className="welcome-symbol"><ShieldCheck size={44} strokeWidth={1.5} /></div>
      <span className="eyebrow">{ru.welcomeEyebrow}</span><h2>{ru.newTitle}</h2><p>{ru.newCaption}</p>
      <div className="button-row">{trialAvailable && <Button onClick={onTrial}>{ru.try}<ArrowRight size={18} /></Button>}<Button tone={trialAvailable ? 'secondary' : 'primary'} onClick={onBuy}>{ru.buy}</Button></div>
      <div className="welcome-points"><span><MonitorSmartphone size={18} />{ru.multipleDevices}</span><span><CircleHelp size={18} />{ru.setupHelp}</span></div>
    </section>
  </>;
  const blocked = state === 'expired' || state === 'traffic' || state === 'devices';
  const title = state === 'expired' ? ru.expiredTitle : state === 'traffic' ? ru.trafficTitle : state === 'devices' ? ru.devicesTitle : state === 'unknown' ? ru.status.unknown : ru.readyTitle;
  const caption = state === 'expired' ? ru.expiredCaption : state === 'traffic' ? ru.trafficCaption : state === 'devices' ? ru.devicesCaption : state === 'unknown' ? ru.unknownCaption : ru.readyCaption;
  return <>
    <PageHeading title={ru.homeTitle} caption={ru.homeCaption} />
    <div className="home-grid">
      <section className={`panel connection-card ${blocked ? 'connection-card--blocked' : ''}`} data-ui="home.connection">
        <div className="connection-top"><Badge tone={state === 'unknown' ? 'neutral' : state === 'expiring' || blocked ? 'warning' : 'success'}>{ru.status[state]}</Badge>
          {hasMultiple && <Button tone="quiet" className="subscription-switch" onClick={onSubscriptions} aria-label={ru.changeSubscription}><Layers2 size={17} /><span>{subscription.name}</span><ArrowRight size={16} /></Button>}
        </div>
        <div className="connection-main"><div className="connection-emblem" aria-hidden="true"><span /><ShieldCheck size={42} strokeWidth={1.5} /></div>
          <span className="eyebrow">{subscription.name}</span><h2>{title}</h2><p>{caption}</p>
          <Button disabled={blocked && state !== 'devices' && !renewAvailable} onClick={blocked ? state === 'devices' ? onSubscriptions : onRenew : onConnect} data-ui="home.connect.primary" className="connect-button">
            {blocked ? state === 'devices' ? ru.subscriptionDetails : ru.renew : ru.openClient(client.name)}{blocked ? <ArrowRight size={20} /> : <ExternalArrow />}
          </Button>
          {!blocked && <button type="button" className="text-button" onClick={onGuide}>{ru.connectionHelp}<ArrowRight size={16} /></button>}
        </div>
        <div className="subscription-metrics">
          <div><CalendarDays size={18} /><span>{ru.expires}</span><strong>{subscription.expires}</strong><small>{subscription.remaining}</small></div>
          <div><ChartNoAxesColumnIncreasing size={18} /><span>{ru.traffic}</span><strong>{subscription.traffic ?? ru.unknown}</strong>{subscription.trafficPercent !== null && <progress aria-label={ru.traffic} value={subscription.trafficPercent} max={100} />}</div>
          <div><MonitorSmartphone size={18} /><span>{ru.devices}</span><strong>{subscription.devices ?? ru.unknown}</strong><small>{ru.inSubscription}</small></div>
        </div>
      </section>
      <aside className="home-aside">
        <section className="panel client-card" data-ui="home.client"><span className="eyebrow">{ru.client}</span><div className="client-identity"><span className="client-icon"><Smartphone size={25} /></span><div><h3>{client.name}</h3><span>{ru.forDevice}</span></div></div><p>{ru.clientCaption}</p><Button tone="secondary" onClick={onClient} data-ui="home.client.change">{ru.change}<ArrowRight size={17} /></Button></section>
        {renewAvailable && <section className="renew-card"><Clock3 size={23} /><h3>{ru.renewalTitle}</h3><p>{ru.renewalCaption}</p><button type="button" className="text-button" onClick={onRenew} data-ui="home.renew">{ru.renew}<ArrowRight size={16} /></button></section>}
      </aside>
    </div>
    <p className="connection-note"><ShieldCheck size={17} />{ru.browserStatus}</p>
    <section className="panel help-row"><RowButton icon={<CircleHelp size={24} />} title={ru.quickHelp} caption={ru.quickHelpCaption} onClick={onGuide} /></section>
  </>;
}
