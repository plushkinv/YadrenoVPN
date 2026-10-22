import { Check, CircleCheck, Clock3, LoaderCircle, XCircle } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { Catalog, Order, Quote } from '../api/contracts';
import { ApiError, mutationKey } from '../api/client';
import { PageHeading, Button, Dialog } from '../components/Ui';
import { Field, Failure, Resource } from '../components/Forms';
import { useApp, useAction, useBack, useResource } from '../runtime/context';
import { money, paymentMoney } from '../runtime/format';
import { appText as t, paymentMethodName } from '../i18n/app';
import { ru } from '../i18n/ru';
import { Purchase } from './Purchase';
import { SubscriptionHosts } from '../components/SubscriptionHosts';

interface PendingOrder {
  quote_id: string; key: string;
  order_id?: string; payment_type?: string; quote?: Quote; use_balance?: boolean;
}
const invalidQuotes = ['quote_changed', 'quote_expired', 'quote_unavailable', 'quote_not_found'];
export function Checkout() {
  const back = useBack();
  const { api, route, param, bootstrap, session, navigate, preview, storage } = useApp();
  const requestKey = `${session?.account_id}.order-request`;
  const [pending, setPending] = useState<PendingOrder | null>(() => storage.get(requestKey, null));
  const catalog = useResource<Catalog>('/catalog' + (route === 'renewal' ? `?subscription_id=${encodeURIComponent(param)}` : ''));
  const [tariffId, setTariff] = useState<number | undefined>(pending?.quote?.tariff_id ?? undefined);
  const [method, setMethod] = useState(pending?.payment_type ?? '');
  const [useBalance, setBalance] = useState(pending?.use_balance ?? false);
  const [calculation, setCalculation] = useState<{ key: string; quote?: Quote; error?: unknown }>();
  const [revision, setRevision] = useState(0);
  const action = useAction();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const tariff = catalog.data?.tariffs.find(item => item.id === tariffId) ?? catalog.data?.tariffs[0];
  const selectedMethod = tariff?.payment_methods.find(item => item.id === method)?.id ?? tariff?.payment_methods[0]?.id ?? '';
  const scope = `${session?.account_id}:${route}:${param}`;
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const mounted = useRef(false);
  const apiRef = useRef(api);
  apiRef.current = api;
  const calculationKey = JSON.stringify([scope, tariff?.id, selectedMethod, useBalance, revision]);
  const quote = pending?.quote ?? (calculation?.key === calculationKey ? calculation.quote : undefined);
  const quoteError = calculation?.key === calculationKey ? calculation.error : undefined;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    let active = true;
    if (!preview && !pending && tariff?.available && selectedMethod) {
      setCalculation({ key: calculationKey });
      apiRef.current.request<Quote>('/quotes', 'POST', { purpose: route === 'renewal' ? 'key_renewal' : 'key_purchase',
        tariff_id: tariff.id, ...(route === 'renewal' ? { key_id: Number(param) } : {}), payment_type: selectedMethod, use_balance: useBalance })
        .then(value => { if (active) setCalculation({ key: calculationKey, quote: value }); },
          error => { if (active) setCalculation({ key: calculationKey, error }); });
    }
    return () => { active = false; };
  }, [calculationKey, preview, Boolean(pending), tariff?.available]);
  function refreshQuote() { setCalculation(undefined); setRevision(value => value + 1); }
  const active = () => mounted.current && currentScope.current === scope;
  function savePending(value: PendingOrder, next: PendingOrder | null) {
    // A late response cannot overwrite a newer checkout after cancellation.
    if (storage.get<PendingOrder | null>(requestKey, null)?.key !== value.key) return;
    storage.set(requestKey, next);
    if (active()) setPending(next);
  }
  async function readOrder(value: PendingOrder) {
    const order = value.order_id ? await api.request<Order>('/orders/' + encodeURIComponent(value.order_id))
      : await api.request<Order>('/orders', 'POST', { quote_id: value.quote_id }, value.key);
    savePending(value, { ...value, order_id: order.order_id });
    return order;
  }
  function showOrder(value: PendingOrder, order: Order) {
    if (!active()) return;
    if (order.presentation) storage.set(`${session?.account_id}.order.${order.order_id}.presentation`, order.presentation);
    savePending(value, null);
    navigate('payment/' + encodeURIComponent(order.order_id));
  }
  async function cancelPreparation(value: PendingOrder) {
    try {
      // Recover only the original order identity; never retry invoice creation.
      let order = await readOrder(value);
      if (!active()) return;
      if (order.status !== 'paid' && !order.provider_confirmed) {
        try { order = await api.request<Order>('/orders/' + encodeURIComponent(order.order_id) + '/cancel', 'POST'); }
        catch (error) {
          if (!(error instanceof ApiError) || error.code !== 'order_already_paid') throw error;
          order = await api.request<Order>('/orders/' + encodeURIComponent(order.order_id));
        }
      }
      showOrder(value, order);
    } catch (error) {
      if (!value.order_id && error instanceof ApiError && invalidQuotes.includes(error.code)) {
        savePending(value, null);
        if (active()) { setConfirmCancel(false); refreshQuote(); }
        return;
      }
      throw error;
    }
  }
  async function createOrder(value: PendingOrder) {
    storage.set(requestKey, value); setPending(value);
    try {
      let order = await readOrder(value);
      value = { ...value, order_id: order.order_id, payment_type: value.payment_type ?? order.payment_type ?? undefined };
      savePending(value, value);
      if (!active()) return;
      if (order.status === 'pending' && !order.provider_confirmed && !order.payment_url) {
        order = await api.request<Order>('/orders/' + encodeURIComponent(order.order_id) + '/method', 'POST', { payment_type: value.payment_type });
      }
      showOrder(value, order);
    } catch (error) {
      if (!value.order_id && error instanceof ApiError && invalidQuotes.includes(error.code)) {
        savePending(value, null);
        if (active()) refreshQuote();
        throw new ApiError('quote_changed');
      }
      throw error;
    }
  }
  const recovery = pending && !action.busy && <div className="payment-actions">
    <Button onClick={() => action.run(() => createOrder(pending))}>{t.retry}</Button>
    <Button tone="quiet" onClick={() => { action.clear(); setConfirmCancel(true); }}>{t.cancelOrder}</Button>
  </div>;
  return <><Resource state={catalog}>{data => {
    if (!tariff) return <><PageHeading title={t.buy} back={back} /><p>{t.empty}</p>{recovery}<Failure error={action.error} /></>;
    const options = data.tariffs.map(item => ({ id: String(item.id), title: item.name,
      price: money(item.payable_amount_minor, item.base_currency), caption: item.duration_days ? `${item.duration_days} дн.` : t.unlimited,
      details: [item.traffic_limit_gb ? `${item.traffic_limit_gb} ГБ` : `${ru.traffic}: ${t.unlimited}`, item.max_ips ? `${ru.devices}: ${item.max_ips}` : `${ru.devices}: ${t.unlimited}`] }));
    const selected = options.find(item => item.id === String(tariff.id))!;
    const balanceOnly = quote && quote.balance_deduct_minor > 0 && (quote.payment_type === 'balance' || quote.charge_minor === 0);
    const free = quote && quote.charge_minor === 0 && quote.balance_deduct_minor === 0;
    const locked = action.busy || Boolean(pending);
    return <Purchase quotes={options} selected={selected} included={[
      tariff.traffic_limit_gb ? `${tariff.traffic_limit_gb} ГБ` : t.unlimited,
      tariff.max_ips ? `${t.devices}: ${tariff.max_ips}` : `${t.devices}: ${t.unlimited}`,
    ]} method={paymentMethodName(selectedMethod, tariff.payment_methods.find(item => item.id === selectedMethod)?.label)} renewalName={route === 'renewal' ? t.renew : undefined}
      onBack={back} onSelect={item => { setTariff(Number(item.id)); action.clear(); }}
      total={preview ? selected.price : quote ? money(balanceOnly ? quote.balance_deduct_minor : quote.charge_minor, balanceOnly ? quote.base_currency : quote.charge_currency) : quoteError || pending || !tariff.available || !selectedMethod ? t.unknown : t.calculating}
      totalLabel={balanceOnly ? t.fromBalance : t.charged}
      breakdown={quote && !balanceOnly && quote.balance_deduct_minor > 0 && <dl><div><dt>{t.fromBalance}</dt><dd>{money(quote.balance_deduct_minor, quote.base_currency)}</dd></div></dl>}
      disabled={locked || !tariff.available || !selectedMethod || (!preview && !quote)} selectionDisabled={locked}
      continueLabel={action.busy ? t.preparingPayment : balanceOnly ? t.payBalance : free ? route === 'renewal' ? t.renewFree : t.purchaseFree : t.pay}
      onContinue={() => action.run(async () => {
        if (preview) throw new ApiError('preview_only');
        if (!quote || pending) return;
        if (quote.expires_at <= Date.now() / 1000) { refreshQuote(); throw new ApiError('quote_changed'); }
        await createOrder({ quote_id: quote.quote_id, key: mutationKey(), payment_type: selectedMethod, quote, use_balance: useBalance });
      })}
      methodControl={<div className="action-list"><Field label={t.method} hideLabel><select disabled={locked} value={selectedMethod} onChange={e => { setMethod(e.target.value); action.clear(); }}>{tariff.payment_methods.map(item => <option value={item.id} key={item.id}>{paymentMethodName(item.id, item.label)}</option>)}</select></Field>
        {bootstrap.features.balance && <label className="check-field"><input type="checkbox" disabled={locked} checked={useBalance} onChange={e => { setBalance(e.target.checked); action.clear(); }} />{t.balanceUse}</label>}
        {!tariff.available && <p>{t.methodUnavailable}</p>}</div>}
      summary={<>{recovery}
        <Failure error={quoteError} retry={refreshQuote} /><Failure error={action.error} /></>} />;
  }}</Resource>
    {!catalog.data && pending && <section className="panel">{recovery}<Failure error={action.error} /></section>}
    {confirmCancel && pending && <Dialog title={t.confirmCancel} onClose={() => setConfirmCancel(false)}>
      <Button disabled={action.busy} onClick={() => action.run(() => cancelPreparation(pending))}>{t.cancelOrder}</Button>
      <Failure error={action.error} />
    </Dialog>}
  </>;
}

export function OrderStatus() {
  const back = useBack();
  const { api, param, environment, navigate, session, storage } = useApp();
  const state = useResource<Order>('/orders/' + encodeURIComponent(param));
  const action = useAction();
  const [confirm, setConfirm] = useState(false);
  const [placeholder, setPlaceholder] = useState(() => storage.get<string>(`${session?.account_id}.order.${param}.presentation`, '') === 'placeholder');
  useEffect(() => {
    const refresh = () => { if (document.visibilityState === 'visible') state.retry(); };
    window.addEventListener('pageshow', refresh); document.addEventListener('visibilitychange', refresh);
    return () => { window.removeEventListener('pageshow', refresh); document.removeEventListener('visibilitychange', refresh); };
  }, [param]);
  return <><PageHeading title={ru.payment} back={back} />
    <Resource state={state}>{order => {
      const paid = order.status === 'paid';
      const confirmed = paid || order.provider_confirmed;
      const ready = paid && order.access_status === 'ready';
      const canCheck = order.can_check ?? !(ready || paid && order.purpose === 'balance_topup');
      const canceled = order.status === 'canceled';
      const closed = canceled && !canCheck;
      const title = ready ? t.ready : paid ? order.purpose === 'balance_topup' ? t.paid : t.issuing : confirmed ? t.paymentCompleting : closed ? t.paymentCanceled : order.status === 'pending' ? t.pendingPayment : t.refused;
      return <><section className="panel payment-result" data-ui="payment.status">
        <ol className="payment-progress" aria-label={ru.order}><li className="is-complete"><Check size={18} aria-hidden="true" />{ru.order}</li><li className={confirmed ? 'is-complete' : 'is-current'}>{confirmed && <Check size={18} aria-hidden="true" />}{ru.payment}</li>{order.purpose !== 'balance_topup' && <li className={ready ? 'is-complete' : confirmed ? 'is-current' : ''}>{ready && <Check size={18} aria-hidden="true" />}{ru.access}</li>}</ol>
        <div className={'payment-symbol payment-symbol--' + (ready ? 'ready' : confirmed ? 'issuing' : order.status === 'pending' ? 'pending' : 'refused')} aria-hidden="true">
          {ready || paid && order.purpose === 'balance_topup' ? <CircleCheck size={32} /> : confirmed ? <LoaderCircle size={32} /> : order.status === 'pending' ? <Clock3 size={32} /> : <XCircle size={32} />}
        </div><div role="status" aria-live="polite"><h2>{title}</h2><p>{ready ? ru.paymentReadyCaption : confirmed && order.purpose !== 'balance_topup' ? ru.paymentIssuingCaption : closed ? t.paymentCanceledCaption : order.status === 'pending' ? t.paymentReturn : confirmed ? t.paid : ru.paymentRefusedCaption}</p></div>
        <div className="payment-receipt"><div><strong>{ru.order}</strong><span>{order.order_id}</span></div><strong>{order.charge_amount != null && order.charge_currency ? paymentMoney(order.charge_amount, order.charge_currency) : money(order.payable_amount_minor, order.base_currency)}</strong></div>
        {order.balance_deduct_minor > 0 && <dl><div><dt>{t.fromBalance}</dt><dd>{money(order.balance_deduct_minor, order.base_currency)}</dd></div></dl>}
        <div className="payment-actions">
          {!confirmed && order.status === 'pending' && <Button disabled={action.busy} onClick={() => action.run(async () => {
            const presentation = order.presentation ?? storage.get<string | undefined>(`${session?.account_id}.order.${order.order_id}.presentation`, undefined);
            if (presentation === 'placeholder') { setPlaceholder(true); return; }
            if (order.payment_url) { await environment.payment(order.payment_url, presentation); state.retry(); return; }
            const result = await api.request<Order>('/orders/' + encodeURIComponent(param) + '/method', 'POST', { payment_type: order.payment_type });
            state.retry();
            if (result.presentation) storage.set(`${session?.account_id}.order.${order.order_id}.presentation`, result.presentation);
            if (result.presentation === 'placeholder') { setPlaceholder(true); return; }
            if (result.payment_url) await environment.payment(result.payment_url, result.presentation);
            state.retry();
          })}>{t.pay}</Button>}
          {canCheck && <Button tone="secondary" disabled={action.busy} onClick={() => action.run(async () => { await api.request('/orders/' + encodeURIComponent(param) + '/check', 'POST'); state.retry(); })}>{t.checkPayment}</Button>}
          {ready && order.subscription_id && <Button onClick={() => navigate('connect/' + order.subscription_id)}>{t.connect}</Button>}
          {canceled && order.purpose !== 'balance_topup' && <Button onClick={() => navigate(order.purpose === 'key_renewal' && order.subscription_id ? 'renewal/' + order.subscription_id : 'purchase')}>{t.chooseTariff}</Button>}
          {!confirmed && order.status === 'pending' && <Button tone="quiet" onClick={() => setConfirm(true)}>{t.cancelOrder}</Button>}
        </div>{placeholder && <p role="status">{t.placeholder}</p>}<Failure error={action.error} />
      </section>{ready && order.purpose === 'key_purchase' && order.subscription_id && <SubscriptionHosts keyId={order.subscription_id} />}</>;
    }}</Resource>
    {confirm && <Dialog title={t.confirmCancel} onClose={() => setConfirm(false)}><Button disabled={action.busy} onClick={() => action.run(async () => { await api.request('/orders/' + encodeURIComponent(param) + '/cancel', 'POST'); setConfirm(false); state.retry(); })}>{t.cancelOrder}</Button><Failure error={action.error} /></Dialog>}
  </>;
}
