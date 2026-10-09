import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { AppContext, useApp } from './runtime/context';
import { RuntimeStore, type RuntimeState } from './runtime/contract';
import { dispatch } from './runtime/dispatch';
import { serveRuntime } from './runtime/channel';
import { loadApplication } from './loader';
import { customization } from './config';
import { Failure } from './components/Forms';
import { AdminPreview } from './preview/AdminPreview';
import type { InstallationPreview } from './preview/contracts';
import type { UiSettings } from './api/contracts';

export function Surface({ configuration, onSettingsSaved }: { configuration?: InstallationPreview; onSettingsSaved: (value: UiSettings) => void }) {
  const value = useApp();
  const current = useRef(value); current.current = value;
  const [failure, setFailure] = useState<unknown>(), [attempt, retry] = useState(0);
  const element = useRef<HTMLDivElement>(null), frame = useRef<HTMLIFrameElement>(null);
  const state: RuntimeState = { session: value.session && { account_id: value.session.account_id, telegram_id: value.session.telegram_id, source: value.session.source, expires_at: value.session.expires_at },
    bootstrap: value.bootstrap, settings: value.settings, route: value.route, param: value.param, revision: value.revision,
    preset: value.preset, theme: value.theme, preview: false, section: value.section, launch: value.launch, status: value.status,
    storage: value.storage.get('__snapshot__', {}), environment: { kind: value.environment.kind, initialTheme: value.environment.initialTheme },
    insets: Object.fromEntries(Array.from(document.documentElement.style).filter(key => key.startsWith('--tg-')).map(key => [key, document.documentElement.style.getPropertyValue(key)])) };
  const runtime = useRef<RuntimeStore>(null);
  if (!runtime.current) runtime.current = new RuntimeStore(state, (method, args) => method === 'reload' ? Promise.resolve(retry(count => count + 1)) : dispatch(current.current, method, args), value.environment.native);
  useLayoutEffect(() => { runtime.current!.update(state); });
  const isolated = Boolean(configuration);
  useEffect(() => {
    let active = true, cleanup: (() => void) | undefined;
    const controller = new AbortController();
    setFailure(undefined);
    if (!isolated) {
      void loadApplication(element.current!, customization, runtime.current!, controller.signal).then(stop => { if (active) cleanup = stop; else stop(); }, error => { if (active) setFailure(error); });
      return () => { active = false; controller.abort(); cleanup?.(); };
    }
    const timeout = setTimeout(() => setFailure(new Error('Application frame unavailable')), 20000);
    const receive = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.origin !== 'null' || event.data?.type !== 'yadreno.application.ready') return;
      clearTimeout(timeout); cleanup?.();
      const channel = new MessageChannel();
      cleanup = serveRuntime(channel.port1, runtime.current!, () => setFailure(new Error('Application failed')));
      frame.current.contentWindow!.postMessage({ type: 'yadreno.application.connect', state: runtime.current!.getSnapshot() }, '*', [channel.port2]);
    };
    window.addEventListener('message', receive);
    return () => { active = false; clearTimeout(timeout); cleanup?.(); window.removeEventListener('message', receive); };
  }, [isolated, attempt]);
  return <div className={isolated ? 'system-frame-shell system-ui' : undefined} data-preset={value.preset} data-theme={value.theme}>
    <div className="system-application">
      <div className="system-ui"><Failure error={failure} retry={() => retry(count => count + 1)} /></div>
      {isolated ? <iframe key={attempt} ref={frame} src={customization.frame_url} sandbox="allow-scripts" title="Личный кабинет" referrerPolicy="no-referrer" /> : <div ref={element} />}
    </div>
    {configuration && <AppContext.Provider value={value}><div className="admin-review-slot"><AdminPreview configuration={configuration} onSettingsSaved={onSettingsSaved} /></div></AppContext.Provider>}
  </div>;
}
