import { ArrowRight, Check, Layers2, Plus } from 'lucide-react';
import { Badge, Button, PageHeading } from '../components/Ui';
import { ru } from '../i18n/ru';
import type { SubscriptionView } from '../model';

export function Subscriptions({ items, selected, onSelect, onBuy }: { items: SubscriptionView[]; selected: string; onSelect: (id: string) => void; onBuy: () => void }) {
  return <><PageHeading title={ru.subscriptions} />
    {!items.length ? <section className="panel empty-state"><Layers2 size={28} aria-hidden="true" /><h2>{ru.noSubscriptions}</h2></section> :
      <div className="subscription-list">{items.map(item => <button type="button" key={item.id} className="panel subscription-card" data-ui="subscription.card"
        onClick={() => onSelect(item.id)} aria-label={ru.openSubscription + ': ' + item.name}>
        <span className="subscription-card-heading"><span className="subscription-icon"><Layers2 size={22} aria-hidden="true" /></span>
          <span className="subscription-card-copy"><strong>{item.name}</strong><span>{item.plan}</span></span><ArrowRight size={21} aria-hidden="true" /></span>
        <span className="subscription-card-foot"><Badge tone={['active', 'trial', 'expiring'].includes(item.state) ? 'success' : 'warning'}>{ru.status[item.state]}</Badge><span>{item.expires}</span></span>
        {item.id === selected && <span className="selected-label"><Check size={16} aria-hidden="true" />{ru.onHome}</span>}
      </button>)}</div>}
    <Button tone="secondary" className="wide separated" onClick={onBuy}><Plus size={20} aria-hidden="true" />{ru.newSubscription}</Button>
  </>;
}
