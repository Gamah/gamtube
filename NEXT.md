# NEXT

Two independent explorations off the `bot` branch. They touch different files
and can be worked one per branch, in either order.

## Before starting

Read `CLAUDE.md` and `docs/bot.md`. The short version of what the bot does:
someone posts a TikTok or Instagram link, Discord renders a card with nothing
to play, the bot downloads the video with yt-dlp, holds it in memory and
replies with a `/d/{id}` link that Discord unfurls into a working player.

`make test` runs everything that can run without a Discord token:

- `tests/test_pure.py` - link detection, channel opt-in, the memory store,
  Range parsing, title cleanup. Pure stdlib, no deps at all.
- `tests/test_routes.py` - the `/d/` routes through FastAPI's TestClient.
  Skips cleanly when fastapi isn't installed.
- `tests/test_deploy.sh` - `deploy.sh` end to end against stubbed `apt-get`,
  `systemctl`, `sudo` and friends.

`python3 -m venv` has no ensurepip on this host; use `pip install --target` into
a scratch directory plus `PYTHONPATH`. discord.py installs that way, so API
surface gets confirmed by introspection rather than from memory - that is how
`Thread.overwrites_for` turning out not to exist was caught, and how permission
integers should be derived (`Permissions(...).value`).

Nothing here can talk to Discord. The gateway paths ship reviewed, not tested,
and get verified on Gamah's server.

---

## 1. The bot should see nothing until it is opted in

**Goal.** Adding the bot to a server does nothing at all. It cannot see any
channel, receives no message content, and only becomes able to act where
someone deliberately opts it in.

**Decided: the bot holds no elevated permissions, ever.** It is a servant of
the community and runs nothing. So the rejected approach is the bot writing its
own deny overwrites on `on_guild_join`, which would need Manage Roles or Manage
Channels. Don't build that, and don't propose it again - a bot whose pitch is
"it cannot see anything" must not be asking to manage the server.

That makes this mostly a documentation and reporting job.

**Where this stands.** The install link already grants no permissions, but that
is not the same as blind: a bot is a server member and inherits `@everyone`,
which on most servers allows View Channels, Send Messages, Read Message History
and Embed Links. Today the bot can see most channels and simply declines to act
in them - `scope.opted_in` counts neither role permissions nor `@everyone`
overwrites, so only an explicit per-channel overwrite enables it. **The gate is
correct; the visibility is not.**

Discord has no per-bot visibility switch. Blindness means View Channels being
*denied* to the bot's own role, as an overwrite, everywhere it has not been
opted into. Categories are the practical unit, since channels inherit from
them.

**The work.**

- Write the admin procedure in `docs/bot.md`: deny View Channels to the bot's
  role at the category level, then allow it per channel alongside the existing
  opt-in ticks. Include what it looks like when half-done, because that is the
  state people will actually hit.
- Make the startup log state what the bot can see, so a half-done setup is
  obvious rather than silent. `scope.missing_capabilities` already reports
  effective permissions for opted-in channels; the counterpart is **a count of
  channels the bot can see but is not opted into**, which is the number that
  should be zero. Log it per guild, and warn when it isn't zero.
- Consider whether the bot should refuse to start, or loudly warn, when it can
  see a lot of channels it has no business seeing. A warning is probably right;
  refusing to start punishes the wrong person.
- `bot/scope.py` is pure and must stay that way - it is the only part of this
  that can be tested here. Anything touching discord.py objects belongs in
  `bot/client.py`.

**Acceptance.** A fresh install in a server with default `@everyone`
permissions reports how many channels it can see. Following the documented
procedure brings that to zero while opted-in channels keep working. The
half-configured case produces a warning that names the problem.

---

## 2. Upload the file when allowed, fall back to the link

**Goal.** Prefer a native attachment, because the player is then present the
instant the message lands - no unfurl, no cache question, no TTL, and the file
ends up on Discord's CDN permanently. Fall back to the current link path on any
rejection, so the feature never gets worse than it is today.

**Why it is worth it.** Everything awkward about the link path exists because
Discord has to come and fetch the video: the player arrives late, the item has
to stay in memory long enough, and whether old embeds keep working is still
unresolved - some links keep playing well past the TTL and some do not, and the
working theory is whether Discord pulled the whole file before we dropped it.
An upload removes all of that. The cost is the size cap, which is the guild's
`filesize_limit` and comfortably fits short-form clips.

Note this **shrinks** the codebase rather than adding to it. An uploaded video
needs no store entry, no `/d/` route, no TTL and no warmup. If uploads cover
most real clips, the link path becomes the rare fallback and a fair amount of
what exists today goes cold.

**The work.**

- Try the upload when the file is under `guild.filesize_limit`, which already
  accounts for boosts. Don't attempt it when we know it won't fit.
- Fall back to the link on **any** failure: `discord.Forbidden` (no Attach
  Files), HTTP 413, 429 throttling, anything else. The fallback must be total -
  a failed upload must never leave the user with nothing.
- The store `put` should only happen on the fallback path.
- `core.titles.safe_filename` was removed in `abe8a76` along with its tests and
  needs restoring: Discord shows the attachment filename above the player, and
  a raw yt-dlp title contains slashes, emoji and occasionally hundreds of
  characters.
- `handler._prepare` already returns the bytes, so the upload itself is
  `discord.File(io.BytesIO(item.data), filename=...)`.
- Rate limits are the interesting failure. discord.py handles 429 by sleeping
  rather than raising, so a naive call blocks instead of falling back. Work out
  what it actually does on an attachment upload and whether a timeout is needed
  to make the fallback fire promptly.

**Decided: a distinct emoji per outcome**, so the GitHub docs can explain what
each one means and Gamah can tell throttling apart from a permission problem at
a glance. Which means the reaction is now two separate things, and that needs
sorting out:

- a **start** marker, added when work begins. Currently this is the check mark
  with `ACK_KEEP_REACTION`, which no longer works if the check means "uploaded
  natively". Pick something neutral for in-progress and swap it for the outcome.
- an **outcome** marker, one per state. At least: uploaded natively, fell back
  to a link because Attach Files is missing, fell back because of throttling,
  fell back because the file is over the cap, and failed outright. A thinking
  face was Gamah's suggestion for the generic fallback.

Every emoji stays a code default in `bot/config.py`, and the set gets a table in
both `docs/bot.md` and `README.md` so a viewer who sees one can look it up.

**Acceptance.** A clip under the cap arrives as a playable attachment with no
`/d/` link and no store entry. A clip over the cap, or an upload that is refused
or throttled, arrives as the link exactly as it does today, carrying the emoji
for that specific reason, with a log line naming it. `make test` still passes.

---

## Conventions

- Branch per goal off `bot`; push the branch, never open a PR or merge.
- No em dashes anywhere, including comments and commit messages.
- No AI attribution of any kind in commits.
- A tunable belongs in `bot/config.py` as a code default, **never** in the
  `.env` heredoc in `deploy.sh`. A key written to `.env` overrides its default
  forever, so later default changes silently never reach a deployment. This
  caused real confusion during development with `ACK_MODE`; the deploy sandbox
  test now fails if a tunable gets pinned again.
- Never add a behaviour with a visible side effect nobody asked for. An earlier
  warmup default DMed the application owner on every single link, which is why
  warming now requires an explicitly named channel and has no implicit target.
- `deploy.sh` rsyncs with `--delete`, so every runtime path needs an explicit
  exclude. `logs` and `.env` are already excluded; adding a new runtime
  directory means adding one.
