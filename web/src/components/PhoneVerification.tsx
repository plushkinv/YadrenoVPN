import { useCallback, useEffect, useRef, useState } from 'react';
import type { PhoneVerification } from '../api/contracts';
import { useApp } from '../runtime/context';
import { appText as t } from '../i18n/app';
import { Field, Failure } from './Forms';

export function usePhoneVerification(phone: string, purpose: string) {
  const { api } = useApp();
  const [challenge, setChallenge] = useState<PhoneVerification>();
  const [proof, setProof] = useState<string>();
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [now, setNow] = useState(Date.now() / 1000);
  const revision = useRef(0), occupied = useRef(false);
  const reset = useCallback(() => {
    revision.current++; occupied.current = false;
    setChallenge(undefined); setProof(undefined); setCode(''); setError(undefined); setBusy(false);
  }, []);
  useEffect(() => { reset(); return () => { revision.current++; }; }, [phone, purpose, reset]);
  const apply = useCallback((result: PhoneVerification, expected: number) => {
    if (revision.current !== expected) return;
    setNow(Date.now() / 1000);
    setChallenge(result); setError(undefined);
    if (result.proof) setProof(result.proof);
  }, []);
  useEffect(() => {
    if (!challenge) return;
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [challenge?.challenge_id]);
  useEffect(() => {
    if (challenge && now >= challenge.expires_at) setProof(undefined);
  }, [challenge?.expires_at, now]);
  useEffect(() => {
    if (!challenge || challenge.method !== 'smsaero_mobile' || proof ||
        !['waiting', 'code_required', 'confirmed'].includes(challenge.state)) return;
    let stopped = false, timer: number;
    const selected = challenge;
    async function poll() {
      if (stopped || Date.now() / 1000 >= selected.expires_at) return;
      const expected = revision.current;
      try {
        if (!occupied.current) {
          const result = await api.request<PhoneVerification>('/auth/verification/status', 'POST', { challenge_id: selected.challenge_id });
          if (stopped || revision.current !== expected || occupied.current) return;
          if (result.state === 'confirmed') {
            occupied.current = true; setBusy(true);
            try {
              const verified = await api.request<PhoneVerification>('/auth/verification/verify', 'POST', { challenge_id: selected.challenge_id });
              if (!stopped) apply(verified, expected);
            } finally {
              if (revision.current === expected) { occupied.current = false; setBusy(false); }
            }
          } else apply(result, expected);
        }
      } catch (failure) {
        if (!stopped && revision.current === expected) setError(failure);
      } finally {
        if (!stopped) timer = window.setTimeout(poll, 5000);
      }
    }
    timer = window.setTimeout(poll, selected.state === 'confirmed' ? 0 : 5000);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [api, challenge?.challenge_id, challenge?.method, challenge?.state, proof, apply]);

  const phase = challenge && now >= challenge.expires_at && !proof ? 'expired' : challenge?.state;
  const terminal = phase !== undefined && ['expired', 'failed', 'unknown', 'verified'].includes(phase) && !proof;
  const canResend = !challenge || now >= challenge.resend_at;
  async function advance(restart = false) {
    if (occupied.current) return;
    if ((restart || terminal) && !canResend) return;
    occupied.current = true; setBusy(true); setError(undefined);
    const expected = ++revision.current;
    try {
      const fresh = restart || terminal || !challenge;
      const path = fresh ? 'request' : phase === 'code_required' ? 'verify' : 'status';
      const body = fresh ? { phone, purpose } : phase === 'code_required'
        ? { challenge_id: challenge!.challenge_id, code } : { challenge_id: challenge!.challenge_id };
      const result = await api.request<PhoneVerification>('/auth/verification/' + path, 'POST', body);
      if (fresh && revision.current === expected) { setCode(''); setProof(undefined); }
      apply(result, expected);
    } finally {
      if (revision.current === expected) { occupied.current = false; setBusy(false); }
    }
  }
  return { challenge, proof, code, setCode, busy, error, phase, terminal, canResend,
    advance, reset, setProof, submitDisabled: terminal && !canResend };
}

export function VerificationFields({ verification }: { verification: ReturnType<typeof usePhoneVerification> }) {
  const { challenge, phase, proof, code, setCode, error } = verification;
  if (!challenge) return null;
  let caption = proof ? t.verificationReady : phase === 'expired' ? t.verificationExpired :
    phase === 'failed' ? t.verificationFailed : phase === 'unknown' ? t.verificationUnknown :
    phase === 'waiting' || phase === 'confirmed' ? t.mobileWaiting :
    challenge.send_state === 'unknown' ? t.verificationUncertain :
    challenge.method === 'ucaller' ? t.callSent : t.codeSent;
  return <><p role="status">{caption}</p>
    {!proof && phase === 'code_required' && <Field label={challenge.method === 'ucaller' ? t.callCode : t.smsCode}>
      <input inputMode="numeric" autoComplete="one-time-code" required
        pattern={challenge.code_length ? '[0-9]{' + challenge.code_length + '}' : '[0-9]+'}
        maxLength={challenge.code_length ?? 32} value={code} onChange={event => setCode(event.target.value)} />
    </Field>}<Failure error={error} /></>;
}
