import { useState } from 'react';
import { mutationKey } from '../api/client';
import { Button, PageHeading } from '../components/Ui';
import { Empty, Failure, Field, Form, Resource } from '../components/Forms';
import { appText as t } from '../i18n/app';
import { useApp, useAction, useResource } from '../runtime/context';
import { date, money } from '../runtime/format';
import type { Page } from '../api/contracts';

interface HistoryRow {
  id?: number | string; order_id?: string; created_at: string | null; paid_at?: string | null;
  purpose?: string; status?: string; payable_amount_minor?: number | null; base_currency?: string;
  delta_minor?: number; currency?: string; delta_days?: number | null; expires_after?: string | null;
}
interface BalanceView { amount_minor: number; currency: string; history: HistoryRow[]; }
const financialPurposes = new Set(['key_purchase', 'key_renewal', 'balance_topup']);
export function History() {
  const { route, param, navigate } = useApp();
  const [offset, setOffset] = useState(0);
  const path = route === 'balance' ? '/balance' : route === 'history' ? `/subscriptions/${encodeURIComponent(param)}/history` : '/payments';
  const state = useResource<Page<HistoryRow> | BalanceView>(path + '?limit=50&offset=' + offset);
  return <><PageHeading title={route === 'balance' ? t.balance : t.history} back={() => navigate(route === 'history' ? 'subscription/' + param : 'more')} />
    <Resource state={state}>{value => {
      const items = 'history' in value ? value.history : value.items;
      return <section className="panel form-panel">
        {'amount_minor' in value && <h2>{money(value.amount_minor, value.currency)}</h2>}
        {!items.length && <Empty />}{items.map((item, index) => <div className="list-row" key={item.order_id ?? item.id ?? index}>
          <span>{item.purpose === 'key_renewal' ? t.renew : item.purpose === 'key_purchase' ? t.buy : t.history}<small>{date(item.created_at)}</small></span>
          <span>{money(item.delta_minor ?? item.payable_amount_minor, item.currency ?? item.base_currency ?? '')}{item.delta_days != null && <small>{item.delta_days} дн.</small>}{item.expires_after && <small>{date(item.expires_after)}</small>}</span>
          {item.order_id && financialPurposes.has(item.purpose ?? '') && <Button tone="quiet" onClick={() => navigate('payment/' + encodeURIComponent(item.order_id!))}>{t.details}</Button>}
        </div>)}<div className="button-row">{offset > 0 && <Button tone="quiet" onClick={() => setOffset(offset - 50)}>{t.back}</Button>}{items.length === 50 && <Button onClick={() => setOffset(offset + 50)}>{t.continue}</Button>}</div>
      </section>;
    }}</Resource></>;
}

export function Promotion() {
  const { api, refresh } = useApp();
  const [code, setCode] = useState('');
  const [result, setResult] = useState<string>();
  const action = useAction();
  const [key, setKey] = useState(mutationKey);
  return <><PageHeading title={t.promo} /><section className="panel form-panel"><Form busy={action.busy} submit={t.activate} onSubmit={() => action.run(async () => {
    await api.request('/promotions/check', 'POST', { code });
    await api.request('/promotions/activate', 'POST', { code }, key); setResult(t.promoDone); refresh();
  })}><Field label={t.code}><input required maxLength={128} value={code} onChange={e => { setCode(e.target.value); setResult(undefined); setKey(mutationKey()); }} /></Field></Form>
    <Button tone="quiet" disabled={action.busy} onClick={() => action.run(async () => { await api.request('/promotions/clear', 'POST', {}, mutationKey()); setResult(t.saved); refresh(); })}>{t.clearPromo}</Button>
    {result && <p role="status">{result}</p>}<Failure error={action.error} />
  </section></>;
}

interface ReferralView {
  enabled: boolean; site_url?: string | null; telegram_url?: string | null; conditions_html?: string;
  statistics?: { level: number; paying_count: number; count: number; total_reward_minor: number; reward_currency: string | null; total_reward_days: number }[];
}
export function Referrals() {
  const { environment } = useApp();
  const state = useResource<ReferralView>('/referrals');
  const [copied, setCopied] = useState<boolean>();
  return <><PageHeading title={t.referrals} /><Resource state={state}>{data => <section className="panel form-panel">
    {!data.enabled ? <Empty /> : <>
      <div className="rich-content" dangerouslySetInnerHTML={{ __html: data.conditions_html ?? '' }} />
      {data.site_url && <><Field label={t.share}><input value={data.site_url} readOnly onFocus={e => e.target.select()} /></Field><div className="button-row"><Button onClick={async () => setCopied(await environment.copy(data.site_url!))}>{t.copy}</Button><Button tone="secondary" onClick={() => environment.share(data.site_url!)}>{t.share}</Button></div></>}
      {copied !== undefined && <p role="status">{copied ? t.copied : t.copyFailed}</p>}
      {data.statistics?.map(item => <div className="list-row" key={item.level}><span>{item.level} · {item.count} / {item.paying_count}</span><span>{money(item.total_reward_minor, item.reward_currency ?? '')} · {item.total_reward_days} дн.</span></div>)}
    </>}
  </section>}</Resource></>;
}

export function Help() {
  const { environment } = useApp();
  const state = useResource<{ content_html: string; links: { label: string | null; url: string }[] }>('/help');
  return <><PageHeading title={t.help} /><Resource state={state}>{data => <section className="panel form-panel">
    <div className="rich-content" dangerouslySetInnerHTML={{ __html: data.content_html }} />
    <div className="action-list">{data.links.map((link, index) => <Button tone="secondary" key={index} onClick={() => environment.openLink(link.url)}>{link.label || t.help}</Button>)}</div>
  </section>}</Resource></>;
}

export function Appearance() {
  const { preset, theme, appearance } = useApp();
  return <><PageHeading title={t.appearance} /><section className="panel form-panel"><Field label={t.appearance}><select value={preset} onChange={e => appearance(e.target.value as typeof preset, theme)}><option value="clear">Universal / Clear</option><option value="signal">Signal / Dark</option><option value="friendly">Friendly / Brand</option></select></Field><div className="button-row"><Button tone={theme === 'light' ? 'primary' : 'secondary'} onClick={() => appearance(preset, 'light')}>{t.light}</Button><Button tone={theme === 'dark' ? 'primary' : 'secondary'} onClick={() => appearance(preset, 'dark')}>{t.dark}</Button></div></section></>;
}

export function MoreAccount() {
  const { navigate, bootstrap } = useApp();
  const items = [
    ['account', t.account, true], ['appearance', t.appearance, true], ['payments', t.paymentHistory, true],
    ['balance', t.balance, bootstrap.features.balance], ['promotion', t.promo, bootstrap.features.promotions],
    ['referrals', t.referrals, bootstrap.features.referrals], ['import', t.import, bootstrap.features.subscription_import], ['help', t.help, true],
  ] as const;
  return <><PageHeading title="Ещё" /><section className="panel form-panel"><div className="action-list">{items.filter(item => item[2]).map(([path, label]) => <Button key={path} tone="secondary" onClick={() => navigate(path)}>{label}</Button>)}</div></section></>;
}
