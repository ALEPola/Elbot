import {AudioPlayerStatus, createAudioResource, StreamType} from '@discordjs/voice';

// @discordjs/voice stops a resource after five consecutive empty reads. That
// is correct for files, but normal for this live mixer between speech/music
// bursts. Start a fresh resource when input returns instead of leaving the
// player permanently Idle or streaming silence forever.
export function createRestartablePlayback(player, makeStream) {
  let generation = 0;

  const invalidate = () => { generation++; };
  player.on(AudioPlayerStatus.Idle, invalidate);

  return {
    ensurePlaying() {
      if (player.state.status !== AudioPlayerStatus.Idle) return false;
      const current = ++generation;
      const source = makeStream(() => current === generation);
      player.play(createAudioResource(source, {
        inputType: StreamType.Opus,
        silencePaddingFrames: 0,
      }));
      return true;
    },
    dispose() {
      generation++;
      player.off(AudioPlayerStatus.Idle, invalidate);
    },
  };
}
