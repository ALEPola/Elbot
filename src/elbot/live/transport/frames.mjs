// Aiko-IT-Systems/Lavalink e72ce9a ExternalBridgeTransport wire format.
export function parseFrame(data, guildId) {
  if (!Buffer.isBuffer(data) || data.length <= 20 || data.length > 4096) return null;
  if (data[0] !== 1 || data[1] !== 0 || data.readBigUInt64BE(2).toString() !== guildId) return null;
  if (data.readUInt16BE(18) !== 20) return null;
  return data.subarray(20);
}

export class FrameQueue {
  constructor(limit = 10) { this.limit = limit; this.frames = []; this.dropped = 0; }
  push(frame) {
    if (this.frames.length >= this.limit) { this.frames.shift(); this.dropped++; }
    this.frames.push(frame);
  }
  pop() { return this.frames.shift(); }
  clear() { this.frames.length = 0; }
}
