# NEXT

Two independent explorations off the `bot` branch. Each is self-contained: they
touch different files and can be worked in parallel.

Read `CLAUDE.md` and `docs/bot.md` first. `make test` runs everything that can
run without a Discord token: pure functions, the `/d/` routes through
FastAPI's TestClient, and `deploy.sh` end to end against stubbed commands.
discord.py is pip-installable locally, so API surface gets checked by
introspection rather than from memory.

---

## 1. The bot should see nothing until it is opted in

**Goal.** Adding the bot to a server does nothing at all. It cannot see any
channel, receives no message content, and only becomes able to act where
someone deliberately opts it in.

**Where this stands.** The install link already grants no permissions, but that
is not the same as blind: a bot is a server member and inherits `@everyone`,
which on most servers allows View Channels, Send Messages, Read Message History
and Embed Links. So today the bot can see most channels and simply declines to
act in them - `scope.opted_in` counts neither role permissions nor `@everyone`
overwrites, so only an explicit per-channel overwrite enables it. The gate is
correct; the visibility is not.

Discord has no per-bot visibility switch. Making it genuinely blind means View
Channels being **denied** to the bot's own role, as an overwrite, everywhere it
hasn't been opted into. Categories are the practical unit, since channels
inherit from them.

**Shape of the work.**

- Establish what a bot can do to its own role's overwrites, and with which
  permission. Verify against the live API, not memory: can it write a deny
  overwrite for itself on a category it can currently see, and what does that
  require (Manage Roles, Manage Channels, both, and does Discord's rule about
  only granting permissions you hold apply to denies)?
- Decide between the two shapes this can take, and say which and why:
  - **self-denying on join** - on `on_guild_join`, write a View Channels deny
    for the bot's role on every category and top-level channel. Needs a real
    permission in the invite, which is a cost worth stating plainly.
  - **documented manual setup** - the admin denies View Channels to the bot's
    role at the category level once, then allows per channel. No new
    permission, more setup, easy to get wrong.
- Either way, make the startup log say what the bot can actually see, so a
  half-done setup is obvious. `scope.missing_capabilities` already reports
  effective permissions per opted-in channel; the counterpart is a count of
  channels it can see but is not opted into, which is the number that should be
  zero.
- `bot/scope.py` is pure and must stay that way; it is the only part of this
  that can be tested here. Anything touching discord.py objects goes in
  `bot/client.py` and gets careful review instead of tests.

**Acceptance.** A fresh install in a server with default `@everyone`
permissions sees zero channels, logs that it sees zero, and acts only where
opted in. Documented in `docs/bot.md` with whatever the admin has to do.

**Open question for Gamah** - is the bot allowed to hold Manage Roles or Manage
Channels so it can deny itself on join? That decides whether this is code or
documentation.

---

## 2. Upload the file when allowed, fall back to the link

**Goal.** Prefer a native attachment, because the player is then present the
instant the message lands - no unfurl, no cache question, no TTL, and the file
ends up on Discord's CDN permanently. Fall back to the current link path on any
rejection, so the feature never gets worse than it is today.

**Why it is worth it.** Everything awkward about the link path exists because
Discord has to come and fetch the video: the player arrives late, the item has
to stay in memory long enough, and whether old embeds keep working is still an
open question. An upload removes all of it. The cost is the size cap, which is
the guild's `filesize_limit` and comfortably fits short-form clips.

**Shape of the work.**

- Try the upload first when the file is under `guild.filesize_limit`, which
  already accounts for boosts. Don't attempt it when we know it won't fit.
- Fall back to the link on **any** failure: `discord.Forbidden` (no Attach
  Files), HTTP 413, 429 rate limiting, or anything else. The fallback must be
  total - a failed upload must never leave the user with nothing.
- Signal the fallback with a distinct reaction (a thinking face) instead of the
  check mark, so a viewer can tell the good path didn't run and Gamah can tell
  the bot is being throttled. The reason goes in the log either way.
- An uploaded video needs no store entry, no `/d/` route, no TTL and no
  warmup, so the store `put` should only happen on the fallback path.
- `core.titles.safe_filename` was removed in `abe8a76` and needs restoring:
  Discord shows the attachment filename above the player, and a raw yt-dlp
  title contains slashes, emoji and occasionally hundreds of characters. Its
  tests came out in the same commit.
- `handler._prepare` already returns the bytes, so the upload is
  `discord.File(io.BytesIO(item.data), filename=...)`.
- Rate limits are the interesting failure. discord.py handles 429 by waiting,
  so a naive call blocks rather than raising. Work out what it actually does on
  an attachment upload and whether a timeout is needed to make the fallback
  fire promptly rather than after a long sleep.

**Acceptance.** A clip under the cap arrives as a playable attachment with no
`/d/` link and no store entry. A clip over the cap, or an upload that is
refused or throttled, arrives as the link exactly as it does today, with the
thinking reaction and a log line naming the reason. `make test` still passes.

**Open question for Gamah** - one fallback emoji for every reason, or distinct
ones for no-permission versus rate-limited versus too-large?

---

## Conventions

- Branch per goal off `bot`; push the branch, never open a PR or merge.
- No em dashes anywhere, including comments and commit messages.
- No AI attribution in commits.
- A tunable belongs in `bot/config.py` as a code default, never in the `.env`
  heredoc in `deploy.sh`. A key written to `.env` overrides its default forever
  and later default changes silently never reach a deployment.
- Never add a behaviour that messages a real person, posts in a channel nobody
  asked for, or otherwise has a visible side effect that was not requested. An
  earlier warmup default DMed the application owner on every link; it was
  removed for that reason.
