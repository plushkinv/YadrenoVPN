import { customization } from '../config';

/** Poll only alongside subscription refresh; an unchanged ETag fetches no assets. */
export function observeUiVersion(available: () => void) {
  let etag: string | null = null, active = true, busy = false;
  const check = async () => {
    if (busy || !navigator.onLine) return;
    busy = true;
    try {
      const response = await fetch('/api/v1/ui/manifest', { headers: etag ? { 'If-None-Match': etag } : {}, credentials: 'omit', cache: 'no-store', signal: AbortSignal.timeout(10000) });
      if (active && response.headers.get('X-UI-Platform') && response.headers.get('X-UI-Platform') !== customization.platform_version) available();
      if (response.status === 304 || !response.ok) return;
      const { manifest } = await response.json();
      if (!active || manifest?.instance_id !== customization.instance_id) return;
      etag = response.headers.get('ETag');
      if (manifest.build_id !== customization.build_version || response.headers.get('X-UI-Platform') !== customization.platform_version) available();
    } catch { /* Keep the working shell when metadata is unavailable. */ }
    finally { busy = false; }
  };
  window.addEventListener('subscriptions-synchronized', check);
  void check();
  return () => { active = false; window.removeEventListener('subscriptions-synchronized', check); };
}

export async function registerShell() {
  if (customization.instance_id === 'development' || !('serviceWorker' in navigator)) return;
  try { await navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' }); } catch { /* Unsupported WebView: normal online UI remains usable. */ }
}
export async function updateShell() {
  if ('serviceWorker' in navigator) {
    const registration = await navigator.serviceWorker.getRegistration('/');
    if (registration) {
      await registration.update();
      if (registration.installing) await new Promise<void>(resolve => {
        const worker = registration.installing!;
        const timer = setTimeout(resolve, 10000);
        worker.addEventListener('statechange', () => { if (['installed', 'redundant'].includes(worker.state)) { clearTimeout(timer); resolve(); } });
      });
      if (registration.waiting) await new Promise<void>((resolve, reject) => {
        const done = () => { clearTimeout(timer); navigator.serviceWorker.removeEventListener('controllerchange', done); resolve(); };
        const timer = setTimeout(() => { navigator.serviceWorker.removeEventListener('controllerchange', done); reject(new Error('UI update timed out')); }, 10000);
        navigator.serviceWorker.addEventListener('controllerchange', done);
        registration.waiting!.postMessage('activate-ui');
      });
    }
  }
  location.reload();
}
