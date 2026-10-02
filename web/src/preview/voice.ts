/** Browser capture to the existing WAV upload contract; no second ASR path. */
export async function recordVoice(onLimit: () => void) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 }, video: false });
  let audio: AudioContext | undefined;
  try {
    audio = new AudioContext();
    await audio.resume();
    const input = audio.createMediaStreamSource(stream);
    // PCM avoids relabelling Chromium's WebM as an OGG file accepted by the Hub.
    const processor = audio.createScriptProcessor(4096, 1, 1);
    const mute = audio.createGain(); mute.gain.value = 0;
    const chunks: Float32Array[] = [];
    let samples = 0, finished = false;
    processor.onaudioprocess = event => {
      const chunk = event.inputBuffer.getChannelData(0);
      if (44 + (samples + chunk.length) * 2 > 10 * 1024 * 1024) { onLimit(); return; }
      chunks.push(new Float32Array(chunk)); samples += chunk.length;
    };
    input.connect(processor); processor.connect(mute); mute.connect(audio.destination);
    return (keep: boolean) => {
      if (finished) return undefined;
      finished = true; processor.onaudioprocess = null;
      input.disconnect(); processor.disconnect(); mute.disconnect();
      stream.getTracks().forEach(track => track.stop()); void audio!.close();
      if (!keep || !samples) return undefined;
      const bytes = new ArrayBuffer(44 + samples * 2), view = new DataView(bytes);
      const text = (at: number, value: string) => [...value].forEach((char, index) => view.setUint8(at + index, char.charCodeAt(0)));
      text(0, 'RIFF'); view.setUint32(4, bytes.byteLength - 8, true); text(8, 'WAVE'); text(12, 'fmt ');
      view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
      view.setUint32(24, audio!.sampleRate, true); view.setUint32(28, audio!.sampleRate * 2, true);
      view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, 'data'); view.setUint32(40, samples * 2, true);
      let at = 44;
      for (const chunk of chunks) for (const value of chunk) {
        const sample = Math.max(-1, Math.min(1, value));
        view.setInt16(at, sample * (sample < 0 ? 32768 : 32767), true); at += 2;
      }
      return new File([bytes], 'voice.wav', { type: 'audio/wav' });
    };
  } catch (error) { stream.getTracks().forEach(track => track.stop()); void audio?.close(); throw error; }
}
