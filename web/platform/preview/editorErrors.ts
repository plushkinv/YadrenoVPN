import { text as _tr } from "../i18n/runtime";
import { ApiError } from '../api/client';
import { errorText } from '../i18n/app';

export type EditorAction = 'submit' | 'apply' | 'state' | 'cancel' | 'resume' | 'new-chat';
export function connectionError(value: unknown): boolean {
  return value instanceof ApiError && (['offline', 'network_unavailable'].includes(value.code) || value.details.reason === 'transport');
}

export function missingCandidate(value: unknown): boolean {
  return value instanceof ApiError && value.details.code === 'web_candidate_missing';
}

const messages: Record<EditorAction, string> = {
  get submit() { return _tr("editorErrors.1"); }, get state() { return _tr("editorErrors.2"); },
  get apply() { return _tr("editorErrors.3"); },
  get cancel() { return _tr("editorErrors.4"); }, get resume() { return _tr("editorErrors.5"); },
  get 'new-chat'() { return _tr("editorErrors.6"); },
};

export function editorError(value: unknown, action: EditorAction): string {
  const details = value instanceof ApiError ? value.details : {};
  let message = messages[action];
  if (value instanceof ApiError && value.status === 413) {
    message = _tr("editorErrors.7");
  } else if (details.reason === 'customizer_not_configured') {
    message += _tr("editorErrors.8");
  } else if (details.reason === 'ui_publication_changed' || details.reason === 'ui_source_changed') {
    message += _tr("editorErrors.9");
  } else if (action === 'apply' && details.reason === 'editor_candidate_unavailable') {
    message += _tr("editorErrors.10");
  } else if (value instanceof ApiError && ['authentication_required', 'reauthentication_required', 'access_denied', 'offline'].includes(value.code)) {
    message += '. ' + errorText(value);
  }
  // Older servers may return Hub prose in details.message. It is not an error classification.
  if (typeof details.diagnostic_id === 'string' && /^[a-f0-9]{16}$/.test(details.diagnostic_id)) {
    message += _tr("editorErrors.11") + details.diagnostic_id;
  }
  return message;
}

export function previewError(value: unknown): string {
  let message = _tr("editorErrors.12");
  if (value instanceof ApiError) {
    if (missingCandidate(value)) message = _tr("editorErrors.13");
    else if (value.details.code === 'web_candidate_expired')
      message = _tr("editorErrors.14");
    else if (value.details.code === 'web_source_changed')
      message = _tr("editorErrors.15");
    else if (['authentication_required', 'reauthentication_required', 'access_denied'].includes(value.code))
      message += ' ' + errorText(value);
    const id = value.details.diagnostic_id;
    if (typeof id === 'string' && /^[a-f0-9]{16}$/.test(id)) message += _tr("editorErrors.11") + id;
  }
  return message;
}

export function admissionUnknown(value: unknown): boolean {
  if (!(value instanceof ApiError)) return false;
  if (value.details.outcome_unknown === true || value.code === 'network_unavailable') return true;
  return value.code === 'invalid_response' && (value.status < 400 || value.status >= 500);
}
