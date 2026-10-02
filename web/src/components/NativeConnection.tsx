import { useCallback, useSyncExternalStore } from 'react';
import type { NativeBridge } from '../runtime/environment';
import { useAction } from '../runtime/context';
import { Button } from './Ui';
import { Failure } from './Forms';

const titles = { unavailable: 'Нативное подключение недоступно', disconnected: 'VPN выключен', permission: 'Ожидаем разрешение',
  denied: 'Разрешение не предоставлено', connecting: 'Подключаемся…', connected: 'VPN подключён', error: 'Не удалось подключиться' };

/** The bridge alone reports connection state. Web/API health cannot set it. */
export function NativeConnection({ bridge, importProfile }: { bridge?: NativeBridge; importProfile?: () => Promise<void> }) {
  const subscribe = useCallback((listener: () => void) => bridge?.subscribe(listener) ?? (() => {}), [bridge]);
  useSyncExternalStore(subscribe, () => bridge ? `${bridge.state()}:${bridge.hasProfile()}` : 'unavailable:false');
  const state = bridge?.state() ?? 'unavailable';
  const action = useAction();
  const cancel = useAction();
  if (!bridge) return null;
  return <section className="panel form-panel" data-ui="connection.native">
    {bridge.simulator && <p className="notice">Симулятор · настоящее VPN-соединение не создаётся</p>}
    <div role="status"><h2>{titles[state]}</h2></div>
    {!bridge.hasProfile() && <p>Сначала добавьте подписку.</p>}
    <div className="button-row">
      {state === 'connected' ? <Button disabled={action.busy} onClick={() => action.run(() => bridge.disconnect())}>Отключить</Button>
        : state === 'permission' || state === 'connecting' ? <Button disabled={cancel.busy} onClick={() => cancel.run(() => bridge.cancel())}>Отменить подключение</Button>
        : <Button disabled={action.busy || state === 'unavailable' || !bridge.hasProfile()} onClick={() => action.run(() => bridge.connect())}>{state === 'error' || state === 'denied' ? 'Повторить подключение' : 'Подключить'}</Button>}
      {importProfile && <Button tone="secondary" disabled={action.busy} onClick={() => action.run(importProfile)}>Добавить подписку</Button>}
    </div><Failure error={action.error || cancel.error} />
  </section>;
}
