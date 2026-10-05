import { useState } from 'react';
import { Check, ChevronRight } from 'lucide-react';
import type { Page, Subscription } from '../api/contracts';
import { useResource } from '../runtime/context';
import { subscriptionView } from '../runtime/format';
import { ru } from '../i18n/ru';
import { Button, Dialog } from './Ui';
import { Resource } from './Forms';

export function SubscriptionPicker({ selected, onSelect, onClose }: { selected?: number; onSelect: (id: number) => void; onClose: () => void }) {
  const [offset, setOffset] = useState(0);
  const state = useResource<Page<Subscription>>('/subscriptions?limit=50&offset=' + offset);
  return <Dialog title={ru.changeSubscription} onClose={onClose}><Resource state={state}>{data => <>
    <div className="picker-options">{data.items.map(item => {
      const view = subscriptionView(item);
      return <Button tone="secondary" className="picker-option" key={item.id} aria-pressed={item.id === selected} onClick={() => { onSelect(item.id); onClose(); }}>
        <span><strong>{view.name}</strong><small>{view.plan} · {view.expires}</small></span>{item.id === selected ? <Check size={22} aria-hidden="true" /> : <ChevronRight size={22} aria-hidden="true" />}
      </Button>;
    })}</div><div className="button-row separated">
      {offset > 0 && <Button tone="secondary" onClick={() => setOffset(offset - 50)}>{ru.back}</Button>}
      {data.items.length === 50 && <Button tone="secondary" onClick={() => setOffset(offset + 50)}>{ru.continue}</Button>}
    </div>
  </>}</Resource></Dialog>;
}
