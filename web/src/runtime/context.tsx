import { createContext, useContext, useEffect, useRef, useState } from 'react';
import type { Api } from '../api/client';
import type { Bootstrap, Session, UiSettings } from '../api/contracts';
import type { Preset, Theme } from '../model';
import type { Environment } from './environment';

export interface AppContextValue {
  api: Api; environment: Environment; session: Session | null; bootstrap: Bootstrap; settings: UiSettings;
  navigate: (route: string) => void; route: string; param: string; revision: number;
  refresh: () => void; authenticated: (session: Session) => void; logout: () => Promise<void>;
  preset: Preset; theme: Theme; setTheme: (theme: Theme) => void;
  preview: boolean;
}
export const AppContext = createContext<AppContextValue | null>(null);
export function useApp(): AppContextValue {
  const value = useContext(AppContext);
  if (!value) throw new Error('Application context is missing');
  return value;
}

/** Discard late responses after navigation/account switch; never replay a mutation. */
export function useResource<T>(path: string | null) {
  const { api, revision, session } = useApp();
  const [state, setState] = useState<{ data?: T; error?: unknown; loading: boolean }>({ loading: true });
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setState({ loading: Boolean(path) });
    if (path) api.request<T>(path).then(data => { if (active) setState({ data, loading: false }); },
      error => { if (active) setState({ error, loading: false }); });
    return () => { active = false; };
  }, [path, api, revision, session?.account_id, retry]);
  return { ...state, retry: () => setRetry(value => value + 1) };
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
