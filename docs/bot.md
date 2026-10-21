# The Discord bot

Discord gives TikTok, Instagram, Facebook and Reddit links a dead link-card
with nothing to play. The bot watches for those links, downloads the video with
yt-dlp, and replies with something that plays: a **native attachment** when the
file fits the guild's upload cap, and otherwise a URL it holds in memory for a
few minutes and lets Discord unfurl into a player.

It runs **inside the web app's process**, and `/d/{id}` is its route. That is
not an aesthetic choice: the video for a link lives in this process's RAM, and
a separate process could not serve it. Nothing is written to the database, the
storage backend or the `videos` table, and nothing touches disk beyond the temp
file yt-dlp downloads into.

---

## Attachment first, link second

An attachment is the better outcome in every way. Discord becomes the host, the
player is there the instant the message lands, and none of the link machinery
is involved: no store entry, no TTL, no warmup, no `/d/` route, no question
about whether old embeds keep working. So the bot attaches the file whenever it
fits the guild's `filesize_limit`, which is 10 MiB unboosted, 50 MiB at tier 2
and 100 MiB at tier 3, and already accounts for boosts. [SOURCE: discord.py
2.7.1 `Guild._PREMIUM_GUILD_LIMITS`]

Over the cap, the bot makes **one** shrink attempt aimed at it, and only when
fitting still leaves a watchable bitrate. `UPLOAD_MIN_BITRATE_KBPS` (900, video
and audio together) is checked against the clip's probed duration, so what the
cap really buys is a length:

| Guild cap | Clip length that still clears 900 kbps |
|---|---|
| 10 MiB (no boosts) | up to about 1 m 33 s |
| 50 MiB (tier 2) | up to about 7 m 46 s |
| 100 MiB (tier 3) | up to about 15 m 32 s |

A three minute clip inside a 10 MiB cap would have roughly 440 kbps for picture
and sound, which is not worth looking at - and the link path serves that same
clip untouched. So it goes out as a link, by design, not as a ruined
attachment.

Everything else falls back to the link too: no Attach Files in the channel,
Discord throttling the upload, a 413, or any other failure. **The fallback is
total.** A failed upload never leaves you with nothing, and the reaction says
which fallback it was.

Throttling is the one that needs a deadline rather than an exception handler.
discord.py answers a 429 by sleeping for the `retry_after` Discord gives it and
retrying, up to five times; it raises `RateLimited` only if the client was
built with `max_ratelimit_timeout` and the wait exceeds it, and never for a
global limit. [SOURCE: discord.py 2.7.1 `HTTPClient.request`] Left alone, a
throttled upload therefore blocks instead of falling back, so the bot gives one
attempt `UPLOAD_TIMEOUT_SECONDS` (45) and posts the link when that runs out.
`UPLOAD_ENABLED=false` turns the whole attachment path off.

---

## The lifecycle

1. Someone posts a TikTok link. Discord renders a useless card.
2. The bot reacts to the message with ⏳ straight away and shows the typing
   indicator. Download plus a re-encode can run most of a minute, and silence
   for that long is indistinguishable from the bot ignoring you.
3. It downloads the video and runs the compatibility gate over it (below).
4. If the file fits the guild's cap it is sent as an attachment, the ⏳ becomes
   ✅, and that is the end of it: no link, no store entry, nothing to expire.
5. Otherwise the bytes go into `bot/store.py` under `sha256(url)[:12]` and it
   replies with the link plus a short note:

   ```
   https://your.domain/d/abc123def456
   (clicking this link does nothing)
   ```

   The URL stays bare on its own line: Discord unfurls a plain URL in a
   message that has other text, but not one wrapped in `<>` or markdown, and
   the embed is the entire point. The note is there because the link plays
   inline but goes nowhere useful when clicked - set `LINK_NOTE=` to drop it.
   The "working on it" note is deleted at this point.

   The link is a **new message** rather than an edit of the note, because a
   fresh message is reliably unfurled. On failure the note is edited to say so
   instead of vanishing.

   `ACK_MODE` picks how it acknowledges: `reaction` (default, see the table
   below), `message`, or `none`. Reaction mode needs Add Reactions on the
   channel and **silently falls back to a message without it** - if you are
   seeing "working on it..." when you expected a reaction, that is why, and the
   log says so:

   ```
   INFO  gamtube.bot  no Add Reactions here; acknowledging with a message instead
   ```

   With `WARMUP_CHANNEL_ID` set, the link is warmed before this public post so
   the player arrives with the message rather than filling in a second later.
   See below.
6. Discord fetches that page, reads its `og:` tags, then fetches `/d/{id}/f`
   with byte-range requests to build the player.
7. `LINK_TTL_SECONDS` later (300 by default) the item is evicted and the link
   404s.

Steps 5 to 7 only happen on the link path. An attachment is finished at step 4.

### What the reaction means

In reaction mode there are two markers: ⏳ while the work runs, swapped at the
end for exactly one emoji that says how it ended. The outcome emoji **stays**
put - it is the "handled" marker, and the thing you look up here.

| Emoji | Meaning | What to do about it |
|---|---|---|
| ⏳ | downloading and converting | wait; it is replaced when the work ends |
| ✅ | attached natively | nothing, this is the good one |
| 📦 | fell back to a link: over the guild cap, or too long to fit it well | boost the server, or accept the link |
| 🔒 | fell back to a link: no **Attach Files** in this channel | tick Attach Files on the channel overwrite |
| 🐌 | fell back to a link: Discord throttled or stalled the upload | nothing; it is transient |
| 🤔 | fell back to a link: the upload failed some other way | check the log, it names the HTTP status |
| ❌ | nothing was posted at all | check the log; usually yt-dlp could not fetch it |

Every one of them is a code default in `bot/config.py` (`EMOJI_WORKING`,
`EMOJI_UPLOADED`, `EMOJI_OVERSIZE`, `EMOJI_FORBIDDEN`, `EMOJI_THROTTLED`,
`EMOJI_FALLBACK`, `EMOJI_FAILED`), and each outcome gets a log line naming the
reason. `ACK_OUTCOME_REACTION=false` clears ⏳ at the end and adds nothing,
for a bot that leaves no trail. It replaces the old `ACK_KEEP_REACTION`, which
cannot mean anything now that ✅ specifically means "attached natively".

### The assumption this rests on

Step 7 is a bet: that once Discord has unfurled a link it holds its own copy,
so the link may expire. **If Discord instead proxies each view from the origin,
an expired item means a dead embed in channel history.**

That is cheap to settle once the bot is running: post a link, wait past the
TTL, then try to play the embed from another account. If it still plays,
Discord cached it. If it doesn't, raise `LINK_TTL_SECONDS` - or set it high
enough that it stops mattering.

### Warming the link

Posting a link is what makes Discord fetch it, so left alone the player can
only ever appear *after* the message: the reply sits there as a bare URL until
Discord has read the page, pulled the video and pushed the embed, and clients
sometimes don't re-render until you revisit the channel. There is no way to
pre-attach it, because a bot cannot set an embed's `video` field.

So when `WARMUP_CHANNEL_ID` names a channel, the bot posts the link there
first, waits until **we** have served the whole file, pauses
`WARMUP_SETTLE_SECONDS`, deletes it, and only then posts publicly - by which
point Discord has the embed cached for that URL.

The wait is on our own byte accounting rather than a timer. We serve the video,
so "Discord has finished pulling it" is something we know first-hand instead of
guessing; `WARMUP_TIMEOUT_SECONDS` caps it.

It is **off by default**, and there is no implicit target: warming has to post
the link somewhere, and anywhere picked on your behalf is a visible side effect
you did not ask for. Use a channel nobody reads, in the same guild, that the
bot can post in.

Every failure path falls back to posting unwarmed: a missing channel, a failed
post, a timeout. `WARMUP_CHANNEL_ID=0` turns it off.

Two things this assumes, neither documented by Discord: that the unfurl cache is
shared across channels, and that a short settle is enough. The log settles it -
a warmed link should produce **no second `page` fetch** when it goes out
publicly:

```bash
grep -E 'warm |page |media ' /opt/gamtube/logs/app.log | tail -20
```

### Why the link still exists

The link route has no size ceiling, at the cost of the bet above. That is the
whole of its remaining job: everything that fits goes out as an attachment and
never reaches any of this.

---

## Why a downloaded mp4 is not trusted as-is

yt-dlp hands back whatever the platform served, and that file plays fine in VLC
and on a phone while stalling on desktop - audio running, or a first frame and
a picture that never advances. Two separate reasons, so the bot checks both
before it decides anything else, on the upload path as well as the link path.
Discord re-encodes attachments anyway, but shipping it a broken index is still
not worth doing.

**The index.** An mp4 is a sequence of boxes, each a 32 bit size and a 4
character type (ISO/IEC 14496-12 4.2). The `moov` box is the index and `mdat`
is the media, and a player reading over ranged HTTP - which is exactly how
`/d/{id}/f` is fetched - cannot begin until it has the index. A `moov` sitting
*after* `mdat` is the standard cause of that stall. `core/mp4.py` walks the top
level boxes with no decoder and no ffprobe, so it is tested here on hand built
atoms.

**The streams.** 10 bit, 4:2:2 and 4:4:4 H.264 are all valid and all undecodable
in a browser or Discord's player, as is non-AAC audio in an mp4 container, and
libx264 will not take odd dimensions. So the file passes only if it is h264,
`yuv420p`, a baseline/main/high profile, even dimensions, and aac audio or none.

What happens then:

| Verdict | Work | Cost |
|---|---|---|
| everything passes | served as downloaded | none |
| streams fine, `moov` last | remux: `-c copy -movflags +faststart` | a moov rewrite |
| anything else | full re-encode, also `+faststart` | a real encode |

`+faststart` is on **both** ffmpeg argument sets in `core/transcoder.py`, which
means the web app's own output gets it too. The log names which one happened
and why:

```
gate https://...: already fine - h264/aac yuv420p with moov first
gate https://...: remuxed in 0.4s -> 3.1MB - moov follows mdat, so ranged playback stalls
gate https://...: re-encoded in 11.2s -> 4.0MB - pix_fmt is yuv420p10le, not yuv420p
```

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

### 3. Invite it

On the application's **Installation** page set the install link to **Discord
Provided Link**, and under **Default Install Settings → Guild Install** set
scope `bot` and the permissions below. Then the invite is just:

```
https://discord.com/oauth2/authorize?client_id={APP_ID}
```

Discord reads the scopes from what you saved, so there is one place to change
this later rather than a URL to rebuild.

| Permission | Why |
|---|---|
| View Channels | see a channel exists at all |
| Send Messages | post the reply |
| Read Message History | re-read the message after Discord unfurls it |
| Embed Links | what makes a posted link unfurl into a player |
| Attach Files | send the video as an attachment instead of a link |
| Add Reactions | the working and outcome markers |

Install permissions are a ceiling, not a guarantee: a channel overwrite denying
any of them still wins, which is exactly how you exclude a channel. Leaving
them out of the invite also mostly works, since a bot inherits `@everyone` and
most servers allow all but Attach Files to it - but then the bot's permissions
change whenever `@everyone` does, which is a surprise you do not need.

**The bot acts wherever it can see a link.** There is no per-channel enabling
step: see **Excluding a channel** below for the opt-out, and **Requiring an
explicit opt-in** for the stricter mode if you want the other behaviour.

### 4. Check the public URL

The reply links to `{BASE_URL}/d/{id}`, so `BASE_URL` has to be what the
outside world actually reaches, scheme included. Since `/d/` is served by the
same app on the same port as everything else, a reverse proxy that already
fronts gamtube needs no new routing.

---

## Excluding a channel

Deny the bot **View Channels** there:

> Channel → **Edit Channel** → **Permissions** → add the bot (or its role) →
> set View Channels to deny (red X) → save.

A category-level deny covers every channel under it, which is the cheap way to
exclude most of a server at once; a channel-level allow beats it where you want
the bot back. Threads have no permissions of their own, so a thread follows its
parent channel.

That is the whole mechanism. Discord is the record, so it survives restarts and
redeploys, there is no command and no stored state, and the bot never writes
these overwrites itself - doing so would need Manage Roles or Manage Channels,
which it deliberately does not have.

A channel it cannot see is invisible in every sense: no message content, no
log line, nothing to opt out of twice.

On connect the bot says where it will reply, and warns when a channel it is in
cannot actually be replied in - a channel with View Channels but no Send
Messages looks fine and is silent:

```
INFO  gamtube.bot  my-server: will reply in 3 of 12 text channel(s): clips, memes, general
WARN  gamtube.bot  my-server: #quiet is in scope but the bot lacks send_messages there; it will not be able to reply
```

`Attach Files` is the one gap that is not a warning: without it every clip
still works, it just goes out as a link with 🔒, so it is said once at startup
rather than per clip.

```
INFO  gamtube.bot  my-server: #clips has no Attach Files for the bot, so every clip there goes out as a link
```

If it can see nothing at all, that is a warning too, since it looks identical
to a broken bot:

```
WARN  gamtube.bot  my-server: the bot will reply in none of its 12 text channel(s) (it cannot see any of them)
```

## Requiring an explicit opt-in

`CHANNEL_OPT_IN=true` inverts the default: the bot then refuses to act unless
the channel carries an **explicit** permission overwrite granting it
`OPT_IN_PERMISSION` (default `embed_links`, which is also what its reply needs
in order to unfurl, so marker and capability cannot disagree).

> Channel → **Edit Channel** → **Permissions** → add the bot (or its role) →
> tick Embed Links → save.

Use it when a server would rather a new channel be silent until someone
deliberately enables it. The cost is exactly that: a channel created later does
nothing, and the symptom is silence.

What does **not** count as opting in:

- **Role-level Embed Links from the invite.** Base role permissions aren't
  overwrites, so the invite can carry the permission without enabling anything.
- **An `@everyone` overwrite.** `embed_links` is commonly allowed for
  `@everyone`, and honouring it would opt in every channel on the server at
  once, so it is ignored.
- **Inheriting from a category.** Only an overwrite on the channel itself (or,
  for a thread, on its parent channel) counts.

In this mode a DM is refused outright, since a DM cannot carry an overwrite.
With opt-in off the bot answers a DM like any other message it can see.

`OPT_IN_PERMISSION` changes which permission is the marker; it is validated
against Discord's permission names at startup, so a typo is logged and the bot
doesn't start rather than silently doing nothing. `DISCORD_GUILDS` /
`DISCORD_CHANNELS` are a coarser allowlist applied before either rule.

### Blindness in that mode

Opting in controls what the bot *does*, not what it *sees*: it is a server
member and inherits `@everyone`, so it can usually still read channels it will
never act in. Closing that gap takes the same overwrites as **Excluding a
channel** - deny View Channels to the bot's role at the category level, allow
it back on the channels you opted in - and the startup log reports how far you
got, because a half-done pass is otherwise invisible:

```
INFO  gamtube.bot  my-server: blind outside its opted-in channels (0 of 14 text channel(s) visible but not opted in)
WARN  gamtube.bot  my-server: 7 of 14 text channel(s) visible but not opted in (general, random, offtopic, music, art, and 2 more); deny View Channels to the bot's role at the category level to make it blind - see docs/bot.md
```

The count is the number to drive to zero, and only appears in this mode -
with opt-in off, a channel it can see is a channel it works in, so there is
nothing to compare. It is a warning and never a refusal to start: the gate is
already correct, so a half-done pass is untidy rather than unsafe.

Only text channels are counted; a voice channel it can see cannot carry a
link. And a channel listed there may be one you opted in *improperly* - check
it is not also in an "is in scope but the bot lacks ..." warning.

---

## How it decides to act

1. A message arrives in a channel the bot can see - or, with
   `CHANNEL_OPT_IN=true`, one that explicitly opted in. Links are matched against
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

A link is served from RAM, so there are two budgets. They are **not** Discord's
upload cap, which is the guild's and much smaller:

- `MAX_ITEM_MB` (100) - skip any single video bigger than this.
- `MAX_MEMORY_MB` (512) - total across all live links; oldest evicted first
  when over, regardless of TTL. An attachment never enters the store, so it
  costs nothing here.

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
gate https://...: remuxed in 0.4s -> 3.1MB - moov follows mdat, so ranged playback stalls
prepared https://... : 3.1MB 576x1024 14s total 2.5s (attach=True: 3254579 fits the 10485760 byte cap)
uploaded https://... as Real Title.mp4 (3.1MB, 576x1024, 3.4s end to end; no store entry, no link)
```

and, when it has to fall back:

```
prepared https://... : 22.0MB 1080x1920 183s total 24.1s (attach=False: fitting 183s into 10485760 bytes allows only 458kbps, under the 900kbps floor)
falling back to a link for https://...: fitting 183s into 10485760 bytes allows only 458kbps, under the 900kbps floor
posted https://... as https://tube.4tw.pw/d/abc123def456 (oversize: 22.0MB, 1080x1920, held 300s, 24.6s end to end; store now 1 item(s)/22.0MB)
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
python3 scripts/bot-timeline.py                      # one line per link: full, evict, 404s
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
- **Survive a restart.** The store is memory only, so every *link* the bot has
  posted dies when the service restarts. Given the short TTL that is usually
  indistinguishable from normal expiry. Attachments are unaffected: once
  uploaded they are Discord's, not ours.
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
