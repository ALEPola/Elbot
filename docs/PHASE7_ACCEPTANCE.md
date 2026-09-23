# Phase 7: permissions, confirmation, and fair queuing

Implemented September 12, 2026. Automated validation: 261 Python tests and 5
voice transport tests passed; Ruff passed. Discord acceptance remains to be
performed with at least two human accounts. The existing intermittent speech
cutoff investigation is separate from this phase.

Deployed with explicit approval to the Pi at `192.168.1.5` on September 12,
2026 at 04:47 UTC. All 16 package files passed SHA-256 verification and Python
compilation. Backup: `/home/alex/elbot-backups/phase7-20260912T044722Z`.
ELBOT, the voice bridge and panel are active; Discord connected and commands
synced. The Lavalink startup handshake succeeded, and a subsequent heartbeat
reported both Discord and music ready. Phase 7 behavior still requires
a live check. The Pi lacks pytest,
so automated test counts above are local, not Pi test results.

## Policy

Music mutations from voice, slash commands, and controller buttons use the same
execution service. It fetches the current Discord member, checks current voice
membership, and checks permissions immediately before acting. Song resolution
is followed by another check. Missing identity or failed verification denies
the action. Administrators must also be in ELBOT's voice channel.

The music session owner is the person who first connects music for that guild;
ownership lasts until disconnect. DJ roles are configured with comma-separated
Discord role IDs in `ELBOT_DJ_ROLE_IDS` (empty by default). Administrator or
Manage Server permission also grants DJ control. `/live stop` is available to
the Live starter, music owner, or DJ/admin in the same voice channel.
`/listen stop` cannot bypass this check while Live is active.

| Action | Listener | Owner / DJ / admin |
| --- | --- | --- |
| Request song, read queue/current track | Yes | Yes |
| Pause/resume | Yes | Yes |
| Vote skip | Yes | Yes |
| Direct skip | No | Yes |
| Volume | 0–100, at most 20 points per action | 0–200 |
| Remove requests | Own only | Any |
| Clear, stop music, disconnect | No | Confirmation required |
| Play next, replay, seek, loop, autoplay, fair mode | No | Yes |
| Shuffle / move | No | Yes, with fair mode off |

`/voteskip`, the Vote skip button, and the dislike button all cast one vote per
human per current track. A strict majority of current human listeners is needed.
Departed members' votes are removed when votes are evaluated; bots do not count.

Clear/stop/disconnect produce a requester-bound Discord Confirm/Cancel view.
Voice confirmations appear in the music text channel. The view expires after
30 seconds, is single-use, and rejects changed queue/current-track snapshots,
expired voice sessions, lost permissions, and other users' clicks. A new
confirmation replaces the previous one for that user. The model cannot supply
a confirmation flag or approve its own action.

## Fairness and voice attribution

Fair mode is on by default. Pending songs rotate among requesters while retaining
each person's song order. Owners/DJs may explicitly prioritize a request with
`play_next`; retries also retain priority. Queue displays use playback order.
`/fairqueue enabled:false` allows a DJ to restore manual FIFO ordering.

All users, including DJs, default to three song requests per rolling minute
and three pending songs. Configure `ELBOT_REQUESTS_PER_MINUTE` and
`ELBOT_MAX_QUEUED_PER_USER` to change these positive limits. Failed song
resolution counts toward the minute limit. Limits are in memory per music
session and reset on disconnect/restart.

Each GPT-Live connection binds an immutable Discord requester. Switching
speakers closes the previous API session and creates a new one; reconnects also
invalidate old callbacks. This adds connection latency and changes conversational
continuity when people take turns; existing per-user memory and spend caps remain.
Delayed actions cannot inherit another speaker's identity or the bot's identity.
Approved tool schemas are checked locally, including strict types, extra fields,
required fields, and size/range limits. Calls are grouped by delegated response;
failed/incomplete responses do not execute actions and repeated call IDs do not
execute twice. Logs record action/user metadata without raw tool arguments.

## Discord acceptance checklist

1. Have an owner and ordinary listener join; start music and `/live start`.
2. Listener: say “ELBOT, skip this.” Expect denial. Owner: repeat; expect one skip.
3. Both add several songs. Inspect `/queue` and actual playback for rotation;
   a fourth pending request or fourth request within a minute must be denied.
4. Vote using `/voteskip` and the button. Two humans require two votes; repeat
   clicks from one user must not add votes or skip the following track.
5. Listener cannot remove the owner's song or clear the queue. Owner asks to
   clear: no removal until clicking Confirm. Test Cancel, expiry, another user's
   click, and changing the queue before confirming.
6. Remove a DJ role or leave voice while a song resolves or confirmation is
   pending; execution must be denied. Test ordinary volume limits too.
7. Alternate speakers while requesting songs. Confirm requester labels and
   permission decisions remain correct and old delayed actions are rejected.
8. Verify `/live stop` denies an ordinary listener, then verify owner stop.

These live checks are needed before marking the battle plan's Phase 7 exit test
passed. Automated tests alone do not establish Discord audio or UI behavior.
