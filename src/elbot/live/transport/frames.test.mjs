import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseFrame, FrameQueue} from './frames.mjs';

test('bridge frames validate version, duration and guild before delivering Opus', () => {
  const frame = Buffer.alloc(23);
  frame[0] = 1;
  frame.writeBigUInt64BE(123456789012345678n, 2);
  frame.writeUInt16BE(20, 18);
  frame.set([0xf8, 0xff, 0xfe], 20);
  assert.deepEqual(parseFrame(frame, '123456789012345678'), Buffer.from([0xf8, 0xff, 0xfe]));
  assert.equal(parseFrame(frame, '123'), null);
  assert.equal(parseFrame(frame.subarray(0, 20), '123456789012345678'), null);
  frame[0] = 2;
  assert.equal(parseFrame(frame, '123456789012345678'), null);
});

test('music backpressure is bounded and drops stale frames', () => {
  const queue = new FrameQueue(2);
  queue.push('old'); queue.push('new'); queue.push('newest');
  assert.equal(queue.dropped, 1);
  assert.equal(queue.pop(), 'new');
  assert.equal(queue.pop(), 'newest');
  queue.clear();
  assert.equal(queue.pop(), undefined);
});
