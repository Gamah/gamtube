# gamtube

Re-host video from anywhere yt-dlp supports. Two front ends over one download
pipeline:

- **web** - paste a URL, get back a clean link at `your.domain/v/{id}`.
  [docs/web.md](docs/web.md)
- **bot** - a Discord bot that replies with the video itself when someone posts
  a TikTok, Instagram or Facebook link Discord won't embed: attached when it
  fits the server's upload cap, otherwise as a playable link.
  [docs/bot.md](docs/bot.md)

No accounts. No algorithm. No ads. Just the video.

---

## Layout

```
core/     yt-dlp + ffmpeg, short ids, title cleanup - no web, no Discord
web/      FastAPI app: routes, templates, database, storage, expiry
bot/      Discord client: link detection, download, in-memory store
docs/     web.md, bot.md
tests/    runs with no venv, no token, no network
```

Dependencies run one way: `web/` imports `bot/`, `bot/` imports `core/`, and
`core/` imports neither. One process serves both - the bot's links are held in
memory and served from `/d/{id}`, so they cannot be split apart - but the bot
is inert without `DISCORD_BOT_TOKEN`, and its failures are contained so the web
app stays up regardless.

---

## Run it

```bash
cp .env.example .env
make migrate
make dev        # web on :8000, plus the bot if a token is set
make test       # pure functions, /d/ routes, deploy.sh sandbox
```

---

## What the bot's reaction means

It reacts to your message while it works, then swaps that for one emoji saying
how it ended. The outcome one stays, so a channel's history reads back.

| Emoji | Meaning |
|---|---|
| ⏳ | downloading and converting |
| ✅ | attached natively, nothing else needed |
| 📦 | posted a link instead: over the server's upload cap, or too long to fit it without wrecking it |
| 🔒 | posted a link instead: the bot has no **Attach Files** in that channel |
| 🐌 | posted a link instead: Discord throttled the upload |
| 🤔 | posted a link instead: the upload failed some other way |
| ❌ | nothing could be posted; the log says why |

All seven are code defaults in `bot/config.py`.
[docs/bot.md](docs/bot.md#what-the-reaction-means) has the longer version.

It replies in any channel it can see a watched link in. To keep it out of one,
deny it **View Channels** there, or on the whole category - that is the only
switch, and it is Discord's, not the bot's.

---

## Deploy

Fresh **Ubuntu 24.04** LXC container or VM, as root:

```bash
git clone https://github.com/Gamah/gamtube.git
cd gamtube
bash deploy.sh --domain your.domain.tld
```

Installs Python 3.12 and ffmpeg, creates a service user and a venv, writes
`.env`, runs migrations, and registers the `gamtube` service. Every prompt is
pre-filled from the existing `.env`, so pressing enter on a re-run keeps the
current value and nothing is silently reset.

The base URL prompt wants a full URL **including scheme**: `MEDIA_BASE_URL` and
the bot's `/d/` links are both derived from it, and an `http://` value on an
HTTPS site gets every video blocked as mixed content. `--domain` only ever
produces `http://`.

Updating an existing guest is just:

```bash
make up
```

It pulls, re-deploys keeping every existing `.env` value, and prints versions.
The first run after a version that adds a setting asks about it once and
remembers the answer - including "no" - so later runs are silent. With no
terminal attached it takes the defaults and carries on, so it's safe over a
pipe or from a script.

The Discord bot is opt-in: no token, no bot, and nothing about it to configure.
`--bot-token TOKEN` or `--no-bot` answer that without a prompt. `make status`,
`make logs`, `make bot-logs` and `make update-ytdlp` cover the rest.

A daily timer upgrades yt-dlp and restarts the service only when the version
actually moves - it's imported as a library, so nothing picks up a new
extractor set without a restart.

---

## Stack

Python 3.12 · FastAPI · discord.py · yt-dlp · ffmpeg · SQLite · SQLAlchemy ·
Alembic

---

## Support

If this is useful to you: [buymeacoffee.com/gamah](https://buymeacoffee.com/gamah)
