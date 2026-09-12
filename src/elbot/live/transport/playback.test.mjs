import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Readable} from 'node:stream';
import {setTimeout as delay} from 'node:timers/promises';
import {
  AudioPlayerStatus, createAudioPlayer, NoSubscriberBehavior,
} from '@discordjs/voice';
import {createRestartablePlayback} from './playback.mjs';

const OPUS_SILENCE = Buffer.from([0xf8, 0xff, 0xfe]);

async function waitFor(predicate, timeoutMs = 3000) {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error('Timed out waiting for player state');
    await delay(10);
  }
}

test('live playback restarts after an input gap makes the player Idle', async () => {
  const queue = [];
  let produced = 0;
  const player = createAudioPlayer({
    behaviors: {noSubscriber: NoSubscriberBehavior.Play},
  });
  const playback = createRestartablePlayback(player, isCurrent => Readable.from((async function* () {
    while (isCurrent()) {
      const frame = queue.shift();
      if (frame) { produced++; yield frame; }
      else await delay(5);
    }
  })(), {objectMode: true, highWaterMark: 1}));

  try {
    // Model the reported first reply, followed by more than five empty
    // 20 ms reads. The stock player must become Idle after this gap.
    queue.push(...Array.from({length: 70}, () => OPUS_SILENCE));
    assert.equal(playback.ensurePlaying(), true);
    await waitFor(() => produced === 70);
    await waitFor(() => player.state.status === AudioPlayerStatus.Idle);

    queue.push(...Array.from({length: 10}, () => OPUS_SILENCE));
    assert.equal(playback.ensurePlaying(), true);
    await waitFor(() => produced > 70);
    assert.notEqual(player.state.status, AudioPlayerStatus.Idle);
  } finally {
    playback.dispose();
    player.stop(true);
  }
});
