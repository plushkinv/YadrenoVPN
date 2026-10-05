import { useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import { Button, PageHeading } from '../components/Ui';
import { Field, Form, Failure, Resource } from '../components/Forms';
import { useApp, useAction, useBack, useResource } from '../runtime/context';
import type { Profile, Session, Subscription, SubscriptionImportResult } from '../api/contracts';
import { appText as t } from '../i18n/app';
import { remember, stored } from '../runtime/storage';
import { usePhoneVerification, VerificationFields } from '../components/PhoneVerification';

export function Authentication() {
  const back = useBack();
  const { api, authenticated, bootstrap, route, navigate } = useApp();
  const [phone, setPhone] = useState('');
  const [password, setPassword] = useState('');
  const [current, setCurrent] = useState('');
  const [verificationRequired, setVerificationRequired] = useState(false);
  const [done, setDone] = useState(false);
  const action = useAction();
  const mode = route === 'register' ? 'register' : route === 'reset' ? 'reset' : route === 'credentials' ? 'credentials' : 'login';
  const verification = usePhoneVerification(phone, mode);
  const proof = verification.proof;
  const needsVerification = mode === 'reset' || mode === 'register' && bootstrap.auth.verification_required
    || mode !== 'login' && verificationRequired;
  const title = mode === 'register' ? t.register : mode === 'reset' ? t.reset : mode === 'credentials' ? t.credentials : t.login;
  async function submit() {
    await action.run(async () => {
      if (needsVerification && !proof) {
        await verification.advance();
        return;
      }
      const path = mode === 'credentials' ? '/account/credentials' : '/auth/' + mode;
      const body: Record<string, unknown> = { phone, password };
      if (proof) body.proof = proof;
      if (mode === 'credentials' && current) body.current_password = current;
      if (mode === 'register') {
        const ref = new URLSearchParams(location.search).get('ref');
        if (ref) body.referral_code = ref;
      }
      try {
        if (mode === 'reset') {
          await api.request('/auth/password/reset', 'POST', { phone, password, proof }); setDone(true); return;
        }
        const session = await api.request<Session>(path, 'POST', body);
        authenticated(session);
      } catch (error) {
        if (mode !== 'login' && error instanceof ApiError && error.code === 'verification_proof_required') {
          setVerificationRequired(true);
          verification.reset();
        } else {
          if (error instanceof ApiError && error.code === 'verification_proof_invalid') verification.reset();
          throw error;
        }
      }
    });
  }
  if (done) return <section className="panel"><p role="status">{t.resetDone}</p><Button onClick={() => navigate('login')}>{t.login}</Button></section>;
  return <><PageHeading title={title} back={mode === 'login' ? undefined : back} /><section className="panel form-panel">
    {bootstrap.auth.unverified_phone_warning_required && mode !== 'login' && <p className="notice">{t.noRecovery}</p>}
    <Form onSubmit={submit} busy={action.busy || verification.busy}
      submitDisabled={needsVerification && !proof && verification.submitDisabled}
      submit={needsVerification && !proof ? verification.challenge && !verification.terminal
        ? verification.phase === 'code_required' ? t.verifyCode : t.checkVerification
        : bootstrap.auth.verification_method === 'ucaller' ? t.requestCall : t.requestVerification : title}>
      <Field label={t.phone}><input type="tel" autoComplete="tel" required maxLength={64} value={phone} onChange={e => { setPhone(e.target.value); verification.reset(); setVerificationRequired(false); }} /></Field>
      {mode === 'credentials' && <Field label={t.currentPassword}><input type="password" autoComplete="current-password" maxLength={128} value={current} onChange={e => setCurrent(e.target.value)} /></Field>}
      <Field label={mode === 'login' ? t.password : t.newPassword}><input type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} required minLength={8} maxLength={128} value={password} onChange={e => setPassword(e.target.value)} /></Field>
      {needsVerification && <VerificationFields verification={verification} />}
    </Form><Failure error={action.error} />
    {needsVerification && verification.challenge && !proof && !verification.terminal && <Button tone="quiet"
      disabled={!verification.canResend || action.busy || verification.busy}
      onClick={() => action.run(() => verification.advance(true))}>{t.repeatVerification}</Button>}
    {mode === 'login' && <div className="button-row"><Button tone="quiet" onClick={() => navigate('register')}>{t.register}</Button>{bootstrap.auth.password_recovery_available && <Button tone="quiet" onClick={() => navigate('reset')}>{t.reset}</Button>}</div>}
  </section></>;
}

export function Account() {
  const back = useBack();
  const { navigate, logout } = useApp();
  const state = useResource<Profile>('/me');
  const action = useAction();
  return <><PageHeading title={t.account} back={back} /><Resource state={state}>{profile => <section className="panel form-panel">
    <h2>{profile.first_name || profile.username || `${t.account} ${profile.account_id}`}</h2>
    <p>{profile.credentials.phone}</p><div className="action-list">
      <Button tone="secondary" onClick={() => navigate('credentials')}>{t.credentials}</Button>
      {profile.telegram_id ? <p>{t.telegramLinked}</p> : <Button tone="secondary" onClick={() => navigate('telegram-link')}>{t.telegramLink}</Button>}
      <Button tone="secondary" onClick={() => navigate('import')}>{t.import}</Button>
    </div><Failure error={action.error} />
  </section>}</Resource><Button tone="quiet" disabled={action.busy} onClick={() => action.run(logout)}>{t.logout}</Button></>;
}

export function TelegramLink() {
  const back = useBack();
  const { api, environment, refresh, navigate, session } = useApp();
  const action = useAction();
  type Link = { token: string; telegram_url: string; expires_at: number };
  const savedKey = `${session?.account_id}.telegram-link`;
  const [link, setLink] = useState<Link | undefined>(() => {
    const value = stored<Link | null>(savedKey, null);
    return value && value.expires_at * 1000 > Date.now() ? value : undefined;
  });
  const [status, setStatus] = useState<{ state: string; telegram_id: number | null; first_name?: string }>();
  const runLink = (operation: () => Promise<void>) => action.run(async () => {
    try { await operation(); }
    catch (error) {
      if (error instanceof ApiError && error.code === 'link_invalid') {
        remember(savedKey, null); setLink(undefined); setStatus(undefined);
      }
      throw error;
    }
  });
  return <><PageHeading title={t.telegramLink} back={back} /><section className="panel form-panel">
    <p>{t.linkCaption}</p>{!link ? <Button disabled={action.busy} onClick={() => action.run(async () => { const value = await api.request<Link>('/account/telegram/link', 'POST'); remember(savedKey, value); setLink(value); })}>{t.continue}</Button> : <div className="action-list">
      <Button onClick={() => environment.openLink(link.telegram_url)}>{t.openTelegram}</Button>
      <Button tone="secondary" disabled={action.busy} onClick={() => runLink(async () => setStatus(await api.request('/account/telegram/link/status', 'POST', { token: link.token })))}>{t.linkCheck}</Button>
      {status?.telegram_id && status.state === 'confirmed' ? <><p>{status.first_name || String(status.telegram_id)}</p><Button disabled={action.busy} onClick={() => runLink(async () => {
        await api.request('/account/telegram/link/finish', 'POST', { token: link.token, telegram_id: status.telegram_id }); remember(savedKey, null); refresh(); navigate('account');
      })}>{t.linkFinish}</Button></> : status && <p role="status">{t.linkWaiting}</p>}
    </div>}<Failure error={action.error} />
  </section></>;
}

export function SubscriptionImport() {
  const back = useBack();
  const { api, refresh, navigate } = useApp();
  const [url, setUrl] = useState('');
  const [result, setResult] = useState<SubscriptionImportResult | null>(null);
  const [groupId, setGroupId] = useState('');
  const [pollError, setPollError] = useState<unknown>();
  const action = useAction();
  useEffect(() => {
    if (result?.state !== 'pending') return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function check() {
      try {
        const keys = await Promise.all(result!.key_ids.map(id => api.request<Subscription>('/subscriptions/' + id)));
        if (!active) return;
        setPollError(undefined);
        if (keys.length && keys.every(key => key.access_status === 'ready')) {
          setResult({ state: 'completed', key_ids: result!.key_ids, groups: [] }); refresh(); return;
        }
      } catch (error) { if (active) setPollError(error); }
      if (active) timer = setTimeout(check, 3000);
    }
    void check();
    return () => { active = false; clearTimeout(timer); };
  }, [api, result, refresh]);
  const submit = () => action.run(async () => {
    const next = await api.request<SubscriptionImportResult>('/subscription-imports', 'POST', { url, ...(groupId ? { group_id: Number(groupId) } : {}) });
    setResult(next); setPollError(undefined); refresh();
  });
  return <><PageHeading title={t.import} back={back} /><section className="panel form-panel">
    {result?.state === 'completed' ? <><div role="status"><h2>{t.importDone}</h2></div><Button onClick={() => navigate('subscriptions')}>{t.subscriptions}</Button></>
      : result?.state === 'pending' ? <><p role="status">{t.importPending}</p><Button disabled={action.busy} onClick={submit}>{t.retry}</Button><Button tone="secondary" onClick={() => navigate('subscriptions')}>{t.subscriptions}</Button></>
      : <><p>{t.importCaption}</p><Form busy={action.busy} submit={t.import} onSubmit={submit}>
        <Field label={t.importUrl}><input type="url" required maxLength={2048} autoComplete="off" value={url} onChange={e => { setUrl(e.target.value); setResult(null); setGroupId(''); }} /></Field>
        {result?.state === 'select_group' && <Field label={t.importGroup}><select required value={groupId} onChange={e => setGroupId(e.target.value)}>
          <option value="" disabled>{t.importGroup}</option>{result.groups.map(group => <option key={group.id} value={group.id}>{group.name}</option>)}
        </select></Field>}
      </Form></>}
    <Failure error={action.error || pollError} />
  </section></>;
}
