import type { AppContextValue } from './context';
import type { NativeBridge } from './environment';

export interface PresentationStatus {
  offline: boolean; cachedAt?: number; newUi: boolean; updating: boolean;
}
export interface ApplicationStorage {
  get<T>(key: string, fallback: T): T;
  set(key: string, value: unknown): void;
}
export type RuntimeMethod = 'api' | 'navigate' | 'back' | 'refresh' | 'authenticated' | 'logout' | 'setTheme'
  | 'storage.set' | 'copy' | 'share' | 'openLink' | 'importSubscription' | 'payment' | 'update' | 'reload' | 'loginWithTelegram';
export type RuntimeState = Pick<AppContextValue, 'session' | 'bootstrap' | 'settings' | 'route' | 'param' | 'revision' | 'preset' | 'theme' | 'preview' | 'section' | 'launch' | 'status'> & {
  environment: { kind: AppContextValue['environment']['kind']; initialTheme?: 'light' | 'dark' };
  storage: Record<string, unknown>; insets: Record<string, string>;
};
export interface ApplicationRuntime {
  getSnapshot(): RuntimeState;
  subscribe(listener: () => void): () => void;
  call(method: RuntimeMethod, args: unknown[]): Promise<unknown>;
  native?: NativeBridge;
}

/** One store for direct rendering, the frame transport and synthetic fixtures. */
export class RuntimeStore implements ApplicationRuntime {
  private listeners = new Set<() => void>();
  constructor(private state: RuntimeState, public call: ApplicationRuntime['call'], public native?: NativeBridge) {}
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  update(state: RuntimeState) { this.state = state; this.listeners.forEach(listener => listener()); }
}
