import {test} from 'node:test';
import assert from 'node:assert/strict';
import {FRAME_BYTES, SpeechQueue, mixFrame} from './mixer.mjs';

function frame(value) {
  const b = Buffer.alloc(FRAME_BYTES);
  for (let i = 0; i < FRAME_BYTES / 2; i++) b.writeInt16LE(value, i * 2);
  return b;
}

test('speech queue assembles exact frames across chunk boundaries and drops oldest when over limit', () => {
  const q = new SpeechQueue(FRAME_BYTES * 2);
  q.push(Buffer.alloc(1000, 1)); q.push(Buffer.alloc(3000, 2)); q.push(Buffer.alloc(2000, 3));
  assert.equal(q.length, 6000);
  const f = q.take(FRAME_BYTES);
  assert.equal(f.length, FRAME_BYTES);
  assert.equal(f[0], 1); assert.equal(f[999], 1); assert.equal(f[1000], 2);
  assert.equal(q.take(FRAME_BYTES), null);
  q.push(Buffer.alloc(FRAME_BYTES * 3, 9));
  assert.ok(q.dropped > 0);
  assert.ok(q.length <= FRAME_BYTES * 3);
});

test('mixing ducks music, adds speech and clamps', () => {
  const mixed = mixFrame(frame(1000), frame(200), 0.25);
  assert.equal(mixed.readInt16LE(0), 450);
  const clipped = mixFrame(frame(30000), frame(30000), 1);
  assert.equal(clipped.readInt16LE(0), 32767);
  const speechOnly = mixFrame(null, frame(-5), 0.25);
  assert.equal(speechOnly.readInt16LE(2), -5);
  const duckedOnly = mixFrame(frame(-1000), null, 0.5);
  assert.equal(duckedOnly.readInt16LE(0), -500);
});
