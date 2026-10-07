import { ApiError } from '../api/client';
import { errorText } from '../i18n/app';

export type EditorAction = 'submit' | 'state' | 'cancel' | 'resume' | 'new-chat';
const messages: Record<EditorAction, string> = {
  submit: 'Ошибка при отправке запроса', state: 'Ошибка при обновлении состояния запроса',
  cancel: 'Ошибка при отмене запроса', resume: 'Ошибка при возобновлении запроса',
  'new-chat': 'Ошибка при создании нового чата',
};

export function editorError(value: unknown, action: EditorAction): string {
  const details = value instanceof ApiError ? value.details : {};
  let message = messages[action];
  if (value instanceof ApiError && value.status === 413) {
    message = 'Не удалось загрузить вложение: сервер отклонил размер запроса.';
  } else if (details.reason === 'customizer_not_configured') {
    message += '. Подключите кастомизатор в настройках Yadreno Admin.';
  } else if (details.reason === 'ui_publication_changed' || details.reason === 'ui_source_changed') {
    message += '. Интерфейс изменился после открытия. Откройте актуальную версию и повторите запрос.';
  } else if (value instanceof ApiError && ['authentication_required', 'reauthentication_required', 'access_denied', 'offline'].includes(value.code)) {
    message += '. ' + errorText(value);
  }
  // Older servers may return Hub prose in details.message. It is not an error classification.
  if (typeof details.diagnostic_id === 'string' && /^[a-f0-9]{16}$/.test(details.diagnostic_id)) {
    message += '\nКод ошибки: ' + details.diagnostic_id;
  }
  return message;
}

export function admissionUnknown(value: unknown): boolean {
  if (!(value instanceof ApiError)) return false;
  if (value.details.outcome_unknown === true || value.code === 'network_unavailable') return true;
  return value.code === 'invalid_response' && (value.status < 400 || value.status >= 500);
}
