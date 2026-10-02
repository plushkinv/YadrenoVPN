import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { errorText } from '../i18n/app';
import { useApp } from '../runtime/context';
import type { PreviewContext } from './contracts';
import { recordVoice } from './voice';

interface EditorState {
  task: { task_id: string; viewed: PreviewContext } | null;
  latest: { request_id: number | string; event: string; final: { content: string; content_html?: string } | null;
    progress: { content: string; cancel_button_text: string | null } | null;
    resume_allowed: boolean; cancel_button_text: string | null } | null;
  local_polling: boolean;
}
export interface CandidatePreview {
  task_id: string; candidate: { build_id: string; customization_version: string };
  viewed: PreviewContext; preview_url: string; published: boolean;
  pages: { id: string; title: string | null }[];
}
const prefix = '/admin/ui/editor';
const maxFiles = 5, maxFileBytes = 10 * 1024 * 1024;

export function useAdminEditor({ viewed, onCandidate, polling, visible }: {
  viewed: PreviewContext; onCandidate: (value: CandidatePreview) => void;
  polling: boolean; visible: boolean;
}) {
  const { api } = useApp();
  const [state, setState] = useState<EditorState>();
  const [message, setMessage] = useState('');
  const [voice, setVoice] = useState<File>();
  const [files, setFiles] = useState<File[]>([]);
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const stop = useRef<Awaited<ReturnType<typeof recordVoice>> | undefined>(undefined);
  const alive = useRef(true), checking = useRef(false), candidateRequest = useRef<number | string | undefined>(undefined);
  const generation = useRef(0);
  const visibleRef = useRef(visible); visibleRef.current = visible;
  const candidateCallback = useRef(onCandidate); candidateCallback.current = onCandidate;
  const reportError = (value: unknown) => {
    if (!alive.current) return;
    const detail = value instanceof ApiError ? value.details : {};
    setError(typeof detail.message === 'string' ? detail.message
      : value instanceof ApiError && value.status === 413 ? 'Файл больше 10 МиБ или превышен общий размер вложений.'
      : detail.reason === 'customizer_not_configured' ? 'Подключите кастомизатор в настройках Yadreno Admin.'
      : detail.reason === 'ui_publication_changed' || detail.reason === 'ui_source_changed'
        ? 'Интерфейс изменился после открытия. Откройте актуальную версию и повторите запрос.' : errorText(value));
  };
  const refresh = async () => {
    if (checking.current) return;
    checking.current = true;
    const started = generation.current;
    try {
      const value = await api.request<EditorState>(prefix);
      if (!alive.current || started !== generation.current) return;
      setState(value); setError('');
      if (value.latest?.final) setNotice('');
      if (value.latest?.final && candidateRequest.current !== value.latest.request_id) {
        setReady(false);
        try {
          const candidate = await api.request<CandidatePreview>(prefix + '/preview', 'POST');
          if (alive.current && started === generation.current) { candidateCallback.current(candidate); candidateRequest.current = value.latest.request_id; }
        } catch (reason) {
          if (reason instanceof ApiError && [404, 409, 422].includes(reason.status)) candidateRequest.current = value.latest.request_id;
          else throw reason;
        }
      }
      if (alive.current && started === generation.current) setReady(true);
    } catch (reason) { reportError(reason); }
    finally { checking.current = false; }
  };
  useEffect(() => {
    alive.current = true; setReady(false); void refresh();
    return () => { alive.current = false; stop.current?.(false); stop.current = undefined; };
  }, [api]);
  useEffect(() => {
    if (!polling) return;
    setReady(false);
    void refresh();
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void refresh(); }, 4000);
    return () => clearInterval(timer);
  }, [polling, api]);
  useEffect(() => { if (!visible && stop.current) finishVoice(); }, [visible]);
  async function submit() {
    if (!ready || busy || active || recording || (!message.trim() && !voice && !files.length)) return;
    setBusy(true); setError(''); setNotice('');
    generation.current++;
    try {
      if (voice || files.length) {
        const body = new FormData(); body.set('message', message); body.set('viewed', JSON.stringify(viewed));
        files.forEach(file => body.append('files', file));
        if (voice) body.set('voice', voice);
        await api.request(prefix + '/uploads', 'POST', body);
      } else await api.request(prefix + '/turns', 'POST', { message, viewed });
      if (!alive.current) return;
      setMessage(''); setVoice(undefined); setFiles([]); setState(undefined); setReady(false); setNotice('Запрос принят.');
      await refresh();
    } catch (reason) { reportError(reason); setNotice('При потере связи проверьте состояние запроса перед повторной отправкой.'); }
    finally { if (alive.current) setBusy(false); }
  }
  async function control(action: 'cancel' | 'resume' | 'new-chat') {
    if (!ready || busy) return;
    setBusy(true); setError('');
    generation.current++;
    try {
      const result = await api.request<{ response_text?: string }>(prefix + '/' + action, 'POST');
      if (!alive.current) return;
      if (action === 'new-chat') candidateRequest.current = undefined;
      setNotice(result.response_text ?? ''); await refresh();
    } catch (reason) { reportError(reason); }
    finally { if (alive.current) setBusy(false); }
  }
  function finishVoice() {
    const file = stop.current?.(true); stop.current = undefined; setRecording(false);
    if (file && file.size > maxFileBytes) { setError('Голосовая запись больше 10 МиБ. Запишите более короткую просьбу.'); return; }
    setVoice(file);
  }
  function selectFiles(selected: FileList | null) {
    if (!selected) return;
    const added = Array.from(selected);
    if (files.length + added.length + (voice ? 1 : 0) > maxFiles) {
      setError('Можно отправить до пяти файлов, включая голосовую запись.'); return;
    }
    if (added.some(file => file.size > maxFileBytes)) { setError('Каждый файл должен быть не больше 10 МиБ.'); return; }
    setFiles(current => [...current, ...added]); setError('');
  }
  async function microphone() {
    if (!ready || busy || active || files.length >= maxFiles) return;
    if (recording) { finishVoice(); return; }
    setBusy(true); setError('');
    try {
      const finish = await recordVoice(finishVoice);
      if (!alive.current) { finish(false); return; }
      stop.current = finish; setRecording(true); setVoice(undefined);
      if (!visibleRef.current) finishVoice();
    } catch { setError('Микрофон недоступен. Разрешите запись или отправьте текст.'); }
    finally { if (alive.current) setBusy(false); }
  }
  const latest = state?.latest;
  const active = Boolean(state?.local_polling || latest && !latest.final && latest.resume_allowed);
  return { state, latest, active, message, setMessage, voice, files, recording, busy, ready, error, notice,
    submit, control, refresh, microphone, selectFiles,
    removeVoice: () => setVoice(undefined),
    removeFile: (index: number) => setFiles(current => current.filter((_, item) => item !== index)),
    canAttach: ready && !busy && !active && !recording && files.length + (voice ? 1 : 0) < maxFiles,
    canRecord: ready && !busy && !active && files.length < maxFiles,
    canSubmit: ready && !busy && !active && !recording && Boolean(message.trim() || voice || files.length),
  };
}
export type AdminEditorController = ReturnType<typeof useAdminEditor>;
