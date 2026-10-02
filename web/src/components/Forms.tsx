import { cloneElement, useId, type FormEvent, type ReactNode, type ReactElement } from 'react';
import { appText as t, errorText } from '../i18n/app';
import { Button } from './Ui';
import { ApiError } from '../api/client';

export function Field({ label, children }: { label: string; children: ReactElement }) {
  const id = useId();
  return <div className="form-field"><label htmlFor={id}>{label}</label>{cloneElement(children as ReactElement<{ id: string }>, { id })}</div>;
}
export function Form({ children, onSubmit, busy, submit = t.continue, submitDisabled = false }: {
  children: ReactNode; onSubmit: () => void; busy: boolean; submit?: string; submitDisabled?: boolean;
}) {
  return <form className="app-form" onSubmit={(event: FormEvent) => { event.preventDefault(); if (!busy && !submitDisabled) onSubmit(); }}>
    <fieldset disabled={busy}>{children}</fieldset><Button type="submit" disabled={busy || submitDisabled}>{busy ? t.loading : submit}</Button>
  </form>;
}
export function Failure({ error, retry }: { error: unknown; retry?: () => void }) {
  if (!error) return null;
  return <div className="notice notice--error" role="alert"><p>{errorText(error)}</p>{error instanceof ApiError && error.operationId && <a href={'#operation/' + encodeURIComponent(error.operationId)}>{t.checkOperation}</a>}{retry && <Button tone="secondary" onClick={retry}>{t.retry}</Button>}</div>;
}
export function Loading() { return <div className="panel empty-state" role="status" aria-busy="true"><span className="loader" /><p>{t.loading}</p></div>; }
export function Empty() { return <p className="muted">{t.empty}</p>; }
export function Resource<T>({ state, children }: {
  state: { loading: boolean; error?: unknown; data?: T; retry: () => void }; children: (data: T) => ReactNode;
}) {
  return state.loading ? <Loading /> : state.error ? <Failure error={state.error} retry={state.retry} /> : state.data === undefined ? <Empty /> : <>{children(state.data)}</>;
}
