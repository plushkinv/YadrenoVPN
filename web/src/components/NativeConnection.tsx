import { text as _tr } from "../i18n/runtime";
import { useCallback, useSyncExternalStore } from 'react';
import type { NativeBridge } from '../runtime/environment';
import { useAction } from '../runtime/context';
import { Button } from './Ui';
import { Failure } from './Forms';

const titles = { get unavailable() { return _tr("NativeConnection.1"); }, get disconnected() { return _tr("NativeConnection.2"); }, get permission() { return _tr("NativeConnection.3"); },
  get denied() { return _tr("NativeConnection.4"); }, get connecting() { return _tr("NativeConnection.5"); }, get connected() { return _tr("NativeConnection.6"); }, get error() { return _tr("NativeConnection.7"); } };

/** The bridge alone reports connection state. Web/API health cannot set it. */
export function NativeConnection({ bridge, importProfile }: { bridge?: NativeBridge; importProfile?: () => Promise<void> }) {
  const subscribe = useCallback((listener: () => void) => bridge?.subscribe(listener) ?? (() => {}), [bridge]);
  useSyncExternalStore(subscribe, () => bridge ? `${bridge.state()}:${bridge.hasProfile()}` : 'unavailable:false');
  const state = bridge?.state() ?? 'unavailable';
  const action = useAction();
  const cancel = useAction();
  if (!bridge) return null;
  return <section className="panel form-panel" data-ui="connection.native">
    {bridge.simulator && <p className="notice">{_tr("NativeConnection.8")}</p>}
    <div role="status"><h2>{titles[state]}</h2></div>
    {!bridge.hasProfile() && <p>{_tr("NativeConnection.9")}</p>}
    <div className="button-row">
      {state === 'connected' ? <Button disabled={action.busy} onClick={() => action.run(() => bridge.disconnect())}>{_tr("NativeConnection.10")}</Button>
        : state === 'permission' || state === 'connecting' ? <Button disabled={cancel.busy} onClick={() => cancel.run(() => bridge.cancel())}>{_tr("NativeConnection.11")}</Button>
        : <Button disabled={action.busy || state === 'unavailable' || !bridge.hasProfile()} onClick={() => action.run(() => bridge.connect())}>{state === 'error' || state === 'denied' ? _tr("NativeConnection.12") : _tr("NativeConnection.13")}</Button>}
      {importProfile && <Button tone="secondary" disabled={action.busy} onClick={() => action.run(importProfile)}>{_tr("NativeConnection.14")}</Button>}
    </div><Failure error={action.error || cancel.error} />
  </section>;
}
