// Mixes bot speech over music for one 20 ms Discord frame (48 kHz stereo s16le).
export const FRAME_BYTES = 3840;

export class SpeechQueue {
  constructor(limitBytes = FRAME_BYTES * 150) { this.limit = limitBytes; this.chunks = []; this.length = 0; this.dropped = 0; }
  push(buf) {
    this.chunks.push(buf); this.length += buf.length;
    while (this.length > this.limit && this.chunks.length > 1) {
      const old = this.chunks.shift(); this.length -= old.length; this.dropped += old.length;
    }
  }
  take(n) {
    if (this.length < n) return null;
    const out = Buffer.allocUnsafe(n);
    let filled = 0;
    while (filled < n) {
      const head = this.chunks[0];
      const need = n - filled;
      if (head.length <= need) { head.copy(out, filled); filled += head.length; this.chunks.shift(); }
      else { head.copy(out, filled, 0, need); this.chunks[0] = head.subarray(need); filled += need; }
    }
    this.length -= n;
    return out;
  }
  clear() { this.chunks = []; this.length = 0; }
}

export function mixFrame(musicPcm, speechPcm, musicGain) {
  const out = Buffer.alloc(FRAME_BYTES);
  const samples = FRAME_BYTES / 2;
  for (let i = 0; i < samples; i++) {
    const music = musicPcm && musicPcm.length >= FRAME_BYTES ? musicPcm.readInt16LE(i * 2) * musicGain : 0;
    const speech = speechPcm && speechPcm.length >= FRAME_BYTES ? speechPcm.readInt16LE(i * 2) : 0;
    const v = Math.round(music + speech);
    out.writeInt16LE(v > 32767 ? 32767 : v < -32768 ? -32768 : v, i * 2);
  }
  return out;
}
