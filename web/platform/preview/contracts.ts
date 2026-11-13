import { text as _tr } from "../i18n/runtime";
import type { UiSettings, ModuleAvailability } from '../api/contracts';
import type { Preset, Theme } from '../model';


export interface InstallationPreview {
  settings: UiSettings; captured_at: number; currency: string; features: Record<string, boolean>;
  modules?: ModuleAvailability[];
  tariffs: { id: number; name: string; group_id: number; duration_days: number; price_minor: number; traffic_limit_gb: number; max_ips: number }[];
  trial_offers: { offer_id: number; tariff_name: string | null; duration_days: number | null; traffic_limit_gb: number | null }[];
}
export const scenarios = {get "active"() { return _tr("contracts.1"); }, get "empty"() { return _tr("contracts.2"); }, get "loading"() { return _tr("contracts.3"); }, get "error"() { return _tr("contracts.4"); }, get "pending"() { return _tr("contracts.5"); }, get "confirmed"() { return _tr("contracts.6"); }, get "issuing"() { return _tr("app.issuing"); }, get "ready"() { return _tr("app.ready"); }, get "expired"() { return _tr("contracts.7"); }, get "traffic"() { return _tr("contracts.8"); }, get "unknown"() { return _tr("contracts.9"); }, get "unconfigured"() { return _tr("contracts.10"); }, get "disabled"() { return _tr("app.disabled"); }};
export interface PreviewContext {
  contract_version: 1; route: string; scenario: keyof typeof scenarios; preset: Preset; theme: Theme;
  ui_version: string; customization_version: string;
}
export interface PreviewMessage { type: 'yadreno.preview'; installation: InstallationPreview; context: PreviewContext; }
