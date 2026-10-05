import { useCallback, useEffect, useRef, useState } from 'react';

interface Entry { route: string; trail: string[]; }
const stateKey = 'yadrenoNavigation';

export function readRoute() {
  const hash = location.hash.slice(1);
  const order = /^\/orders\/([A-Za-z0-9_.:-]+)\/?$/.exec(location.pathname);
  if (!hash && order) return 'payment/' + order[1];
  return hash && !hash.includes('tgWebApp') && hash !== 'main-content' ? hash : 'home';
}

/** Used only for an entry without an in-app predecessor. */
export function parentRoute(route: string) {
  const [page, ...parts] = route.split('/');
  const param = parts.join('/');
  if (['connect', 'renewal', 'devices', 'hosts', 'history'].includes(page) && param) return 'subscription/' + param;
  if (['subscription', 'purchase', 'operation', 'trials'].includes(page)) return 'subscriptions';
  if (page === 'payment') return 'payments';
  if (['credentials', 'telegram-link'].includes(page)) return 'account';
  if (['account', 'payments', 'balance', 'promotion', 'referrals', 'import', 'help'].includes(page)) return 'more';
  if (['register', 'reset'].includes(page)) return 'login';
  return 'home';
}

export function navigationSection(route: string) {
  const page = route.split('/')[0];
  if (['connect', 'trials', 'purchase', 'renewal', 'subscription', 'devices', 'hosts', 'history', 'operation'].includes(page)) return 'subscriptions';
  if (['account', 'help', 'payment', 'payments', 'balance', 'promotion', 'referrals', 'import', 'credentials', 'telegram-link'].includes(page)) return 'more';
  return page;
}

function savedEntry(): Entry | undefined {
  const value = history.state?.[stateKey];
  return value?.route === readRoute() && Array.isArray(value.trail) && value.trail.every((route: unknown) => typeof route === 'string') ? value : undefined;
}

/** Browser history owns normal navigation; an opaque demo never traverses its parent. */
export function useNavigation(preview: boolean) {
  const current = useRef<Entry>(savedEntry() ?? { route: readRoute(), trail: [] });
  const [route, setRoute] = useState(current.current.route);
  const commit = useCallback((entry: Entry) => { current.current = entry; setRoute(entry.route); }, []);
  const write = useCallback((entry: Entry, replace = false) => {
    const oldURL = location.href;
    history[replace || preview ? 'replaceState' : 'pushState']({ ...history.state, [stateKey]: entry }, '', '#' + entry.route);
    commit(entry);
    // Preview context and source-owned hash listeners retain their existing contract.
    window.dispatchEvent(new HashChangeEvent('hashchange', { oldURL, newURL: location.href }));
    window.scrollTo({ top: 0 });
  }, [commit, preview]);
  const navigate = useCallback((next: string) => {
    if (next !== current.current.route) write({ route: next, trail: [...current.current.trail, current.current.route] });
  }, [write]);
  const reset = useCallback((next?: string) => {
    const entry = { route: next ?? current.current.route, trail: [] };
    if (next === undefined) {
      // Preserve Telegram's launch fragment and the browser's reload position.
      history.replaceState({ ...history.state, [stateKey]: entry }, ''); commit(entry);
    } else write(entry, true);
  }, [commit, write]);
  const back = useCallback(() => {
    const { route, trail } = current.current;
    if (trail.length && !preview) history.back();
    else if (trail.length) write({ route: trail[trail.length - 1], trail: trail.slice(0, -1) }, true);
    else reset(parentRoute(route));
  }, [preview, reset, write]);
  useEffect(() => {
    history.replaceState({ ...history.state, [stateKey]: current.current }, '');
    const changed = () => {
      if (location.hash === '#main-content') return;
      const saved = savedEntry(), next = readRoute();
      if (saved) commit(saved);
      else if (next !== current.current.route) {
        const entry = { route: next, trail: [...current.current.trail, current.current.route] };
        history.replaceState({ ...history.state, [stateKey]: entry }, '');
        commit(entry);
      }
    };
    window.addEventListener('popstate', changed); window.addEventListener('hashchange', changed);
    return () => { window.removeEventListener('popstate', changed); window.removeEventListener('hashchange', changed); };
  }, [commit]);
  const origin = route.startsWith('payment/') ? current.current.trail.at(-1) : undefined;
  return { route, navigate, back, reset, section: navigationSection(origin ?? route) };
}
