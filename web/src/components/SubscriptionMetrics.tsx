import { ArrowRight } from 'lucide-react';
import type { SubscriptionView } from '../model';
import { ru } from '../i18n/ru';

export function SubscriptionMetrics({ subscription, onDevices }: { subscription: SubscriptionView; onDevices?: () => void }) {
  const devices = <><span className="muted">{ru.devices}</span><strong>{subscription.devices ?? ru.unknown}</strong>
    {onDevices && <span className="metric-link">{ru.manage}<ArrowRight size={18} aria-hidden="true" /></span>}</>;
  return <section className="panel usage-metrics" aria-label={ru.usage}>
    <div className="traffic-metric"><span className="muted">{ru.traffic}</span><strong>{subscription.traffic ?? ru.unknown}</strong>
      {subscription.trafficPercent != null && <progress aria-label={ru.traffic} max={100} value={subscription.trafficPercent} />}</div>
    {onDevices ? <button type="button" className="device-metric" onClick={onDevices}>{devices}</button> : <div className="device-metric">{devices}</div>}
  </section>;
}
