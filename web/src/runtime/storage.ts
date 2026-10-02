/** Personal snapshots stay outside the shared shell cache. No secrets or request queue. */
let prefix = 'yadreno.account.development.';
let generationKey = 'yadreno.session.development';
export function configureStorage(instance: string) {
  prefix = 'yadreno.account.' + instance + '.';
  generationKey = 'yadreno.session.' + instance;
  try { Object.keys(sessionStorage).filter(key => key.startsWith('yadreno.account.') && !key.startsWith(prefix)).forEach(key => sessionStorage.removeItem(key)); } catch { /* Opaque preview origin. */ }
}
function sharedGeneration() { try { return localStorage.getItem(generationKey); } catch { return null; } }
export function syncGeneration() {
  const shared = sharedGeneration();
  if (shared && stored('generation', null) !== shared) forgetAccounts();
  if (shared) remember('generation', shared);
}
export function announceSessionChange() {
  const value = crypto.randomUUID();
  try { localStorage.setItem(generationKey, value); } catch { /* No shared storage. */ }
  remember('generation', value);
}
export function observeSessionChange(listener: () => void) {
  const changed = (event: StorageEvent) => { if (event.key === generationKey) { syncGeneration(); listener(); } };
  window.addEventListener('storage', changed);
  return () => window.removeEventListener('storage', changed);
}
export function logoutPending(value?: boolean): boolean {
  try {
    if (value !== undefined) { if (value) localStorage.setItem(generationKey + '.logout', '1'); else localStorage.removeItem(generationKey + '.logout'); }
    return localStorage.getItem(generationKey + '.logout') === '1';
  } catch { return false; }
}
export function stored<T>(key: string, fallback: T): T {
  try { const value = sessionStorage.getItem(prefix + key); return value ? JSON.parse(value) as T : fallback; } catch { return fallback; }
}
export function remember(key: string, value: unknown) {
  try { sessionStorage.setItem(prefix + key, JSON.stringify(value)); } catch { /* Private mode/opaque preview origin. */ }
}
export function forgetAccounts() {
  try { Object.keys(sessionStorage).filter(key => key.startsWith(prefix)).forEach(key => sessionStorage.removeItem(key)); } catch { /* No storage available. */ }
}
export function snapshot<T>(accountId: number, key: 'subscriptions', value?: T): { updated: number; data: T } | null {
  if (value !== undefined) remember(`${accountId}.${key}`, { updated: Date.now(), data: value });
  return stored(`${accountId}.${key}`, null);
}
