/** Public v1 read models. Commercial decisions are supplied by the server. */
export interface AuthSettings {
  phone_format: string; verification_available: boolean; verification_required: boolean;
  verification_method: 'ucaller' | 'smsaero_mobile' | 'smsaero_sms' | null;
  password_recovery_available: boolean; unverified_phone_warning_required: boolean;
  telegram_login_available?: boolean;
}
export interface PhoneVerification {
  challenge_id: string; method: 'ucaller' | 'smsaero_mobile' | 'smsaero_sms';
  state: 'code_required' | 'waiting' | 'confirmed' | 'failed' | 'unknown' | 'verified' | 'expired';
  send_state: 'sent' | 'failed' | 'unknown'; expires_at: number; resend_at: number;
  code_length: number | null; proof: string | null;
}
export interface ModuleAvailability { module_id: string; version: string; api_version: number; state: string; }
export interface Bootstrap { api_version: number; auth: AuthSettings; features: Record<string, boolean>; modules?: ModuleAvailability[]; }
export interface Session { account_id: number; telegram_id: number | null; source: string; expires_at: number; }
export interface Profile {
  account_id: number; telegram_id: number | null; username: string | null;
  first_name: string | null; last_name: string | null; created_at: string | null;
  credentials: { present: boolean; phone: string | null; phone_verified: boolean };
}
export interface Method { id: string; presentation: string; }
export interface Tariff {
  id: number; name: string; group_id: number; duration_days: number; traffic_limit_gb: number;
  max_ips: number; base_currency: string; nominal_amount_minor: number; payable_amount_minor: number;
  payment_methods: Method[]; available: boolean; reason: string | null;
}
export interface Catalog { purpose: string; subscription_id: number | null; tariffs: Tariff[]; groups: { id: number; name: string }[]; }
export interface Subscription {
  id: number; name: string | null; tariff_id: number; tariff_name: string | null;
  server_id: number | null; server_name: string | null; expires_at: string | null; created_at: string | null;
  state: string; access_status: string;
  traffic: { used_bytes: number | null; limit_bytes: number | null; known: boolean; updated_at: string | null; source: string };
  devices_available: boolean; actions: Record<string, { allowed: boolean; reason: string | null }>;
  pending_operations: { id: string; kind: string; created_at: number }[];
  servers: { id: number; name: string }[];
}
export interface Page<T> { items: T[]; limit: number; offset: number; }
export interface SubscriptionImportResult {
  state: 'select_group' | 'pending' | 'completed'; key_ids: number[]; groups: { id: number; name: string }[];
}
export interface TrialOffer {
  eligible: boolean; reason: string | null; scope: string;
  offer: { offer_id: number; tariff_name: string | null; duration_days: number | null; traffic_limit_gb: number | null } | null;
}
export interface Quote {
  quote_id: string; expires_at: number; version: number; purpose: string; tariff_id: number | null;
  key_id: number | null; payment_type: string; base_currency: string; nominal_amount_minor: number;
  payable_amount_minor: number; charge_minor: number; charge_currency: string; balance_deduct_minor: number;
  duration_days: number | null; traffic_limit_gb: number | null; max_ips: number | null;
}
export interface Order {
  order_id: string; operation_id?: string | null; purpose: string; status: string; fulfillment_status: string;
  provider_confirmed: boolean; provider_status?: string | null; payment_type?: string | null;
  base_currency: string; nominal_amount_minor: number; payable_amount_minor: number; balance_deduct_minor: number;
  charge_amount?: string | null; charge_currency?: string | null; subscription_id?: number | null;
  access_status: string | null; payment_url?: string | null; presentation?: string;
}
export interface Composition { ok: boolean; status: string; error_code?: string | null; }
export interface Operation {
  operation_id: string; key_id?: number; state?: string; ok?: boolean; reason?: string;
  composition?: Composition | null;
  result?: { state?: string; error?: string; composition?: Composition } | null;
}
export interface UiSettings {
  title: string; logo: string | null; preset: 'clear' | 'signal' | 'friendly'; theme: 'light' | 'dark';
  sync_interval_seconds: number;
}
