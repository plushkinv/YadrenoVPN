import type { UiSettings, ModuleAvailability } from '../api/contracts';
import type { Preset, Theme } from '../model';


export interface InstallationPreview {
  settings: UiSettings; captured_at: number; currency: string; features: Record<string, boolean>;
  modules?: ModuleAvailability[];
  tariffs: { id: number; name: string; group_id: number; duration_days: number; price_minor: number; traffic_limit_gb: number; max_ips: number }[];
  trial_offers: { offer_id: number; tariff_name: string | null; duration_days: number | null; traffic_limit_gb: number | null }[];
}
export const scenarios = {"active": "Активная подписка", "empty": "Пустые данные", "loading": "Загрузка", "error": "Ошибка", "pending": "Ожидание оплаты", "confirmed": "Провайдер подтвердил оплату", "issuing": "Оплачено, доступ готовится", "ready": "Доступ готов", "expired": "Срок истёк", "traffic": "Трафик исчерпан", "unknown": "Неизвестные счётчики", "unconfigured": "Нужна настройка", "disabled": "Доступ отключён"};
export interface PreviewContext {
  contract_version: 1; route: string; scenario: keyof typeof scenarios; preset: Preset; theme: Theme;
  ui_version: string; customization_version: string;
}
export interface PreviewMessage { type: 'yadreno.preview'; installation: InstallationPreview; context: PreviewContext; }
