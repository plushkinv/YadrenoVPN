import { customization } from './config';
import { loadApplication } from './loader';
import { receiveRuntime } from './runtime/channel';
import { previewRuntime } from './preview/runtime';
import type { PreviewMessage } from './preview/contracts';

const element = document.getElementById('application')!;
let generation = 0, dispose: (() => void) | undefined;
let loading: AbortController | undefined;
const fail = () => {
  element.replaceChildren();
  const message = document.createElement('p'); message.textContent = 'Не удалось загрузить интерфейс. Повторите открытие приложения.';
  element.append(message);
};
window.addEventListener('message', async event => {
  if (window.parent === window || event.source !== window.parent || event.origin !== new URL(document.baseURI).origin) return;
  const message = event.data;
  if (customization.mode === 'preview') {
    if (message?.type !== 'yadreno.preview' || message.context?.contract_version !== 1 || !message.installation?.settings) return;
  } else if (message?.type !== 'yadreno.application.connect' || event.ports.length !== 1 || !message.state) return;
  const current = ++generation; loading?.abort(); dispose?.(); dispose = undefined;
  const controller = loading = new AbortController();
  const receiver = customization.mode === 'preview' ? undefined : receiveRuntime(event.ports[0], message.state);
  dispose = () => receiver?.close();
  try {
    const runtime = receiver?.runtime ?? await previewRuntime(message as PreviewMessage);
    const stop = await loadApplication(element, customization, runtime, controller.signal);
    if (current !== generation) { stop(); receiver?.close(); return; }
    dispose = () => { stop(); receiver?.close(); };
  } catch {
    if (current === generation) event.ports[0]?.postMessage({ type: 'failed' });
    receiver?.close();
    if (current === generation) fail();
  }
});
window.parent.postMessage({ type: customization.mode === 'preview' ? 'yadreno.preview.ready' : 'yadreno.application.ready' }, '*');
if (customization.mode === 'preview') window.addEventListener('pointerdown', () => window.parent.postMessage({ type: 'yadreno.preview.interaction' }, '*'));
window.addEventListener('pagehide', () => { generation++; loading?.abort(); dispose?.(); });
