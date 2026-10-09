import { useEffect, useRef, useState } from 'react';
import { Pause, Play, Trash2 } from 'lucide-react';
import type { VoiceClip } from './voice';

export function voiceTime(seconds: number) {
  const value = Math.max(0, Math.floor(seconds));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}

export function VoiceWave({ peaks, progress = 0 }: { peaks: number[]; progress?: number }) {
  return <div className="admin-voice-wave" aria-hidden="true">{Array.from({ length: 40 }, (_, index) =>
    <i key={index} className={index / 40 < progress ? 'played' : ''}
      style={{ height: `${3 + 25 * Math.sqrt(peaks[index] ?? 0)}px` }} />)}</div>;
}

export function VoiceMessage({ clip, disabled, onRemove }: { clip: VoiceClip; disabled: boolean; onRemove: () => void }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false), [time, setTime] = useState(0), [error, setError] = useState('');
  useEffect(() => {
    const element = audio.current!;
    const url = URL.createObjectURL(clip.file);
    element.src = url; setPlaying(false); setTime(0); setError('');
    return () => { element.pause(); element.removeAttribute('src'); element.load(); URL.revokeObjectURL(url); };
  }, [clip]);
  useEffect(() => { if (disabled) audio.current?.pause(); }, [disabled]);
  useEffect(() => {
    const hide = () => { if (document.visibilityState === 'hidden') audio.current?.pause(); };
    document.addEventListener('visibilitychange', hide);
    return () => document.removeEventListener('visibilitychange', hide);
  }, []);
  function fail() {
    audio.current?.pause(); setPlaying(false);
    setError('Не удалось воспроизвести запись. Попробуйте ещё раз или запишите заново.');
  }
  async function toggle() {
    const element = audio.current;
    if (!element) return;
    if (!element.paused) { element.pause(); return; }
    setError('');
    try { if (element.error) element.load(); await element.play(); }
    catch (reason) {
      if (reason instanceof DOMException && reason.name === 'AbortError') return;
      fail();
    }
  }
  return <div className="admin-voice-preview">
    <audio ref={audio} preload="metadata" onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
      onEnded={() => { setPlaying(false); setTime(0); }} onTimeUpdate={() => setTime(audio.current?.currentTime ?? 0)}
      onError={fail} />
    <div className="admin-editor-voice" aria-label="Голосовое сообщение готово">
      <button type="button" className="button button--quiet admin-icon" disabled={disabled} onClick={() => void toggle()}
        aria-label={playing ? 'Пауза' : 'Прослушать запись'} aria-pressed={playing}>{playing ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}</button>
      <VoiceWave peaks={clip.peaks} progress={time / clip.duration} />
      <span className="admin-voice-time" aria-label={`Длительность записи ${voiceTime(clip.duration)}`}>{voiceTime(playing ? time : clip.duration)}</span>
      <button type="button" className="button button--quiet admin-icon" aria-label="Удалить запись" disabled={disabled} onClick={onRemove}><Trash2 aria-hidden="true" /></button>
    </div>
    {error && <p className="admin-editor-input-error" role="alert">{error}</p>}
  </div>;
}
