import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, type Api } from '../api/client';
import type { Bootstrap, Session, UiSettings } from '../api/contracts';
import type { Preset, Theme } from '../model';
import type { Environment } from './environment';
import { parentRoute } from './navigation';
import type { ApplicationStorage, PresentationStatus } from './contract';

export interface AppContextValue {
  api: Api; environment: Environment; session: Session | null; bootstrap: Bootstrap; settings: UiSettings;
  navigate: (route: string) => void; route: string; param: string; revision: number;
  refresh: () => void; authenticated: (session: Session) => void; logout: () => Promise<void>;
  loginWithTelegram: () => Promise<void>;
  preset: Preset; theme: Theme; setTheme: (theme: Theme) => void;
  preview: boolean;
  section: string; back: () => void;
  storage: ApplicationStorage; launch: Readonly<Record<string, string>>;
  status: PresentationStatus; update: () => Promise<void>; reload: () => void;
}
export const AppContext = createContext<AppContextValue | null>(null);
export const BackContext = createContext<(() => void) | null>(null);
export function useApp(): AppContextValue {
  const value = useContext(AppContext);
  if (!value) throw new Error('Application context is missing');
  return value;
}

/** Stock and custom pages share return behavior. */
export function useBack() {
  const back = useContext(BackContext);
  const { navigate, route, param } = useApp();
  return back ?? (() => navigate(parentRoute(route + (param ? '/' + param : ''))));
}

/** Discard late responses after navigation/account switch; never replay a mutation. */
export function useResource<T>(path: string | null) {
  const { api, revision, session } = useApp();
  const resource = useMemo(() => ({ api, path, account: session?.account_id }), [api, path, session?.account_id]);
  const [state, setState] = useState<{ resource: typeof resource; data?: T; error?: unknown; loading: boolean }>({ resource, loading: Boolean(path) });
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setState(previous => {
      const data = previous.resource === resource ? previous.data : undefined;
      return { resource, data, loading: Boolean(resource.path) && data === undefined };
    });
    if (resource.path) resource.api.request<T>(resource.path).then(data => {
      if (active) setState({ resource, data, loading: false });
    }, error => {
      if (!active) return;
      const temporary = error instanceof ApiError && (['offline', 'network_unavailable'].includes(error.code) || error.status >= 500);
      setState(previous => ({ resource, error, loading: false,
        data: temporary && previous.resource === resource ? previous.data : undefined }));
    });
    return () => { active = false; };
  }, [resource, revision, retry]);
  // Hide the previous owner/path immediately, before the next effect starts.
  const current = state.resource === resource ? state : { loading: Boolean(path), data: undefined, error: undefined };
  return { data: current.data, error: current.error, loading: current.loading, retry: () => setRetry(value => value + 1) };
}

export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const running = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function run<T>(action: () => Promise<T>): Promise<T | undefined> {
    if (running.current) return;
    running.current = true;
    setBusy(true); setError(undefined);
    try { return await action(); }
    catch (failure) { if (mounted.current) setError(failure); return undefined; }
    finally { running.current = false; if (mounted.current) setBusy(false); }
  }
  return { busy, error, run, clear: () => setError(undefined) };
}
