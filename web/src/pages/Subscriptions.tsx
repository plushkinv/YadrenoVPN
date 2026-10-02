import { ArrowRight, Check, Layers2, Plus } from 'lucide-react';
import { Badge, Button, PageHeading } from '../components/Ui';
import { ru } from '../i18n/ru';
import type { SubscriptionView } from '../model';

export function Subscriptions({ items, selected, onSelect, onBuy }: { items: SubscriptionView[]; selected: string; onSelect: (id: string) => void; onBuy: () => void }) {
  return <><PageHeading title={ru.subscriptions} caption={ru.subscriptionsCaption} action={<Button tone="secondary" onClick={onBuy}><Plus size={19} />{ru.newSubscription}</Button>} />
    {items.length === 0 ? <section className="panel empty-state"><Layers2 size={38} /><h2>{ru.noSubscriptions}</h2><Button onClick={onBuy}>{ru.buy}<ArrowRight size={18} /></Button></section> :
      <div className="subscription-list">{items.map(item => <article key={item.id} className={`panel subscription-card ${item.id === selected ? 'is-selected' : ''}`} data-ui="subscription.card">
        <div className="subscription-card-heading"><span className="subscription-icon"><Layers2 size={26} /></span><Badge tone={item.state === 'active' || item.state === 'trial' ? 'success' : 'warning'}>{ru.status[item.state]}</Badge></div>
        <h2>{item.name}</h2><p>{item.plan}</p>
        <dl><div><dt>{ru.expires}</dt><dd>{item.expires}</dd></div><div><dt>{ru.traffic}</dt><dd>{item.traffic ?? ru.unknown}</dd></div><div><dt>{ru.devices}</dt><dd>{item.devices ?? ru.unknown}</dd></div></dl>
        <Button tone={item.id === selected ? 'primary' : 'secondary'} onClick={() => onSelect(item.id)} aria-label={`${ru.selectSubscription}: ${item.name}`}>
          {item.id === selected ? <><Check size={18} />{ru.chosen}</> : <>{ru.choose}<ArrowRight size={18} /></>}
        </Button>
      </article>)}</div>}
  </>;
}
