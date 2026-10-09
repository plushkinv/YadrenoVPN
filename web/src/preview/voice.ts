/** Bounded mono PCM WAV capture for the existing Hub upload/ASR contract. */
export const maxVoiceBytes = 10 * 1024 * 1024;
export interface VoiceProgress { duration: number; peaks: number[] }
export interface VoiceClip extends VoiceProgress { file: File }
export interface VoiceRecorder { finish: (keep: boolean) => VoiceClip | undefined }

export function microphoneError(reason: unknown): string {
  const name = reason instanceof Error ? reason.name : '';
  if (name === 'NotAllowedError' || name === 'SecurityError')
    return 'Нет доступа к микрофону. Разрешите запись в настройках приложения или браузера либо напишите текстом.';
  if (name === 'NotFoundError' || name === 'OverconstrainedError')
    return 'Микрофон не найден. Подключите его и попробуйте снова либо напишите текстом.';
  if (name === 'NotSupportedError')
    return 'Запись звука недоступна в этом браузере. Откройте Mini App в поддерживаемом клиенте либо напишите текстом.';
  return 'Не удалось записать звук. Проверьте, доступен ли микрофон, и попробуйте снова либо напишите текстом.';
}

export async function recordVoice({ signal, onProgress, onLimit, onInterrupted }: {
  signal: AbortSignal; onProgress: (value: VoiceProgress) => void;
  onLimit: () => void; onInterrupted: () => void;
}): Promise<VoiceRecorder> {
  if (!navigator.mediaDevices?.getUserMedia || typeof AudioContext === 'undefined')
    throw new DOMException('Audio capture unavailable', 'NotSupportedError');
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 }, video: false });
  let audio: AudioContext | undefined, input: MediaStreamAudioSourceNode | undefined;
  let processor: ScriptProcessorNode | undefined, mute: GainNode | undefined;
  let finished = false, samples = 0;
  const chunks: Float32Array[] = [], recentPeaks: number[] = [];
  const interrupted = () => { if (!finished) onInterrupted(); };
  const cleanup = () => {
    finished = true;
    signal.removeEventListener('abort', abort);
    if (processor) processor.onaudioprocess = null;
    if (audio) audio.onstatechange = null;
    input?.disconnect(); processor?.disconnect(); mute?.disconnect();
    stream.getTracks().forEach(track => { track.removeEventListener('ended', interrupted); track.stop(); });
    if (audio && audio.state !== 'closed') void audio.close().catch(() => {});
  };
  const abort = () => { cleanup(); chunks.length = 0; };
  try {
    if (signal.aborted) throw new DOMException('Capture cancelled', 'AbortError');
    signal.addEventListener('abort', abort, { once: true });
    audio = new AudioContext();
    await audio.resume();
    if (signal.aborted) throw new DOMException('Capture cancelled', 'AbortError');
    const rate = audio.sampleRate;
    input = audio.createMediaStreamSource(stream);
    // Keep the supported WAV container; Chromium MediaRecorder emits WebM, not OGG.
    processor = audio.createScriptProcessor(4096, 1, 1);
    mute = audio.createGain(); mute.gain.value = 0;
    processor.onaudioprocess = event => {
      if (finished) return;
      const chunk = event.inputBuffer.getChannelData(0);
      if (44 + (samples + chunk.length) * 2 > maxVoiceBytes) { onLimit(); return; }
      chunks.push(new Float32Array(chunk)); samples += chunk.length;
      let peak = 0;
      for (const value of chunk) peak = Math.max(peak, Math.abs(value));
      recentPeaks.push(Math.min(1, peak));
      if (recentPeaks.length > 40) recentPeaks.shift();
      onProgress({ duration: samples / rate, peaks: [...recentPeaks] });
    };
    input.connect(processor); processor.connect(mute); mute.connect(audio.destination);
    stream.getTracks().forEach(track => track.addEventListener('ended', interrupted));
    audio.onstatechange = () => { if (audio?.state !== 'running') interrupted(); };
    return { finish(keep) {
      if (finished) return;
      cleanup();
      if (!keep || !samples) { chunks.length = 0; return; }
      const bytes = new ArrayBuffer(44 + samples * 2), view = new DataView(bytes);
      const text = (at: number, value: string) => [...value].forEach((char, index) => view.setUint8(at + index, char.charCodeAt(0)));
      text(0, 'RIFF'); view.setUint32(4, bytes.byteLength - 8, true); text(8, 'WAVE'); text(12, 'fmt ');
      view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
      view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true);
      view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, 'data'); view.setUint32(40, samples * 2, true);
      const peaks = Array<number>(40).fill(0);
      let index = 0;
      for (const chunk of chunks) for (const value of chunk) {
        const sample = Math.max(-1, Math.min(1, value));
        view.setInt16(44 + index * 2, sample * (sample < 0 ? 32768 : 32767), true);
        const bucket = Math.min(39, Math.floor(index * 40 / samples));
        peaks[bucket] = Math.max(peaks[bucket], Math.abs(sample)); index++;
      }
      chunks.length = 0;
      return { file: new File([bytes], 'voice.wav', { type: 'audio/wav' }), duration: samples / rate, peaks };
    } };
  } catch (error) { cleanup(); throw error; }
}
