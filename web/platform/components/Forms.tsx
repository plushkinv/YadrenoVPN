import { text as _tr } from "../i18n/runtime";
import { cloneElement, useId, type ReactElement } from 'react';
import { Button } from './Ui';
import { errorText } from '../i18n/app';
export function Field({ label, children }: { label: string; children: ReactElement }) {
  const id = useId();
  return <div className="form-field"><label htmlFor={id}>{label}</label>{cloneElement(children as ReactElement<{ id: string }>, { id })}</div>;
}
export function Failure({ error, retry }: { error: unknown; retry?: () => void }) {
  if (!error) return null;
  return <div role="alert" className="system-failure"><p>{errorText(error)}</p>{retry && <Button onClick={retry}>{_tr("ApplicationBoundary.3")}</Button>}</div>;
}
export function Loading() { return <p role="status" className="system-loading">{_tr("Forms.1")}</p>; }
