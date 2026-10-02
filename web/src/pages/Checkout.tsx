import { useEffect, useState } from 'react';
import type { Catalog, Order, Quote } from '../api/contracts';
import { ApiError, mutationKey } from '../api/client';
import { PageHeading, Button, Dialog } from '../components/Ui';
import { Field, Failure, Resource } from '../components/Forms';
import { useApp, useAction, useResource } from '../runtime/context';
import { money } from '../runtime/format';
import { remember, stored } from '../runtime/storage';
import { appText as t } from '../i18n/app';
import { Purchase } from './Purchase';
import { paymentMethodName } from '../i18n/app';

interface PendingOrder { quote_id: string; key: string; }
export function Checkout() {
  const { api, route, param, bootstrap, session, navigate } = useApp();
  const catalog = useResource<Catalog>('/catalog' + (route === 'renewal' ? `?subscription_id=${encodeURIComponent(param)}` : ''));
  const [tariffId, setTariff] = useState<number>();
  const [method, setMethod] = useState('');
  const [useBalance, setBalance] = useState(false);
  const [quote, setQuote] = useState<Quote>();
  const [pending, setPending] = useState<PendingOrder | null>(() => stored(`${session?.account_id}.order-request`, null));
  const action = useAction();
  const tariff = catalog.data?.tariffs.find(item => item.id === tariffId) ?? catalog.data?.tariffs[0];
  const selectedMethod = tariff?.payment_methods.find(item => item.id === method)?.id ?? tariff?.payment_methods[0]?.id ?? '';
  async function createOrder(value: PendingOrder) {
    setPending(value); remember(`${session?.account_id}.order-request`, value);
    try {
      const order = await api.request<Order>('/orders', 'POST', { quote_id: value.quote_id }, value.key);
      remember(`${session?.account_id}.order-request`, null); setPending(null);
      navigate('payment/' + encodeURIComponent(order.order_id));
    } catch (error) {
      if (error instanceof ApiError && ['quote_changed', 'quote_expired', 'quote_unavailable', 'quote_not_found'].includes(error.code)) {
        remember(`${session?.account_id}.order-request`, null); setPending(null); setQuote(undefined); catalog.retry();
      }
      throw error;
    }
  }
  return <Resource state={catalog}>{data => {
    if (!tariff) return <><PageHeading title={t.buy} /><p>{t.empty}</p></>;
    const options = data.tariffs.map(item => ({ id: String(item.id), title: item.name,
      price: money(item.payable_amount_minor, item.base_currency), caption: `${item.duration_days} дн.` }));
    const selected = options.find(item => item.id === String(tariff.id))!;
    return <Purchase quotes={options} selected={selected} included={[
      tariff.traffic_limit_gb ? `${tariff.traffic_limit_gb} ГБ` : t.unlimited,
      tariff.max_ips ? `${t.devices}: ${tariff.max_ips}` : `${t.devices}: ${t.unlimited}`,
    ]} method={paymentMethodName(selectedMethod)} renewalName={route === 'renewal' ? t.renew : undefined}
      onBack={() => navigate('subscriptions')} onSelect={item => { setTariff(Number(item.id)); setQuote(undefined); }}
      disabled={action.busy || !tariff.available || !selectedMethod || Boolean(pending)} selectionDisabled={action.busy || Boolean(pending)} continueLabel={quote ? t.confirmOrder : t.quote}
      onContinue={() => action.run(async () => {
        if (quote) await createOrder({ quote_id: quote.quote_id, key: mutationKey() });
        else setQuote(await api.request<Quote>('/quotes', 'POST', { purpose: route === 'renewal' ? 'key_renewal' : 'key_purchase',
          tariff_id: tariff.id, ...(route === 'renewal' ? { key_id: Number(param) } : {}), payment_type: selectedMethod, use_balance: useBalance }));
      })}
      methodControl={<div className="action-list"><Field label={t.method}><select disabled={action.busy || Boolean(pending)} value={selectedMethod} onChange={e => { setMethod(e.target.value); setQuote(undefined); }}>{tariff.payment_methods.map(item => <option value={item.id} key={item.id}>{paymentMethodName(item.id)}</option>)}</select></Field>
        {bootstrap.features.balance && <label className="check-field"><input type="checkbox" disabled={action.busy || Boolean(pending)} checked={useBalance} onChange={e => { setBalance(e.target.checked); setQuote(undefined); }} />{t.balanceUse}</label>}
        {!tariff.available && <p>{t.methodUnavailable}</p>}</div>}
      summary={<>{quote && <dl><div><dt>{t.charged}</dt><dd>{money(quote.charge_minor, quote.charge_currency)}</dd></div><div><dt>{t.fromBalance}</dt><dd>{money(quote.balance_deduct_minor, quote.base_currency)}</dd></div></dl>}
        {pending && <Button disabled={action.busy} onClick={() => action.run(() => createOrder(pending))}>{t.checkAgain}</Button>}<Failure error={action.error} /></>} />;
  }}</Resource>;
}

export function OrderStatus() {
  const { api, param, environment, navigate } = useApp();
  const state = useResource<Order>('/orders/' + encodeURIComponent(param));
  const action = useAction();
  const [confirm, setConfirm] = useState(false);
  const [placeholder, setPlaceholder] = useState(false);
  useEffect(() => {
    const refresh = () => { if (document.visibilityState === 'visible') state.retry(); };
    window.addEventListener('pageshow', refresh); document.addEventListener('visibilitychange', refresh);
    return () => { window.removeEventListener('pageshow', refresh); document.removeEventListener('visibilitychange', refresh); };
  }, [param]);
  return <><PageHeading title={t.paymentHistory} back={() => navigate('payments')} />
    <Resource state={state}>{order => {
      const paid = order.status === 'paid';
      const confirmed = paid || order.provider_confirmed;
      const ready = paid && order.access_status === 'ready';
      const title = ready ? t.ready : paid ? order.purpose === 'balance_topup' ? t.paid : t.issuing : confirmed ? t.paymentCompleting : order.status === 'pending' ? t.pendingPayment : t.refused;
      return <section className="panel form-panel" data-ui="payment.status"><div role="status"><h2>{title}</h2></div>
        <p>{order.order_id}</p><strong>{money(order.payable_amount_minor, order.base_currency)}</strong><p className="muted">{t.paymentReturn}</p>
        <div className="action-list">
          {!confirmed && order.status === 'pending' && <Button disabled={action.busy} onClick={() => action.run(async () => {
            const result = await api.request<Order>('/orders/' + encodeURIComponent(param) + '/method', 'POST', { payment_type: order.payment_type });
            state.retry();
            if (result.presentation === 'placeholder') { setPlaceholder(true); return; }
            if (result.payment_url) await environment.payment(result.payment_url, result.presentation);
            state.retry();
          })}>{t.pay}</Button>}
          <Button tone="secondary" disabled={action.busy} onClick={() => action.run(async () => { await api.request('/orders/' + encodeURIComponent(param) + '/check', 'POST'); state.retry(); })}>{t.checkPayment}</Button>
          {ready && order.subscription_id && <Button onClick={() => navigate('connect/' + order.subscription_id)}>{t.connect}</Button>}
          {!confirmed && order.status === 'pending' && <Button tone="quiet" onClick={() => setConfirm(true)}>{t.cancelOrder}</Button>}
        </div>{placeholder && <p role="status">{t.placeholder}</p>}<Failure error={action.error} />
      </section>;
    }}</Resource>
    {confirm && <Dialog title={t.confirmCancel} onClose={() => setConfirm(false)}><Button disabled={action.busy} onClick={() => action.run(async () => { await api.request('/orders/' + encodeURIComponent(param) + '/cancel', 'POST'); setConfirm(false); state.retry(); })}>{t.cancelOrder}</Button><Failure error={action.error} /></Dialog>}
  </>;
}
