import { ArrowRight, Check, CreditCard, LockKeyhole } from 'lucide-react';
import { Button, CheckList, PageHeading } from '../components/Ui';
import { ru } from '../i18n/ru';
import type { QuoteView } from '../model';
import type { ReactNode } from 'react';

export function Purchase({ quotes, selected, included, method, onSelect, onContinue, onBack, renewalName, methodControl, summary, continueLabel, disabled = false, selectionDisabled = false }: {
  quotes: QuoteView[]; selected: QuoteView; included: readonly string[]; method: string;
  onSelect: (quote: QuoteView) => void; onContinue: () => void; onBack: () => void; renewalName?: string;
  methodControl?: ReactNode; summary?: ReactNode; continueLabel?: string; disabled?: boolean; selectionDisabled?: boolean;
}) {
  return <><PageHeading title={renewalName ? ru.renewalPageTitle : ru.purchaseTitle} caption={renewalName ?? ru.purchaseCaption} back={onBack} />
    <div className="purchase-layout"><div className="purchase-options">
      <section className="panel purchase-period"><h2>{ru.period}</h2><div role="group" aria-label={ru.period} className="quote-grid">
        {quotes.map(quote => <button type="button" disabled={selectionDisabled} key={quote.id} className={`quote-option ${quote.id === selected.id ? 'is-selected' : ''}`} aria-pressed={quote.id === selected.id} onClick={() => onSelect(quote)}>
          <span className="quote-check">{quote.id === selected.id && <Check size={13} strokeWidth={3} />}</span><span>{quote.title}</span><strong>{quote.price}</strong><small>{quote.caption}</small>
        </button>)}
      </div><div className="included"><h3>{ru.included}</h3><CheckList items={included} /></div></section>
      <section className="panel method-card"><h2>{ru.paymentMethod}</h2>{methodControl ?? <div className="payment-method"><span className="method-symbol"><CreditCard size={25} /></span><div><strong>{method}</strong><span>{ru.paymentMethodCaption}</span></div><span className="selected-radio"><Check size={14} /></span></div>}</section>
    </div><aside className="panel order-summary" data-ui="purchase.summary"><span className="eyebrow">{ru.orderSummary}</span><h2>{renewalName ?? ru.subscription}</h2><dl><div><dt>{ru.period}</dt><dd>{selected.title}</dd></div><div><dt>{ru.paymentMethod}</dt><dd>{method}</dd></div></dl>
      <div className="order-total"><span>{ru.total}</span><strong>{selected.price}</strong></div><Button disabled={disabled} onClick={onContinue} data-ui="purchase.continue">{continueLabel ?? ru.continue}<ArrowRight size={20} /></Button>{summary}<p className="payment-safety"><LockKeyhole size={16} />{ru.paymentSecure}</p>
    </aside></div>
  </>;
}
