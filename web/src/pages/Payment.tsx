import { ArrowRight, Check, CircleCheck, Clock3, LoaderCircle, RefreshCw, XCircle } from 'lucide-react';
import { Badge, Button, ExternalArrow, PageHeading } from '../components/Ui';
import { ru } from '../i18n/ru';
import type { OrderView } from '../model';

export function Payment({ order, onBack, onPay, onCheck, onConnect }: { order: OrderView; onBack: () => void; onPay: () => void; onCheck: () => void; onConnect: () => void }) {
  const paid = order.state === 'issuing' || order.state === 'ready';
  const ready = order.state === 'ready';
  const messages = {
    pending: [ru.paymentPending, ru.paymentPendingCaption], issuing: [ru.paymentIssuing, ru.paymentIssuingCaption],
    ready: [ru.paymentReady, ru.paymentReadyCaption], refused: [ru.paymentRefused, ru.paymentRefusedCaption],
  };
  return <><PageHeading title={ru.payment} caption={`${ru.order} ${order.id}`} back={onBack} />
    <section className="panel payment-result" data-ui="payment.result">
      <ol className="payment-progress"><li className="is-complete"><Check size={16} />{ru.order}</li><li className={paid ? 'is-complete' : 'is-current'}>{paid ? <Check size={16} /> : <span>2</span>}{ru.payment}</li><li className={ready ? 'is-complete' : paid ? 'is-current' : ''}>{ready ? <Check size={16} /> : <span>3</span>}{ru.access}</li></ol>
      <div className={`payment-symbol payment-symbol--${order.state}`}>{ready ? <CircleCheck size={42} /> : order.state === 'issuing' ? <LoaderCircle size={42} /> : order.state === 'refused' ? <XCircle size={42} /> : <Clock3 size={42} />}</div>
      <div role="status" aria-live="polite"><h2>{messages[order.state][0]}</h2><p>{messages[order.state][1]}</p></div>
      <div className="payment-receipt"><div><strong>{order.subscriptionName}</strong><span>{order.quote.title} · {order.method}</span></div><strong>{order.quote.price}</strong></div>
      <dl className="payment-statuses"><div><dt>{ru.payment}</dt><dd><Badge tone={paid ? 'success' : 'neutral'}>{paid ? ru.paid : order.state === 'refused' ? ru.paymentRefused : ru.awaiting}</Badge></dd></div>
        <div><dt>{ru.access}</dt><dd>{ready ? ru.ready : paid ? ru.preparing : '—'}</dd></div></dl>
      <div className="payment-actions">{ready ? <Button onClick={onConnect}>{ru.goConnect}<ArrowRight size={20} /></Button> : paid ? <Button tone="secondary" onClick={onCheck}><RefreshCw size={18} />{ru.checkPayment}</Button> : order.state === 'refused' ? <Button onClick={onBack}>{ru.returnOrder}<ArrowRight size={20} /></Button> : <><Button onClick={onPay}>{ru.pay}<ExternalArrow /></Button><Button tone="quiet" onClick={onCheck}><RefreshCw size={17} />{ru.checkPayment}</Button></>}</div>
    </section>
  </>;
}
