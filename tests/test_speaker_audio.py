from elbot.live.audio_input import Speaker, SpeakerAudio


PCM = b"\x01\x00\x02\x00" * 960


def receiver():
    now = [10.0]
    audio = SpeakerAudio(1, 2, clock=lambda: now[0])
    generation = audio.start()
    return audio, generation, now


def test_alternating_speakers_are_isolated_and_expire_without_more_audio():
    audio, gen, now = receiver()
    audio.bind(gen, 10, Speaker(100, "Alexis", 1, 2))
    audio.bind(gen, 20, Speaker(200, "Jovan", 1, 2))
    audio.feed(gen, 10, PCM)
    audio.feed(gen, 20, PCM[::-1])
    assert audio.snapshot(100) == PCM
    assert audio.snapshot(200) == PCM[::-1]
    assert [(e.kind, e.speaker.display_name) for e in audio.tick()] == [
        ("speaker_start", "Alexis"), ("speaker_start", "Jovan"),
    ]
    now[0] += 0.6
    assert [e.kind for e in audio.tick()] == ["speaker_stop", "speaker_stop"]
    now[0] += 0.5
    audio.tick()
    assert audio.snapshot(100) == audio.snapshot(200) == b""


def test_unknown_bot_wrong_channel_and_stale_connection_are_rejected():
    audio, gen, _ = receiver()
    assert not audio.feed(gen, 10, PCM)
    assert not audio.bind(gen, 10, Speaker(100, "Bot", 1, 2, is_bot=True))
    assert not audio.bind(gen, 10, Speaker(100, "Wrong guild", 3, 2))
    assert not audio.bind(gen, 10, Speaker(100, "Wrong channel", 1, 3))
    assert audio.bind(gen, 10, Speaker(100, "Alexis", 1, 2))
    audio.feed(gen, 10, PCM)
    newer = audio.start()
    audio.bind(newer, 10, Speaker(200, "Jovan", 1, 2))
    assert not audio.feed(gen, 10, PCM)
    assert not audio.bind(gen, 10, Speaker(100, "Alexis", 1, 2))
    assert audio.snapshot(100) == audio.snapshot(200) == b""


def test_ssrc_reuse_and_member_departure_discard_old_audio():
    audio, gen, _ = receiver()
    audio.bind(gen, 10, Speaker(100, "Alexis", 1, 2))
    audio.feed(gen, 10, PCM)
    audio.bind(gen, 10, Speaker(200, "Jovan", 1, 2))
    assert audio.snapshot(100) == audio.snapshot(200) == b""
    audio.feed(gen, 10, PCM)
    audio.remove(200)
    assert audio.snapshot(200) == b""
    assert not audio.feed(gen, 10, PCM)


def test_new_ssrc_and_display_name_use_discord_identity():
    audio, gen, _ = receiver()
    audio.bind(gen, 10, Speaker(100, "Old name", 1, 2))
    audio.bind(gen, 10, Speaker(100, "New name", 1, 2))
    audio.feed(gen, 10, PCM)
    assert audio.tick()[0].speaker.display_name == "New name"
    audio.bind(gen, 20, Speaker(100, "New name", 1, 2))
    assert not audio.feed(gen, 10, PCM)
    assert audio.snapshot(100) == b""


def test_memory_limit_silence_and_shutdown():
    audio, gen, _ = receiver()
    audio.bind(gen, 10, Speaker(100, "Alexis", 1, 2))
    audio.feed(gen, 10, bytes(len(PCM)))
    assert audio.tick() == []
    for _ in range(1000):
        audio.feed(gen, 10, PCM)
    assert len(audio.snapshot(100)) <= audio.max_bytes
    audio.stop()
    assert audio.snapshot(100) == b""
    assert not audio.feed(gen, 10, PCM)


def test_sequence_wrap_duplicates_late_packets_and_gaps():
    audio, gen, _ = receiver()
    audio.bind(gen, 10, Speaker(100, "Alexis", 1, 2))
    for seq in (65534, 65535, 0, 2):
        assert audio.feed(gen, 10, PCM, sequence=seq)
    assert not audio.feed(gen, 10, PCM, sequence=2)
    assert not audio.feed(gen, 10, PCM, sequence=1)
    assert audio.metrics["sequence_gaps"] == 1
    assert audio.metrics["late_or_duplicate"] == 2


def test_invalid_frames_and_speaker_limit():
    audio = SpeakerAudio(1, 2, max_speakers=1)
    gen = audio.start()
    audio.bind(gen, 10, Speaker(100, "Alexis", 1, 2))
    assert not audio.bind(gen, 20, Speaker(200, "Jovan", 1, 2))
    for pcm in (b"", b"a", b"a" * (audio.MAX_FRAME_BYTES + 4)):
        assert not audio.feed(gen, 10, pcm)
    assert audio.metrics["frames"] == 0
