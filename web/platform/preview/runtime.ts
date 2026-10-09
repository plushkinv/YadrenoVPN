import { ApiError } from '../api/client';
import { RuntimeStore, type RuntimeState } from '../runtime/contract';
import { navigationSection, parentRoute } from '../runtime/navigation';
import { PreviewApi } from './api';
import type { PreviewMessage } from './contracts';

/** Synthetic reads and frame-local presentation only; never delegates to live HTTP. */
export async function previewRuntime(message: PreviewMessage) {
  const api = new PreviewApi(message.installation, message.context);
  const [route, ...parts] = message.context.route.split('/');
  const state: RuntimeState = { session: await api.request('/auth/session'), bootstrap: await api.request('/bootstrap'),
    settings: await api.request('/ui/settings'), route, param: parts.join('/'), revision: 0,
    preset: message.context.preset, theme: message.context.theme, preview: true, section: navigationSection(route),
    launch: {}, status: { offline: false, newUi: false, updating: false }, storage: {}, insets: {}, environment: { kind: 'browser' } };
  const trail: string[] = [];
  const navigate = (next: string) => {
    const [route, ...parts] = next.split('/');
    runtime.update({ ...runtime.getSnapshot(), route, param: parts.join('/'), section: navigationSection(route) });
    window.parent.postMessage({ type: 'yadreno.preview.route', route: next }, '*');
  };
  const runtime = new RuntimeStore(state, async (method, args) => {
    const current = runtime.getSnapshot();
    switch (method) {
      case 'api': return api.request(String(args[0]), String(args[1]) as 'GET' | 'POST');
      case 'navigate': trail.push(current.route + (current.param ? '/' + current.param : '')); navigate(String(args[0])); return;
      case 'back': navigate(trail.pop() ?? parentRoute(current.route + (current.param ? '/' + current.param : ''))); return;
      case 'refresh': runtime.update({ ...current, revision: current.revision + 1 }); return;
      case 'storage.set': current.storage[String(args[0])] = args[1]; return;
      case 'setTheme':
        if (args[0] !== 'light' && args[0] !== 'dark') throw new ApiError('invalid_request');
        runtime.update({ ...current, theme: args[0] });
        window.parent.postMessage({ type: 'yadreno.preview.appearance', preset: current.preset, theme: args[0] }, '*'); return;
      case 'reload': window.parent.postMessage({ type: 'yadreno.preview.ready' }, '*'); return;
      case 'openLink': case 'copy': case 'share': case 'importSubscription': return false;
      default: throw new ApiError('preview_only');
    }
  });
  return runtime;
}
