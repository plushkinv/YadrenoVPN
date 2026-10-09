import { useEffect, useRef, useState } from 'react';
import { microphoneError, recordVoice, type VoiceClip, type VoiceProgress, type VoiceRecorder } from './voice';

const emptyProgress: VoiceProgress = { duration: 0, peaks: [] };

export function useEditorVoice(visible: boolean) {
  const [clip, setClip] = useState<VoiceClip>();
  const [phase, setPhase] = useState<'idle' | 'requesting' | 'recording'>('idle');
  const [progress, setProgress] = useState(emptyProgress);
  const [error, setError] = useState(''), [notice, setNotice] = useState('');
  const recorder = useRef<VoiceRecorder | undefined>(undefined);
  const request = useRef<AbortController | undefined>(undefined);
  const mounted = useRef(true), visibleRef = useRef(visible);
  visibleRef.current = visible;

  function finish(keep = true, message = '') {
    const active = recorder.current;
    recorder.current = undefined;
    const value = active?.finish(keep);
    request.current?.abort(); request.current = undefined;
    setPhase('idle'); setProgress(emptyProgress);
    if (active) {
      setClip(value);
      setError(keep && !value ? 'Запись пустая. Попробуйте записать ещё раз или напишите текстом.' : '');
      setNotice(value ? message : '');
    }
  }
  async function start() {
    if (request.current) return;
    const pending = new AbortController(); request.current = pending;
    setPhase('requesting'); setError(''); setNotice('');
    try {
      const value = await recordVoice({ signal: pending.signal,
        onProgress: value => { if (mounted.current && !pending.signal.aborted) setProgress(value); },
        onLimit: () => finish(true, 'Достигнут лимит 10 МиБ. Запись остановлена и готова к прослушиванию.'),
        onInterrupted: () => finish(true, 'Запись прервалась. Сохранённую часть можно прослушать или записать заново.'),
      });
      if (!mounted.current || pending.signal.aborted || !visibleRef.current || document.visibilityState === 'hidden') {
        value.finish(false); return;
      }
      recorder.current = value; setClip(undefined); setProgress(emptyProgress); setPhase('recording');
    } catch (reason) {
      if (mounted.current && !pending.signal.aborted) setError(microphoneError(reason));
    } finally {
      if (mounted.current && request.current === pending && !recorder.current) {
        pending.abort(); request.current = undefined; setPhase('idle');
      }
    }
  }
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; recorder.current?.finish(false); recorder.current = undefined;
      request.current?.abort(); request.current = undefined; };
  }, []);
  useEffect(() => { if (!visible) finish(); }, [visible]);
  useEffect(() => {
    const hide = () => { if (document.visibilityState === 'hidden') finish(); };
    document.addEventListener('visibilitychange', hide);
    return () => document.removeEventListener('visibilitychange', hide);
  }, []);
  return { clip, phase, progress, error, notice, start, finish,
    clear: () => { setClip(undefined); setError(''); setNotice(''); },
    dismissError: () => setError(''),
  };
}
