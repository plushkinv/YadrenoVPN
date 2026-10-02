/** Presentation values supplied by an adapter, never a second business model. */
export type Preset = 'clear' | 'signal' | 'friendly';
export type Theme = 'light' | 'dark';
export type Route = string;
export type AccessState = 'active' | 'trial' | 'expiring' | 'expired' | 'traffic' | 'devices' | 'pending' | 'unconfigured' | 'disabled' | 'unknown';
export type HomeState = AccessState | 'new' | 'loading' | 'offline' | 'unavailable';
export type PaymentState = 'pending' | 'issuing' | 'ready' | 'refused';
export interface SubscriptionView {
  id: string;
  name: string;
  plan: string;
  state: AccessState;
  expires: string;
  remaining: string;
  traffic: string | null;
  trafficPercent: number | null;
  devices: string | null;
}
export interface ClientView { id: string; name: string; platforms: string; }
export interface QuoteView { id: string; title: string; price: string; caption: string; }
export interface OrderView {
  id: string;
  purpose: 'purchase' | 'renewal';
  subscriptionName: string;
  quote: QuoteView;
  method: string;
  state: PaymentState;
}
