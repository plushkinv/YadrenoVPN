import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { admissionUnknown, editorError, previewError } from './editorErrors';
import { useApp } from '../runtime/context';
import type { PreviewContext } from './contracts';
import { useEditorVoice } from './useEditorVoice';
import { maxVoiceBytes } from './voice';

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
type Turn = { kind: 'message' } | { kind: 'apply'; candidate: CandidatePreview };
const prefix = '/admin/ui/editor', maxFiles = 5;
const applyMessage = 'Примени именно показанный проверенный вариант интерфейса без дополнительных изменений. Если он устарел или недоступен, сообщи причину; не меняй и не пересобирай его автоматически.';

export function useAdminEditor({ viewed, onCandidate, onNewChat, polling, visible, contextPending = false }: {
  viewed: PreviewContext; onCandidate: (value: CandidatePreview) => void;
  polling: boolean; visible: boolean; contextPending?: boolean; onNewChat?: () => Promise<void>;
}) {
  const { api } = useApp();
  const [state, setState] = useState<EditorState>();
  const [message, setMessage] = useState(''), [files, setFiles] = useState<File[]>([]);
  const voice = useEditorVoice(visible);
  const [busy, setBusy] = useState(false), [ready, setReady] = useState(false), [refreshing, setRefreshing] = useState(false);
  const [actionError, setActionError] = useState(''), [stateError, setStateError] = useState('');
  const [failedControl, setFailedControl] = useState<'cancel' | 'resume' | 'new-chat'>();
  const [inputError, setInputError] = useState(''), [candidateError, setCandidateError] = useState('');
  const [notice, setNotice] = useState('');
  const [uncertain, setUncertain] = useState(false), [checkedUncertain, setCheckedUncertain] = useState(false);
  const [candidate, setCandidate] = useState<CandidatePreview>();
  const alive = useRef(true), operation = useRef(false), generation = useRef(0);
  const checking = useRef<{ generation: number; promise: Promise<boolean> } | undefined>(undefined);
  const candidateRequest = useRef<number | string | undefined>(undefined);
  const lastTurn = useRef<Turn>({ kind: 'message' });
  const candidateCallback = useRef(onCandidate); candidateCallback.current = onCandidate;
  const newChatCallback = useRef(onNewChat); newChatCallback.current = onNewChat;
  const latest = state?.latest;
  const active = Boolean(state?.local_polling || latest && !latest.final);
  const capturing = voice.phase !== 'idle';
  const canAct = ready && !contextPending && !busy && !active && !capturing;
  const hasInput = Boolean(message.trim() || voice.clip || files.length);

  function receiveCandidate(value: CandidatePreview) {
    setCandidate(value); setCandidateError(''); candidateCallback.current(value);
  }
  function refreshState(forcePreview = false): Promise<boolean> {
    const started = generation.current;
    if (checking.current?.generation === started) return checking.current.promise;
    const current = () => alive.current && started === generation.current;
    setRefreshing(true);
    const promise = (async () => {
      try {
        const value = await api.request<EditorState>(prefix);
        if (!current()) return false;
        setState(value); setStateError('');
        if (value.latest?.final) {
          if (forcePreview || candidateRequest.current !== value.latest.request_id) {
            setReady(false); setCandidate(undefined);
            try {
              const result = await api.request<CandidatePreview>(prefix + '/preview', 'POST');
              if (!current()) return false;
              receiveCandidate(result);
            } catch (reason) {
              if (!current()) return false;
              setCandidateError(previewError(reason));
              if (!(reason instanceof ApiError && [404, 409, 422].includes(reason.status))) throw reason;
            }
            candidateRequest.current = value.latest.request_id;
          }
          setNotice('');
        } else { setCandidate(undefined); setCandidateError(''); candidateRequest.current = undefined; }
        if (current()) { setReady(true); return true; }
      } catch (reason) { if (current()) setStateError(editorError(reason, 'state')); }
      return false;
    })().finally(() => {
      if (checking.current?.promise === promise) { checking.current = undefined; if (alive.current) setRefreshing(false); }
    });
    checking.current = { generation: started, promise };
    return promise;
  }
  async function refresh(deliberate = true) {
    // A deliberate status check also revalidates an unchanged candidate.
    const started = generation.current;
    const pending = checking.current;
    if (pending?.generation === generation.current) await pending.promise;
    if (!alive.current || started !== generation.current) return;
    if (await refreshState(true) && deliberate && alive.current && started === generation.current) setCheckedUncertain(true);
  }
  useEffect(() => {
    alive.current = true; generation.current++; candidateRequest.current = undefined;
    setReady(false); setActionError(''); setStateError(''); setCandidateError('');
    void refreshState();
    return () => { alive.current = false; generation.current++; };
  }, [api]);
  useEffect(() => {
    if (!polling) return;
    setReady(false); void refresh(false);
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void refreshState(); }, 4000);
    const show = () => { if (document.visibilityState === 'visible') void refresh(false); };
    document.addEventListener('visibilitychange', show);
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', show); };
  }, [polling, api]);

  async function perform(turn: Turn, repeat = false) {
    if (!canAct || operation.current || uncertain && (!repeat || !checkedUncertain) || turn.kind === 'message' && !hasInput) return;
    operation.current = true; setBusy(true); setActionError(''); setFailedControl(undefined); setInputError(''); setNotice('');
    generation.current++;
    lastTurn.current = turn;
    let admitting = false;
    try {
      if (turn.kind === 'apply') {
        let checked: CandidatePreview;
        try { checked = await api.request<CandidatePreview>(prefix + '/preview', 'POST'); }
        catch (reason) { if (alive.current) { setCandidate(undefined); setCandidateError(previewError(reason)); } return; }
        if (!alive.current) return;
        receiveCandidate(checked);
        if (checked.candidate.build_id !== turn.candidate.candidate.build_id || checked.task_id !== turn.candidate.task_id
          || viewed.ui_version !== checked.candidate.build_id) {
          lastTurn.current = { kind: 'apply', candidate: checked };
          setNotice('Предпросмотр обновлён. Проверьте показанный вариант перед применением.'); return;
        }
        if (checked.published) { setUncertain(false); setNotice('Этот вариант уже применён.'); return; }
      }
      admitting = true;
      const text = turn.kind === 'apply' ? applyMessage : message;
      if (turn.kind === 'message' && (voice.clip || files.length)) {
        const body = new FormData(); body.set('message', text); body.set('viewed', JSON.stringify(viewed));
        files.forEach(file => body.append('files', file));
        if (voice.clip) body.set('voice', voice.clip.file);
        await api.request(prefix + '/uploads', 'POST', body);
      } else await api.request(prefix + '/turns', 'POST', { message: text, viewed });
      if (!alive.current) return;
      if (turn.kind === 'message') { setMessage(''); voice.clear(); setFiles([]); }
      setUncertain(false); setCheckedUncertain(false); setCandidate(undefined);
      setState(undefined); setReady(false); setNotice('Запрос принят.');
      await refreshState();
    } catch (reason) {
      if (alive.current) {
        setActionError(editorError(reason, turn.kind === 'apply' ? 'apply' : 'submit'));
        if (admitting) { setUncertain(admissionUnknown(reason)); setCheckedUncertain(false); }
      }
    } finally { operation.current = false; if (alive.current) setBusy(false); }
  }
  async function control(action: 'cancel' | 'resume' | 'new-chat') {
    if (!ready || operation.current || capturing || action === 'new-chat' && (active || uncertain)) return;
    operation.current = true; setBusy(true); setActionError(''); setFailedControl(undefined); setNotice(''); generation.current++;
    try {
      const result = await api.request<{ response_text?: string }>(prefix + '/' + action, 'POST');
      if (!alive.current) return;
      if (action === 'new-chat') { candidateRequest.current = undefined; setCandidate(undefined); }
      setNotice(result.response_text ?? ''); await refreshState();
      if (action === 'new-chat' && alive.current) await newChatCallback.current?.();
    } catch (reason) { if (alive.current) { setActionError(editorError(reason, action)); setFailedControl(action); } }
    finally { operation.current = false; if (alive.current) setBusy(false); }
  }
  const canAttach = canAct && files.length + (voice.clip ? 1 : 0) < maxFiles;
  function selectFiles(selected: FileList | null) {
    if (!selected || !canAttach) return;
    const added = Array.from(selected);
    if (files.length + added.length + (voice.clip ? 1 : 0) > maxFiles) {
      setInputError('Можно отправить до пяти файлов, включая голосовую запись.'); return;
    }
    if (added.some(file => file.size > maxVoiceBytes)) { setInputError('Каждый файл должен быть не больше 10 МиБ.'); return; }
    setFiles(current => [...current, ...added]); setInputError('');
  }
  const canRecord = canAct && files.length < maxFiles;
  return { state, latest, active, message, setMessage, voice, files, busy, ready: ready && !contextPending, refreshing,
    actionError, stateError, inputError, candidateError, notice, uncertain, candidate,
    canRepeat: failedControl ? ready && !busy && !capturing && (failedControl !== 'new-chat' || !active && !uncertain)
      : canAct && (!uncertain || checkedUncertain) && (lastTurn.current.kind === 'apply' || hasInput),
    repeat: () => failedControl ? control(failedControl) : perform(lastTurn.current, true),
    submit: () => perform({ kind: 'message' }),
    apply: () => candidate && perform({ kind: 'apply', candidate }),
    canApply: canAct && !uncertain && Boolean(candidate && !candidate.published),
    control, refresh, microphone: () => { if (canRecord) void voice.start(); }, selectFiles,
    clearPreview: () => { setCandidate(undefined); setCandidateError(''); },
    removeFile: (index: number) => { setFiles(current => current.filter((_, item) => item !== index)); setInputError(''); },
    canAttach, canRecord, canSubmit: canAct && !uncertain && hasInput,
  };
}
export type AdminEditorController = ReturnType<typeof useAdminEditor>;
