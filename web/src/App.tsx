import { useCallback, useEffect, useMemo, useState } from 'react';
import { ApiError, HttpApi, type Api } from './api/client';
import type { Bootstrap, Session, UiSettings } from './api/contracts';
import { Shell } from './components/Shell';
import { Button } from './components/Ui';
import { Failure, Loading } from './components/Forms';
import { AppContext, BackContext, type AppContextValue } from './runtime/context';
import { useNavigation } from './runtime/navigation';
import type { Environment } from './runtime/environment';
import { AccountApi } from './runtime/cache';
import { registeredPages, customization } from './runtime/registry';
import { announceSessionChange, configureStorage, forgetAccounts, logoutPending, observeSessionChange, remember, stored, syncGeneration } from './runtime/storage';
import { observeUiVersion, registerShell, updateShell } from './runtime/versions';
import { appText as t } from './i18n/app';
import type { Preset, Theme } from './model';
import { AdminPreview } from './preview/AdminPreview';
import { NativeConnection } from './components/NativeConnection';
import { ErrorBoundary } from './runtime/ErrorBoundary';

export interface ApplicationProps { environment: Environment; transport?: Api; preview?: boolean; }
export function App({ environment, transport, preview = false }: ApplicationProps) {
  return <ErrorBoundary><AppRuntime environment={environment} transport={transport} preview={preview} /></ErrorBoundary>;
}
function AppRuntime({ environment, transport, preview = false }: ApplicationProps) {
  useMemo(() => { configureStorage(customization.instance_id); syncGeneration(); }, []);
  const api = useMemo(() => new AccountApi(transport ?? new HttpApi()), [transport]);
  const { route, navigate, back, reset, section } = useNavigation(preview);
  const [session, setSession] = useState<Session | null>(null);
  const [bootstrap, setBootstrap] = useState<Bootstrap>();
  const [settings, setSettings] = useState<UiSettings>();
  const [error, setError] = useState<unknown>();
  const [loading, setLoading] = useState(true);
  const [loggingOut, setLoggingOut] = useState(false);
  const [revision, setRevision] = useState(0);
  const [preset, setPreset] = useState<Preset>('clear');
  const [theme, setTheme] = useState<Theme>('light');
  const [offline, setOffline] = useState(!navigator.onLine);
  const [cachedAt, setCachedAt] = useState<number>();
  const [attempt, setAttempt] = useState(0);
  const [newUi, setNewUi] = useState(false);
  const [updateError, setUpdateError] = useState<unknown>();
  const [updating, setUpdating] = useState(false);
  const refresh = useCallback(() => setRevision(value => value + 1), []);
  const [path, ...parts] = route.split('/');
  const param = parts.join('/');
  const bindSession = useCallback((value: Session | null) => {
    const previous = stored<number | null>('owner', null);
    if (previous !== value?.account_id) { forgetAccounts(); syncGeneration(); reset(); }
    api.setAccount(value?.account_id ?? null); setSession(value);
    if (value) { remember('owner', value.account_id); remember('offline-session', { account_id: value.account_id, expires_at: value.expires_at, telegram_id: null, source: 'offline' }); }
  }, [api, reset]);
  useEffect(() => {
    let active = true;
    setLoading(true); setError(undefined);
    (async () => {
      let identity: Session | null = null;
      let launch: { digest: string; account_id: number } | undefined;
      const localLogout = !preview && logoutPending();
      if (localLogout && navigator.onLine) {
        try { await api.request('/auth/logout', 'POST'); logoutPending(false); }
        catch (failure) { if (failure instanceof ApiError && failure.status === 401) logoutPending(false); else throw failure; }
      }
      const initial = await api.request<Bootstrap>('/bootstrap');
      const presentation = await api.request<UiSettings>('/ui/settings');
      if (!localLogout) {
        // A valid Mini App session survives a long payment return even after
        // the original launch initData expires. Site sessions never authorize admin preview.
        try { identity = await api.request<Session>('/auth/session'); } catch (failure) { if (!(failure instanceof ApiError && failure.status === 401)) throw failure; }
        if (environment.initData) {
          const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(environment.initData));
          const digest = Array.from(new Uint8Array(hash), value => value.toString(16).padStart(2, '0')).join('');
          const previous = stored<{ digest: string; account_id: number } | null>('miniapp-launch', null);
          if (identity?.source !== 'mini_app' || previous?.digest !== digest || previous?.account_id !== identity.account_id)
            identity = await api.request<Session>('/auth/telegram', 'POST', { init_data: environment.initData });
          launch = { digest, account_id: identity.account_id };
        }
      }
      if (!active) return;
      bindSession(identity); setBootstrap(initial); setSettings(presentation);
      if (launch) remember('miniapp-launch', launch);
      const appearance = stored<{ theme: Theme } | null>(`${identity?.account_id}.appearance`, null);
      setPreset(presentation.preset); setTheme(appearance?.theme ?? environment.initialTheme ?? presentation.theme);
      remember('bootstrap', initial); remember('ui-settings', presentation);
      if (identity) {
        const personal = await api.request<Bootstrap>('/bootstrap');
        if (active) { setBootstrap(personal); remember('bootstrap', personal); }
      }
    })().catch(failure => {
      if (!active) return;
      if (!navigator.onLine || failure instanceof ApiError && (failure.code === 'network_unavailable' || failure.status >= 500)) {
        const savedBootstrap = stored<Bootstrap | null>('bootstrap', null), savedSettings = stored<UiSettings | null>('ui-settings', null);
        const identity = stored<Session | null>('offline-session', null);
        if (savedBootstrap && savedSettings && identity && identity.expires_at * 1000 > Date.now() && !logoutPending()) {
          bindSession(identity); setBootstrap(savedBootstrap); setSettings(savedSettings); setPreset(savedSettings.preset); setTheme(savedSettings.theme); return;
        }
      }
      setError(failure);
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [api, environment, attempt, bindSession]);
  useEffect(() => {
    const network = () => { setOffline(!navigator.onLine); if (navigator.onLine) { setCachedAt(undefined); setAttempt(value => value + 1); } };
    const expired = () => { forgetAccounts(); api.setAccount(null); setSession(null); setCachedAt(undefined); };
    const accountChanged = () => { expired(); if (navigator.onLine) setAttempt(value => value + 1); };
    const cached = (event: Event) => setCachedAt((event as CustomEvent<number>).detail);
    window.addEventListener('online', network); window.addEventListener('offline', network);
    window.addEventListener('account-expired', expired); window.addEventListener('account-cache-used', cached);
    window.addEventListener('account-changed', accountChanged);
    const stop = preview ? () => {} : observeSessionChange(accountChanged);
    return () => { stop(); window.removeEventListener('account-changed', accountChanged); window.removeEventListener('online', network); window.removeEventListener('offline', network); window.removeEventListener('account-expired', expired); window.removeEventListener('account-cache-used', cached); };
  }, [api, refresh]);
  useEffect(() => environment.back(path !== 'home' && path !== 'login', back), [environment, path, back]);
  useEffect(() => environment.observe?.(nextTheme => {
    if (nextTheme && !stored(`${session?.account_id}.appearance`, null)) setTheme(nextTheme);
  }), [environment, session?.account_id]);
  useEffect(() => { document.documentElement.style.colorScheme = theme; }, [theme]);
  useEffect(() => {
    if (preview) return;
    void registerShell();
    return observeUiVersion(() => setNewUi(true));
  }, [preview]);
  useEffect(() => {
    if (!session || !settings || preview) return;
    let active = true, pending = false;
    const update = async () => {
      if (!navigator.onLine || document.visibilityState !== 'visible' || pending) return;
      pending = true;
      refresh();
      try {
        const value = await api.request<UiSettings>('/ui/settings');
        if (active) { setSettings(value); setPreset(value.preset); remember('ui-settings', value); }
      } catch { /* Keep the last confirmed presentation during a network failure. */ }
      finally { pending = false; }
    };
    const timer = setInterval(update, settings.sync_interval_seconds * 1000);
    document.addEventListener('visibilitychange', update);
    return () => { active = false; clearInterval(timer); document.removeEventListener('visibilitychange', update); };
  }, [api, session?.account_id, settings?.sync_interval_seconds, preview, refresh]);
  if (loading || loggingOut) return <div className="app" data-preset={preset} data-theme={theme}><NativeConnection bridge={environment.native} /><Loading /></div>;
  if (error || !bootstrap || !settings) return <div className="app" data-preset={preset} data-theme={theme}><NativeConnection bridge={environment.native} /><Failure error={error} retry={() => setAttempt(value => value + 1)} /></div>;
  const definition = registeredPages.find(page => page.id === path);
  const selected = definition && (definition.public || session) ? definition : registeredPages.find(page => page.id === 'login')!;
  const Component = selected.component;
  const value: AppContextValue = { api, environment, session, bootstrap, settings, route: selected.id, param, revision, refresh,
    navigate, preset, theme, preview,
    setTheme: nextTheme => {
      setTheme(nextTheme); remember(`${session?.account_id}.appearance`, { theme: nextTheme });
      if (preview) window.parent.postMessage({ type: 'yadreno.preview.appearance', preset, theme: nextTheme }, '*');
    },
    authenticated: identity => {
      logoutPending(false); bindSession(identity); remember('ui-settings', settings);
      if (!preview) announceSessionChange(); refresh(); reset('home');
      api.request<Bootstrap>('/bootstrap').then(result => { setBootstrap(result); remember('bootstrap', result); }, setError);
    },
    logout: async () => {
      setLoggingOut(true);
      if (!preview) logoutPending(true);
      try { await api.request('/auth/logout', 'POST'); logoutPending(false); }
      finally { forgetAccounts(); bindSession(null); if (!preview) announceSessionChange(); setCachedAt(undefined); reset('login'); setLoggingOut(false); }
    },
  };
  return <BackContext.Provider value={back}><AppContext.Provider value={value}><Shell route={selected.id} activeRoute={section} navigate={navigate} preset={preset} theme={theme}
    title={settings.title} logo={settings.logo?.startsWith('/ui/assets/') ? customization.asset_base + settings.logo.slice(4) : settings.logo} accountLabel={session ? `${t.account} ${session.account_id}` : t.login} account={() => navigate('account')}
    help={() => navigate('help')} toggleTheme={() => value.setTheme(theme === 'light' ? 'dark' : 'light')}
    review={preview ? null : <AdminPreview onSettingsSaved={value => { setSettings(value); setPreset(value.preset); remember('ui-settings', value); }} />}>
    {(offline || cachedAt) && <p className="notice" role="status">{t.offline}{cachedAt && ` ${t.updated}: ${new Date(cachedAt).toLocaleString('ru')}`}</p>}
    {newUi && <p className="notice" role="status">Доступна новая версия интерфейса. <Button tone="quiet" disabled={updating} onClick={() => { setUpdating(true); setUpdateError(undefined); void updateShell().catch(setUpdateError).finally(() => setUpdating(false)); }}>Обновить интерфейс</Button></p>}
    <Failure error={updateError} />
    {environment.native && !['home', 'connect'].includes(selected.id) && <NativeConnection bridge={environment.native} />}
    {!definition ? <h1>{t.notFound}</h1> : definition.feature && !bootstrap.features[definition.feature] ? <p>{t.empty}</p>
      : definition.module_id && !bootstrap.modules?.some(module => module.module_id === definition.module_id && module.state === 'available' && module.api_version === 1)
      ? <p role="alert">Модуль этой страницы недоступен. Повторите позже.</p> : <Component key={`${selected.id}/${param}/${session?.account_id}`} />}
    {!session && selected.id !== 'login' && <Button onClick={() => navigate('login')}>{t.login}</Button>}
  </Shell></AppContext.Provider></BackContext.Provider>;
}
