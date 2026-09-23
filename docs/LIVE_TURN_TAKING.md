# Addressing ELBOT and ending an exchange

Deployed to the existing Pi on September 12, 2026 at 19:42 UTC. Backup:
`/home/alex/elbot-backups/turn-taking-20260912T194250Z`. All six transferred
files passed hash verification and Python compilation. Local validation:
265 Python tests passed and Ruff passed. Live conversation checks remain below.

Follow-up fix deployed at 19:54 UTC after live logs showed continuous silent
output replenishing the playback tail and keeping other speakers busy. Silence
after speech is now capped at 200 ms. Failed or budget-denied connections also
release the speaker lock and invalidate their tool identity. Regression suite:
267 Python tests passed; Ruff passed. Backup:
`/home/alex/elbot-backups/voice-fix-20260912T195403Z`.

The music server successfully resolved both `Bad Bunny` and `Bad Bunny Callaita`;
the latter returned the official CALLAÍTA video first. No play tool appeared in
the failed exchange's logs. Voice usage had reached $2.008 against the $2 daily
cap, so fresh voice requests remain blocked until allowance is available.
The cap and usage ledger were not changed; slash `/play` is independent of that cap.

The September 12 update keeps local wake detection and adds a separate input
gate. Say ELBOT for a new exchange. The identified speaker can continue until
1.5 seconds without speech-level audio. Quiet packets do not extend that time.
The model still receives synthetic silence so it can finish its answer; unrelated
microphone audio is excluded.

After the reply drains from local playback and a quiet margin passes, the same
person gets four seconds to start a follow-up without repeating ELBOT. The next
answer offers the same short window. Another person's speech cancels the implicit
follow-up. After expiry, say ELBOT again. A fresh wake during a reply interrupts
it and invalidates old-session audio and tools. Duplicate wake detections while
already forwarding the speaker do not replay audio or clear the answer.

Incoming packets alone no longer clear ELBOT's speech. Its own output does not
keep the microphone gate open. The conversation prompt asks ELBOT to answer and
wait quietly, avoid small-talk invitations, ask only necessary clarifications,
and ignore side comments. It can still chat when explicitly asked.

`ELBOT_LIVE_INPUT_SILENCE_S` defaults to 1.5 (range 0.5–5).
`ELBOT_LIVE_FOLLOWUP_S` defaults to 4 (range 0–10; zero disables implicit follow-ups).
Production uses the default prompts. Custom `ELBOT_LIVE_INSTRUCTIONS` or
`ELBOT_LIVE_BACKEND_INSTRUCTIONS` replace those defaults and must include the
desired conversational rules if configured later.

This is an audio-level and timing heuristic, not reliable intent recognition.
A same-person side comment during the four-second window can still be sent.
The wake recognizer retains its existing phonetic aliases (including elbow),
which can produce false activations. Noise level, pauses, and playback network
delay affect the timing. No transcripts or audio recordings were added.

GPT-Live has no authoritative output-audio-done event; transcript fragments are
not complete turns. The implementation therefore uses local playback progress
and audio quiet time, consistent with the cautions in the
[session documentation](https://developers.openai.com/api/docs/guides/live-conversations).
The conversational instructions follow the
[prompting guidance](https://developers.openai.com/api/docs/guides/live-prompting).

## Live checks still required

1. Ask a question with ELBOT, listen, and give a follow-up within four seconds.
2. Wait longer than four seconds and talk to another person; ELBOT should stay quiet.
3. Have another person speak after the answer; the original speaker now needs ELBOT again.
4. Leave the microphone open with quiet background noise while ELBOT answers;
   the reply should not be cut off or the conversation prolonged.
5. Say ELBOT during an answer to interrupt, then give the new request.
6. Ask a simple question; expect an answer without “anything else?” or a small-talk invitation.

Tune the thresholds after these checks; automatic tests do not establish live
recognition accuracy or guarantee that a model follows every prompt instruction.
