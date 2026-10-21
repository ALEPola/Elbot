# Elbot security maintenance

Moderation slash commands enforce application-command checks at execution time,
advertise the required Discord member permissions, reject DMs, check the bot's
permissions, and enforce both the requester's and the bot's role hierarchy.
Message deletion is limited to 1–100 messages per request. Discord-side command
overrides supplement these checks; they must never replace them.

Submitted media links must use HTTPS on YouTube, SoundCloud, Spotify, Bandcamp,
or Vimeo. Titles and supported search prefixes remain available. This limits
untrusted URLs before Lavalink and yt-dlp are called; it is not a complete
network egress firewall. Extractor-generated CDN requests and redirects still
need backend/network isolation for complete protection against internal access.

## Tested dependency set

`requirements-pi.lock` pins the production dependency closure tested on Linux
with Python 3.11. Install with `python -m pip install -c requirements-pi.lock .`.
For tests use `python -m pip install -c requirements-pi.lock '.[test]'`.
Refresh the lock in an isolated environment, run the full test suite and an
advisory audit, and review the resulting versions before updating production.
The separate Node voice transport also requires its committed pnpm lock.
Its WebSocket dependency is updated, and a tar 7.x override patches the archive
extraction dependency of the native Opus installer. The pinned pnpm 10 release
supports the Pi's 32-bit ARM environment. Install with the frozen lock before
testing/deploying; native Opus builds remain explicitly allowlisted.

The Linux audit excludes only PYSEC-2026-3447 / GHSA-h35f-9h28-mq5c
(CVE-2026-59890): setuptools' source-distribution exclusion matching on macOS
APFS/HFS+. The Pi is Linux and does not build source distributions for macOS.
Nextcord 2.6 requires `pkg_resources`, which setuptools 81 removed, so runtime
setuptools stays on the patched 80.x line. Do not expand this exception to
other setuptools findings or macOS packaging. Remove it after a tested
Nextcord/Python migration permits setuptools 83+.
Source: https://osv.dev/vulnerability/PYSEC-2026-3447

## Service isolation

Deployment uses strict SSH host-key verification. Configure verified public
host-key entries in the deployment account's known_hosts or the
`DEPLOY_KNOWN_HOSTS` Actions secret, using an out-of-band fingerprint check.
The old `DEPLOY_INCLUDE_ENV=1` path is rejected: deployments always exclude
environment files, runtime data and cookie files. Existing secrets stay on the
host. CI deployment now fails closed until the verified host-key input exists.
Local systemd deployments use the application venv and the tested lock rather
than installing into system Python. `ffmpeg` remains an OS prerequisite, not
an unrelated package from PyPI.

The supplied `/opt/elbot` units target a dedicated, non-login `elbot` account,
with no membership in administrative or Docker groups. Keep that account
separate from MotoBrief and the SSH/deployment account. Provision file access
before installing units; the templates are not a migration script.

For an existing `/home/alex/Elbot` installation, keep secrets and runtime data
in place. Inventory cookie paths, Lavalink state, logs and chat_history without
printing their contents. Grant only the required account traversal and access,
edit unit paths, and use `ProtectHome=read-only` for those home-based paths.
The bot needs writable logs, chat_history, and its configured Lavalink runtime
directory. The panel/update job also needs repository write access for its
documented administrative actions; the bot can additionally mount code read-only.

The panel must retain only the six commands in `infra/sudoers.d/elbot-panel`;
do not give its account general passwordless sudo. Its scoped sudo mechanism
is incompatible with `NoNewPrivileges=true`. The bot itself can and should
use that protection. Keep the panel bound to loopback behind authenticated
HTTPS, with CSRF protection. Never expose Flask/Gunicorn directly to the LAN.

Validate units with `systemd-analyze verify`. Activate accounts/units in a
maintenance window with a rollback route; test Discord readiness, voice join,
playback, moderation denials, panel login and service controls afterward.
Do not assume changing a unit template changes a running service.
