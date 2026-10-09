import { ApiError } from '../api/client';
import { transportError } from './application-context';
import { RuntimeStore, type ApplicationRuntime, type RuntimeMethod, type RuntimeState } from './contract';

/** Ports belong to one document lifetime; disposal discards all late responses. */
export function serveRuntime(port: MessagePort, runtime: ApplicationRuntime, failed: () => void) {
  let active = true;
  const send = () => { if (active) port.postMessage({ type: 'state', state: runtime.getSnapshot() }); };
  const stop = runtime.subscribe(send);
  port.onmessage = async event => {
    if (!active) return;
    const message = event.data;
    if (message?.type === 'failed') { failed(); return; }
    if (message?.type !== 'call' || !Number.isSafeInteger(message.id) || message.id < 1 || typeof message.method !== 'string' || !Array.isArray(message.args)) return;
    try {
      const result = await runtime.call(message.method, message.args);
      if (active) port.postMessage({ type: 'result', id: message.id, result });
    } catch (error) { if (active) port.postMessage({ type: 'result', id: message.id, error: transportError(error) }); }
  };
  port.start(); send();
  return () => { active = false; stop(); port.onmessage = null; port.close(); };
}

export function receiveRuntime(port: MessagePort, initial: RuntimeState) {
  let counter = 0, active = true;
  const pending = new Map<number, { resolve(value: unknown): void; reject(error: unknown): void; timer: ReturnType<typeof setTimeout> }>();
  const runtime = new RuntimeStore(initial, (method: RuntimeMethod, args: unknown[]) => new Promise((resolve, reject) => {
    if (!active) { reject(new ApiError('network_unavailable', true)); return; }
    const id = ++counter;
    const timer = setTimeout(() => { pending.delete(id); reject(new ApiError('network_unavailable', true)); }, method === 'payment' ? 300000 : 30000);
    pending.set(id, { resolve, reject, timer });
    port.postMessage({ type: 'call', id, method, args });
  }));
  port.onmessage = event => {
    const value = event.data;
    if (value?.type === 'state' && value.state) { runtime.update(value.state); return; }
    if (value?.type !== 'result') return;
    const call = pending.get(value.id); if (!call) return;
    pending.delete(value.id); clearTimeout(call.timer);
    const error = value.error;
    if (error) call.reject(new ApiError(error.code, error.retryable, error.status, error.details, error.operationId));
    else call.resolve(value.result);
  };
  port.start();
  const close = () => {
    active = false; port.close();
    for (const call of pending.values()) { clearTimeout(call.timer); call.reject(new ApiError('network_unavailable', true)); }
    pending.clear();
  };
  return { runtime, close };
}
