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
2. The bot replies **"working on it..."** straight away and shows the typing
   indicator. Download plus a re-encode can run most of a minute, and silence
   for that long is indistinguishable from the bot ignoring you.
3. It downloads the video, normalises it to H.264 mp4 if it isn't already, and
   puts the bytes in `bot/store.py` under `sha256(url)[:12]`.
4. It replies with `https://your.domain/d/{id}` - a bare URL, because wrapping
   it in `<>` or markdown suppresses the embed, which is the one thing needed -
   then deletes the note.

   The link is a **new message** rather than an edit of the note, because a
   fresh message is reliably unfurled. On failure the note is edited to say so
   instead of vanishing.

   `ACK_MODE` picks how it acknowledges: `message` (default), `reaction` (an
   emoji on the original, which leaves nothing behind but needs Add Reactions
   and falls back to a message without it), or `none`.

   Note what this does *not* fix: the player appears when Discord finishes
   unfurling, which is after our message lands, so for a moment the reply is a
   bare link. A bot cannot set an embed's `video` field, so there is no way to
   post the player pre-attached. Only an attachment upload avoids the gap, and
   that is capped by the guild's `filesize_limit`.
5. Discord fetches that page, reads its `og:` tags, then fetches `/d/{id}/f`
   with byte-range requests to build the player.
6. `LINK_TTL_SECONDS` later (300 by default) the item is evicted and the link
   404s.

### The assumption this rests on

Step 6 is a bet: that once Discord has unfurled a link it holds its own copy,
so the link may expire. **If Discord instead proxies each view from the origin,
an expired item means a dead embed in channel history.**

That is cheap to settle once the bot is running: post a link, wait past the
TTL, then try to play the embed from another account. If it still plays,
Discord cached it. If it doesn't, raise `LINK_TTL_SECONDS` - or set it high
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
**token**. That token is the only required setting; The prompt
masks what you type as `*`, so a pasted token is visibly there. `deploy.sh`
asks for it once
- including on a `make up` of an existing install - and an empty token means
the bot never starts and nothing else about it needs configuring. You won't be
asked again either way; `--bot-token TOKEN` and `--no-bot` set it without a
prompt.

### 2. Enable the message content intent

On the bot's page, under **Privileged Gateway Intents**, turn on **Message
Content Intent**.

Without it every message arrives with empty content and the bot sees no links
at all - it will connect, log in, and do nothing. The intent is free to toggle
for a personal bot; past roughly 100 servers Discord requires review.

### 3. Invite it with no permissions at all

On the application's **Installation** page set the install link to **Discord
Provided Link**, and under **Default Install Settings → Guild Install** set
scope `bot` and **no permissions**. Then the invite is just:

```
https://discord.com/oauth2/authorize?client_id={APP_ID}
```

Discord reads the scopes from what you saved, so there is one place to change
this later rather than a URL to rebuild.

**What this does and does not do.** It grants the bot no permissions of its
own - but a bot is a server member, so it still inherits `@everyone`, which on
most servers allows View Channels, Send Messages, Read Message History and
Embed Links. Install permissions of `0` means "nothing extra", not "nothing".

So the bot can usually still *see* your channels. What stops it *acting* is
that `opted_in` ignores both role-level permissions and `@everyone` overwrites:
only an explicit overwrite naming the bot or its own role counts, and that is
never there by default.

If you want it genuinely unable to see the rest of the server, **deny** View
Channels to the bot's role at the category level and allow it on the channels
you want - Discord has no per-bot visibility switch, so denial has to be an
overwrite. None of this changes the code.

Granting View Channels / Send Messages / Read Message History in the install
settings instead is the other direction: it makes the bot able to work anywhere
and leaves Embed Links as the only per-channel tick.

### 4. Check the public URL

The reply links to `{BASE_URL}/d/{id}`, so `BASE_URL` has to be what the
outside world actually reaches, scheme included. Since `/d/` is served by the
same app on the same port as everything else, a reverse proxy that already
fronts gamtube needs no new routing.

---

## Opting a channel in

Since the bot holds no server-wide permissions, a channel is enabled by giving
it everything it needs right there:

> Channel → **Edit Channel** → **Permissions** → add the bot (or its role) →
> tick all four → save.

| Permission | Why |
|---|---|
| View Channels | see the channel exists at all |
| Send Messages | post the reply |
| Read Message History | re-read the message after Discord unfurls it |
| **Embed Links** | the opt-in marker, and what makes the reply unfurl |

Removing them opts the channel back out. There is no command and no stored
state: Discord is the record, so it survives restarts and redeploys, and there
is nothing to keep in sync.

`Embed Links` is the one the bot actually tests for. Tick it without
`Send Messages` and the channel looks enabled but can't be replied in - so the
startup log warns about exactly that:

```
WARN  gamtube.bot  my-server: #clips is opted in but the bot lacks send_messages there; it will not be able to reply
```

A channel missing `View Channels` won't appear in the log at all, because the
bot can't see it.

It is also the permission the bot genuinely needs - without Embed Links its
reply posts as plain text and never unfurls - so the marker and the capability
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
when a guild has none - an opt-in bot with nothing granted looks identical to a
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
   `WATCH_DOMAINS` (default list in `bot/detect.py`) - a cheap prefilter so
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

- `MAX_ITEM_MB` (100) - skip any single video bigger than this.
- `MAX_MEMORY_MB` (512) - total across all live links; oldest evicted first
  when over, regardless of TTL.

With a 300-second TTL the store only ever holds the last few minutes of
activity, so the steady state sits far below the budget. Eviction is there for
the pathological case, not the normal one.

---

## Who gets what

Discord uses **two separate services** against these links, and the difference
is the whole design of the gating:

| Fetches | Which service | User agent |
|---|---|---|
| `/d/{id}` - the page, for `og:` tags | the unfurler | `Discordbot/...` |
| `/d/{id}/f` - the video bytes | the media proxy | **not assumed here** |

### The page gate (on by default)

`HUMAN_NOTICE=true` serves a short "this one's for Discord" page, with a link
to the original, to anything that doesn't look like a link crawler. Only a
crawler needs the `og:` tags, so there is no downside: embeds keep working and
a person who clicks the link gets an explanation instead of a bare video.

The crawler list is `bot/agents.py` `DEFAULT_CRAWLERS`, overridable with
`CRAWLER_USER_AGENTS`. A missing or empty user agent counts as a crawler, so a
chat app that stops identifying itself doesn't silently lose its embeds.

### The video gate (off by default)

`MEDIA_USER_AGENTS` restricts who may fetch the bytes. **It is deliberately
not the crawler list.** Discord's media proxy is a different service from its
unfurler, so putting `discordbot` here would most likely block the fetcher that
actually retrieves your video and break every embed.

So don't guess it - measure it. Every media fetch is logged:

```
INFO gamtube.bot  media abc123def456 GET range=bytes=0- ua='...' from 10.0.0.5
```

Run the bot with the gate empty, post a link, then look at what actually hit
the media route:

```bash
grep 'gamtube.bot  media' /opt/gamtube/logs/app.log
```

That tells you two things at once: what to put in `MEDIA_USER_AGENTS`, and
whether Discord fetches once or once per viewer - which is the same question
the TTL rests on. If you see one fetch and nothing more while several people
watch, Discord cached it.

Neither gate is a security boundary; anything can claim to be Discord. They are
there to give people a friendlier page and keep casual traffic off the video.

---

## Logging while you tune it

Turn `BOT_DEBUG=true` on for the first while. It adds full request headers on
both `/d/` routes, per-stage timings, and - instead of one blind sleep - polls
the message during the wait so the log says when the embed *actually* arrived.
It costs a few extra Discord API calls per message, so turn it off once the
numbers stop surprising you.

At `INFO`, without debug, you still get the whole decision trail:

```
message 1234 in #clips has 1 candidate(s): https://www.tiktok.com/@u/video/1
after 3.0s discord produced: [type='link' url='https://...' video=None thumb=True title='...']
1 link(s) need rehosting
download https://... in 2.4s -> .mp4 3.1MB (extractor=TikTok)
prepared https://... : 3.1MB 576x1024 14s total 2.5s
posted https://... as https://tube.4tw.pw/d/abc123def456 (3.1MB, 576x1024, held 300s, 3.0s end to end; store now 1 item(s)/3.1MB)
page abc123def456 crawler=True ua='Mozilla/5.0 (compatible; Discordbot/2.0...)' from 10.0.0.5
media abc123def456 GET fetch=1 range=bytes=0- ua='...' from 10.0.0.5
media abc123def456 serving all 3.1MB
store evict abc123def456 (ttl) after 1 fetch(es), 3.1MB
```

Four questions that log answers, in order of how much they matter:

1. **`after Ns discord produced:`** - what Discord actually built. If a TikTok
   card comes back with `video=None`, the trigger is right; if it has a video
   URL, the bot shouldn't have acted and `EMBED_WAIT_SECONDS` or the predicate
   needs a look.
2. **`media ... fetch=N ua=... from ...`** - who fetches the video, and how
   often. One fetch while several people watch means Discord cached it; one per
   viewer means it proxies, and then both the TTL and `MEDIA_USER_AGENTS` need
   rethinking. This also hands you the UA to put in that list.
3. **`store evict ... after N fetch(es)`** - whether items die before anyone
   fetched them (`after 0 fetch(es)` means the TTL is too short, or Discord
   never came).
4. **the timings** - `download`, `shrink`, `normalise` and the end-to-end
   figure, which tell you whether the reply lands while the message is still
   on screen.

Nothing here is written anywhere but the log, and the log is `app.log`:

```bash
make bot-logs                                        # live, filtered to the bot
grep 'gamtube.bot  media' /opt/gamtube/logs/app.log  # just the video fetches
grep 'discord produced' /opt/gamtube/logs/app.log    # just the embed verdicts
```

---

## What it deliberately does not do

- **Remove the original dead embed.** A bot cannot edit someone else's
  message. You get the working video *plus* their broken card, unless you give
  it Manage Messages and let it delete and repost on the user's behalf - a much
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
