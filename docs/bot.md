# The Discord bot

Discord gives TikTok, Instagram, Facebook and Reddit links a dead link-card
with nothing to play. The bot watches for those links, downloads the video with
yt-dlp and replies with it as an **attachment**, so Discord's own player handles
it and Discord's CDN holds the file.

It does not use the web app. No database row, no storage backend, no expiry —
the file exists in a temp directory for as long as the upload takes and is then
deleted. The two processes share only `core/`.

---

## Why an attachment and not a link

An attachment and a link are different mechanisms, and only one of them is
actually hosting:

- **Attachment** — Discord accepts the bytes, stores them, serves them. Your
  server is out of the loop afterwards, and the video survives indefinitely.
- **Link** — Discord reads your `og:` tags and renders a player pointed at
  *your* URL. Every play is your bandwidth, and when `VIDEO_TTL_HOURS` deletes
  the file the embed in channel history goes dead.

The attachment route costs an upload cap: Discord accepts up to the guild's
`filesize_limit`, which rises with boost level. That is fine for the content
this is for — short-form clips are small, and the videos Discord fails to embed
are precisely the ones that fit.

---

## Setup

### 1. Create the application

In the Discord developer portal: **New Application** → **Bot** → copy the
**token**. That token is the only required setting.

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

**Do not grant Attach Files here.** That one is granted per channel, and it is
what opts a channel in.

### 4. Configure and start

Put the token in `.env`:

```
DISCORD_BOT_TOKEN=...
```

`deploy.sh` prompts for it and enables `gamtube-bot` when it is set; an empty
token leaves the service disabled. Locally, `make dev-bot`.

---

## Opting a channel in

The bot acts only in channels that **explicitly grant it Attach Files** as a
channel permission overwrite:

> Channel → **Edit Channel** → **Permissions** → add the bot's role (or the bot
> itself) → tick **Attach Files** → save.

Removing that overwrite opts the channel back out. There is no command and no
stored state: Discord is the record, so it survives restarts and redeploys, and
there is nothing to keep in sync.

This is deliberately the same permission the bot needs to do its job, so the
two can't disagree — a channel where it isn't opted in is a channel where it
physically cannot upload.

What does **not** count as opting in:

- **Role-level Attach Files from the invite.** Base role permissions aren't
  overwrites. This is why step 3 says to leave it out of the invite.
- **An `@everyone` overwrite.** `attach_files` is commonly allowed for
  `@everyone`, and honouring it would opt in every channel on the server at
  once, so it is ignored.
- **Inheriting from a category.** Only an overwrite on the channel itself (or,
  for a thread, on its parent channel) counts.

Threads have no permissions of their own, so a thread follows its parent
channel.

On connect the bot logs which channels are enabled in each guild, and warns
when a guild has none — an opt-in bot with nothing granted looks identical to a
broken one, so check `logs/bot.log` first:

```
INFO  gamtube.bot  my-server: enabled in clips, memes
WARN  gamtube.bot  other-server: no channel grants attach_files to the bot, so it will do nothing here
```

Set `CHANNEL_OPT_IN=false` to drop the requirement and act anywhere
`DISCORD_GUILDS` / `DISCORD_CHANNELS` permit. `OPT_IN_PERMISSION` changes which
permission is the marker; it is validated against Discord's permission names at
startup, so a typo fails loudly instead of silently disabling the bot.

---

## How it decides to act

1. A message arrives in an opted-in channel. Links are matched against `WATCH_DOMAINS` (default list
   in `bot/detect.py`) — a cheap prefilter so unrelated links never cost a
   yt-dlp call.
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

The format selector prefers H.264/AAC that already fits under the cap, so the
common case needs no ffmpeg at all. If the download still lands over the cap
and `SHRINK_ENABLED` is on, there is one re-encode attempt at `SHRINK_CRF` and
`SHRINK_MAX_HEIGHT`; if that is still too big, the bot gives up quietly.

---

## Tuning

`EMBED_WAIT_SECONDS` is the one setting you should expect to change. Three
seconds is a guess — the real value depends on how fast Discord unfurls each
platform. Too short and links that *would* have embedded get re-hosted
anyway; too long and the reply feels detached from the message. Watch
`logs/bot.log` on a real guild and adjust.

`DISCORD_GUILDS` and `DISCORD_CHANNELS` are a coarser filter applied before
the opt-in check — useful for keeping a bot that is in several servers working
in only one of them. With `CHANNEL_OPT_IN=true` they are usually unnecessary,
since a channel that grants nothing is already excluded.

---

## What it deliberately does not do

- **Remove the original dead embed.** A bot cannot edit someone else's
  message. You get the working video *plus* their broken card, unless you give
  it Manage Messages and let it delete and repost on the user's behalf — a much
  more intrusive bot, and not what this is.
- **Remember anything across restarts.** Dedup is an in-process set, so the
  same link posted in the same channel is downloaded once while the process
  lives and again after a restart.
- **Handle links inside code blocks or spoiler tags.** They are treated like
  any other text.

---

## Operations

```bash
systemctl status gamtube-bot
tail -f /opt/gamtube/logs/bot.log      # or: make bot-logs
```

`gamtube-update-ytdlp.timer` restarts the bot along with the web app whenever
yt-dlp actually moves. Both import yt-dlp as a library, so neither picks up a
new version without a restart.
