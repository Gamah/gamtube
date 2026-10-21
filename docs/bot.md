# The Discord bot

Discord gives TikTok, Instagram, Facebook and Reddit links a dead link-card
with nothing to play. The bot watches for those links, downloads the video with
yt-dlp, holds it in memory for a few minutes, and replies with a URL that
Discord unfurls into a working player.

It runs **inside the web app's process**, and `/d/{id}` is its route. That is
not an aesthetic choice: the video lives in this process's RAM, and a separate
process could not serve it. Nothing is written to the database, the storage
backend or the `videos` table, and nothing touches disk beyond the temp file
yt-dlp downloads into.

---

## The lifecycle

1. Someone posts a TikTok link. Discord renders a useless card.
2. The bot downloads the video, normalises it to H.264 mp4 if it isn't
   already, and puts the bytes in `bot/store.py` under `sha256(url)[:12]`.
3. It replies with `https://your.domain/d/{id}` — a bare URL, because wrapping
   it in `<>` or markdown suppresses the embed, which is the one thing needed.
4. Discord fetches that page, reads its `og:` tags, then fetches `/d/{id}/f`
   with byte-range requests to build the player.
5. `LINK_TTL_SECONDS` later (300 by default) the item is evicted and the link
   404s.

### The assumption this rests on

Step 5 is a bet: that once Discord has unfurled a link it holds its own copy,
so the link may expire. **If Discord instead proxies each view from the origin,
an expired item means a dead embed in channel history.**

That is cheap to settle once the bot is running: post a link, wait past the
TTL, then try to play the embed from another account. If it still plays,
Discord cached it. If it doesn't, raise `LINK_TTL_SECONDS` — or set it high
enough that it stops mattering.

### Why not an attachment

Uploading the file as an attachment would make Discord the host outright, with
no link and no TTL question at all. The catch is the attachment cap: Discord
takes up to the guild's `filesize_limit`, which is small without boosts. The
link route has no such ceiling, at the cost of the bet above.

---

## Setup

### 1. Create the application

In the Discord developer portal: **New Application** → **Bot** → copy the
**token**. That token is the only required setting; `deploy.sh` prompts for it,
and an empty token means the bot simply never starts.

### 2. Enable the message content intent

On the bot's page, under **Privileged Gateway Intents**, turn on **Message
Content Intent**.

Without it every message arrives with empty content and the bot sees no links
at all — it will connect, log in, and do nothing. The intent is free to toggle
for a personal bot; past roughly 100 servers Discord requires review.

### 3. Invite it

Generate an invite with the **`bot`** scope and these permissions:

| Permission | Why |
|---|---|
| View Channels | see the message |
| Send Messages | post the reply |
| Read Message History | re-read the message after Discord unfurls it |

Read Message History is not optional — the bot re-fetches the message to find
out what Discord managed to embed.

**Do not grant Embed Links here.** That one is granted per channel, and it is
what opts a channel in.

### 4. Check the public URL

The reply links to `{BASE_URL}/d/{id}`, so `BASE_URL` has to be what the
outside world actually reaches, scheme included. Since `/d/` is served by the
same app on the same port as everything else, a reverse proxy that already
fronts gamtube needs no new routing.

---

## Opting a channel in

The bot acts only in channels that **explicitly grant it Embed Links** as a
channel permission overwrite:

> Channel → **Edit Channel** → **Permissions** → add the bot's role (or the bot
> itself) → tick **Embed Links** → save.

Removing that overwrite opts the channel back out. There is no command and no
stored state: Discord is the record, so it survives restarts and redeploys, and
there is nothing to keep in sync.

It is also the permission the bot genuinely needs — without Embed Links its
reply posts as plain text and never unfurls — so the marker and the capability
cannot disagree.

What does **not** count as opting in:

- **Role-level Embed Links from the invite.** Base role permissions aren't
  overwrites. This is why step 3 says to leave it out of the invite.
- **An `@everyone` overwrite.** `embed_links` is commonly allowed for
  `@everyone`, and honouring it would opt in every channel on the server at
  once, so it is ignored.
- **Inheriting from a category.** Only an overwrite on the channel itself (or,
  for a thread, on its parent channel) counts.

Threads have no permissions of their own, so a thread follows its parent.

On connect the bot logs which channels are enabled in each guild, and warns
when a guild has none — an opt-in bot with nothing granted looks identical to a
broken one, so check the log first:

```
INFO  gamtube.bot  my-server: enabled in clips, memes
WARN  gamtube.bot  other-server: no channel grants embed_links to the bot, so it will do nothing here
```

Set `CHANNEL_OPT_IN=false` to drop the requirement and act anywhere
`DISCORD_GUILDS` / `DISCORD_CHANNELS` permit. `OPT_IN_PERMISSION` changes which
permission is the marker; it is validated against Discord's permission names at
startup, so a typo is logged and the bot doesn't start rather than silently
doing nothing.

---

## How it decides to act

1. A message arrives in an opted-in channel. Links are matched against
   `WATCH_DOMAINS` (default list in `bot/detect.py`) — a cheap prefilter so
   unrelated links never cost a yt-dlp call.
2. Links wrapped as `<https://...>` are skipped. That syntax is how a user
   deliberately suppresses an embed, so the bot honours it.
3. The bot waits `EMBED_WAIT_SECONDS`, then **re-reads the message**. Embeds
   arrive asynchronously, so the copy delivered to the event handler never has
   them yet.
4. For each link, it checks whether Discord produced an embed *with a playable
   video*. This is judged per link, not per message: a message holding both a
   YouTube link and a TikTok link still gets the TikTok re-hosted.
5. Anything left over is downloaded and posted, up to
   `MAX_LINKS_PER_MESSAGE`.

The format selector prefers H.264/AAC under `MAX_ITEM_MB`, so the common case
needs no ffmpeg at all. Over that, `SHRINK_ENABLED` buys one re-encode attempt
at `SHRINK_CRF` / `SHRINK_MAX_HEIGHT`; still over, and the link is skipped
quietly. `FORCE_MP4` re-encodes anything that arrives in another container,
because mp4/H.264 is what Discord's player handles everywhere, iOS included.

`og:video:width` and `og:video:height` are emitted from an ffprobe of the final
file. They are not decorative: without them Discord sizes the player badly or
degrades the embed to a plain card, which is the exact failure this bot exists
to fix.

---

## Memory

Everything is held in RAM, so there are two budgets:

- `MAX_ITEM_MB` (100) — skip any single video bigger than this.
- `MAX_MEMORY_MB` (512) — total across all live links; oldest evicted first
  when over, regardless of TTL.

With a 300-second TTL the store only ever holds the last few minutes of
activity, so the steady state sits far below the budget. Eviction is there for
the pathological case, not the normal one.

`SERVE_USER_AGENTS` optionally restricts who may fetch the bytes to a list of
user-agent substrings. It is a heuristic — Discord's proxy identifies itself, a
human clicking the link does not — so the default is empty and serves anyone.

---

## What it deliberately does not do

- **Remove the original dead embed.** A bot cannot edit someone else's
  message. You get the working video *plus* their broken card, unless you give
  it Manage Messages and let it delete and repost on the user's behalf — a much
  more intrusive bot, and not what this is.
- **Survive a restart.** The store is memory only, so every link the bot has
  posted dies when the service restarts. Given the short TTL that is usually
  indistinguishable from normal expiry.
- **Handle links inside code blocks or spoiler tags.** They are treated like
  any other text.

---

## Operations

The bot has no service of its own. It starts and stops with `gamtube`, logs
under the `gamtube.bot` logger, and `run_client` swallows its own failures, so
a bad token or a gateway problem degrades to "no bot" rather than taking the
web app down with it.

```bash
systemctl status gamtube
make bot-logs      # tails app.log filtered to gamtube.bot
```

`gamtube-update-ytdlp.timer` restarts `gamtube` whenever yt-dlp actually moves,
which picks up the new extractor set for the bot at the same time.
