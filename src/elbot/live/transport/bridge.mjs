// No Discord login token: the existing Nextcord gateway supplies voice events.
// stdin/stdout are private IPC. Never redirect stdout to operational logs.
import readline from 'node:readline';
import {Readable} from 'node:stream';
import {setTimeout as delay} from 'node:timers/promises';
import WebSocket from 'ws';
import prism from 'prism-media';
import opus from '@discordjs/opus';
import {
  joinVoiceChannel, VoiceConnectionStatus, entersState, EndBehaviorType,
  createAudioPlayer, createAudioResource, StreamType, NoSubscriberBehavior,
} from '@discordjs/voice';
import {parseFrame, FrameQueue} from './frames.mjs';
import {FRAME_BYTES, SpeechQueue, mixFrame} from './mixer.mjs';

let connection, adapter, bridge, player;
let guildId, listening = false, closing = false;
let generation = 0;
let members = new Set();
const subscriptions = new Map();
const frames = new FrameQueue();
// Bot speech (48 kHz stereo PCM from Python) is mixed over ducked music.
const speech = new SpeechQueue();
const DUCK_GAIN = 0.18, DUCK_TAIL_MS = 400;
let duckUntil = 0;
const musicDecoder = new opus.OpusEncoder(48000, 2);
const speechEncoder = new opus.OpusEncoder(48000, 2);
// Module scope: touched both by the 'speak' control handler and the frame
// generator inside start(), which run in different closures.
const diag = {speechFrames: 0, speechBytesIn: 0, musicFrames: 0, silentTicks: 0, encodeErrors: 0, mixed: 0};
// PCM is best-effort: under backpressure drop audio rather than the transport.
// Only a runaway control backlog is fatal.
let pcmDropped = 0;
const emit = (message) => {
  const pending = process.stdout.writableLength;
  if (message.op === 'pcm' && pending > 256 * 1024) {
    if (++pcmDropped % 250 === 0) {
      process.stdout.write(JSON.stringify({op: 'receive_error', reason: 'backpressure', dropped: pcmDropped}) + '\n');
    }
    return;
  }
  if (pending > 4 * 1024 * 1024) { shutdown(1); return; }
  process.stdout.write(JSON.stringify(message) + '\n');
};

function stopReceive() {
  listening = false;
  for (const {source, decoder} of subscriptions.values()) { source.destroy(); decoder.destroy(); }
  subscriptions.clear();
}

function shutdown(code = 0) {
  if (closing) return;
  closing = true;
  stopReceive();
  frames.clear();
  speech.clear();
  player?.stop();
  if (connection?.state.status !== VoiceConnectionStatus.Destroyed) connection?.destroy();
  bridge?.terminate();
  process.exitCode = code;
  process.stdin.destroy();
  // Native voice resources must not leave an orphan process after parent exit.
  setTimeout(() => process.exit(code), 250).unref();
}

function subscribe(userId) {
  if (!listening || !members.has(userId) || subscriptions.has(userId)) return;
  const ssrc = connection.receiver.ssrcMap.get(userId)?.audioSSRC;
  if (ssrc === undefined) return;
  const source = connection.receiver.subscribe(userId, {
    end: {behavior: EndBehaviorType.AfterSilence, duration: 500},
  });
  const decoder = new prism.opus.Decoder({rate: 48000, channels: 2, frameSize: 960});
  const state = {source, decoder};
  const streamGeneration = generation;
  subscriptions.set(userId, state);
  const clean = () => {
    if (subscriptions.get(userId) === state) subscriptions.delete(userId);
    source.unpipe(decoder); source.destroy(); decoder.destroy();
  };
  source.on('error', () => { emit({op: 'receive_error', user_id: userId}); clean(); });
  decoder.on('error', () => { emit({op: 'receive_error', user_id: userId}); clean(); });
  source.on('end', clean);
  decoder.on('data', pcm => {
    if (listening && members.has(userId)) emit({
      op: 'pcm', generation: streamGeneration, user_id: userId, ssrc, pcm: pcm.toString('base64'),
    });
  });
  source.pipe(decoder);
}

async function start(config) {
  if (bridge) throw new Error('Already started');
  guildId = config.guild_id;
  const url = new URL(config.bridge_url);
  if (!['ws:', 'wss:'].includes(url.protocol)) throw new Error('Invalid bridge URL');
  bridge = new WebSocket(url, {headers: {Authorization: `Bearer ${config.bridge_token}`}, maxPayload: 65536});
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Bridge timeout')), 10000);
    bridge.once('open', () => { clearTimeout(timer); resolve(); });
    bridge.once('error', () => { clearTimeout(timer); reject(new Error('Bridge unavailable')); });
  });
  bridge.on('message', (data, binary) => {
    if (!binary) return; // Voice credentials in control messages are never logged.
    const opus = parseFrame(data, guildId);
    if (opus) frames.push(opus);
  });
  bridge.on('error', () => { emit({op: 'failed', reason: 'bridge_error'}); shutdown(1); });
  bridge.on('close', () => { if (!closing) { emit({op: 'failed', reason: 'bridge_closed'}); shutdown(1); } });
  connection = joinVoiceChannel({
    guildId, channelId: config.channel_id, selfDeaf: false, selfMute: false,
    adapterCreator(methods) {
      adapter = methods;
      return {
        sendPayload(payload) { emit({op: 'gateway_send', payload}); return !closing; },
        destroy() {},
      };
    },
  });
  connection.on('error', () => { emit({op: 'failed', reason: 'voice_error'}); shutdown(1); });
  connection.on('stateChange', (oldState, newState) => {
    if (newState.status !== VoiceConnectionStatus.Ready) { stopReceive(); frames.clear(); }
    emit({op: 'voice_status', status: newState.status});
    if (newState.status === VoiceConnectionStatus.Disconnected && !closing) {
      // A move may recover via the existing gateway, otherwise fail visibly.
      entersState(connection, VoiceConnectionStatus.Ready, 10000).catch(() => {
        if (!closing) { emit({op: 'failed', reason: 'voice_disconnected'}); shutdown(1); }
      });
    }
  });
  connection.receiver.speaking.on('start', subscribe);
  await entersState(connection, VoiceConnectionStatus.Ready, 20000);
  player = createAudioPlayer({behaviors: {noSubscriber: NoSubscriberBehavior.Pause}});
  setInterval(() => { emit({op: 'diag', ...diag}); }, 5000).unref();
  const source = Readable.from((async function* () {
    while (!closing) {
      const music = frames.pop();
      let musicPcm = null;
      if (music) {
        diag.musicFrames++;
        // Always decode so the decoder state stays continuous across ducking.
        try { musicPcm = musicDecoder.decode(music); } catch { musicPcm = null; }
      }
      const speechPcm = speech.take(FRAME_BYTES);
      if (speechPcm) {
        diag.speechFrames++;
        duckUntil = Date.now() + DUCK_TAIL_MS;
        try {
          const mixed = mixFrame(musicPcm, speechPcm, DUCK_GAIN);
          diag.mixed++;
          yield speechEncoder.encode(mixed);
        } catch (e) { diag.encodeErrors++; }
      } else if (music) {
        if (Date.now() < duckUntil && musicPcm) {
          try { yield speechEncoder.encode(mixFrame(musicPcm, null, DUCK_GAIN)); }
          catch { diag.encodeErrors++; yield music; }
        } else yield music;
      } else { diag.silentTicks++; await delay(5); }
    }
  })(), {objectMode: true, highWaterMark: 1});
  player.on('error', () => { emit({op: 'failed', reason: 'playback_error'}); shutdown(1); });
  connection.subscribe(player);
  player.play(createAudioResource(source, {inputType: StreamType.Opus, silencePaddingFrames: 0}));
  emit({op: 'ready'});
}

const input = readline.createInterface({input: process.stdin, crlfDelay: Infinity});
input.on('line', line => {
  try {
    if (line.length > 65536) throw new Error('Oversized control message');
    const message = JSON.parse(line);
    if (message.op === 'start') {
      start(message).catch(() => { emit({op: 'failed', reason: 'startup_failed'}); shutdown(1); });
    } else if (message.op === 'voice_state') adapter?.onVoiceStateUpdate(message.data);
    else if (message.op === 'voice_server') adapter?.onVoiceServerUpdate(message.data);
    else if (message.op === 'listen') {
      stopReceive(); members = new Set(message.members || []);
      generation = message.generation;
      listening = Boolean(message.enabled) && connection?.state.status === VoiceConnectionStatus.Ready;
      if (listening) for (const id of members) subscribe(id);
    } else if (message.op === 'members') {
      members = new Set(message.members || []);
      for (const [id, {source, decoder}] of subscriptions) {
        if (!members.has(id)) { source.destroy(); decoder.destroy(); subscriptions.delete(id); }
      }
    } else if (message.op === 'speak') {
      const pcm = Buffer.from(String(message.pcm || ''), 'base64');
      if (pcm.length % 4 !== 0) throw new Error('Speech PCM must be 16-bit stereo');
      if (pcm.length) { speech.push(pcm); diag.speechBytesIn += pcm.length; }
    } else if (message.op === 'speak_clear') {
      speech.clear(); duckUntil = 0;
    } else if (message.op === 'close') shutdown();
  } catch (e) {
    // Visible in the parent's stderr log; never includes audio payloads.
    console.error('[bridge] invalid control, op=%s: %s', (() => {
      try { return JSON.parse(line).op; } catch { return '?'; }
    })(), e && e.message);
    emit({op: 'failed', reason: 'invalid_control'});
    shutdown(1);
  }
});
input.on('close', () => shutdown());
process.on('SIGTERM', () => shutdown());
process.on('SIGINT', () => shutdown());
process.on('uncaughtException', () => { emit({op: 'failed', reason: 'transport_exception'}); shutdown(1); });
