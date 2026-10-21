# gamtube

Re-host video from anywhere yt-dlp supports. Two front ends over one download
pipeline:

- **web** — paste a URL, get back a clean link at `your.domain/v/{id}`.
  [docs/web.md](docs/web.md)
- **bot** — a Discord bot that replies with the video when someone posts a
  TikTok, Instagram or Facebook link Discord won't embed.
  [docs/bot.md](docs/bot.md)

No accounts. No algorithm. No ads. Just the video.

---

## Layout

```
core/     yt-dlp + ffmpeg, short ids, title cleanup — no web, no Discord
web/      FastAPI app: routes, templates, database, storage, expiry
bot/      Discord client: link detection, download, attachment upload
docs/     web.md, bot.md
```

`core/` is the only shared code. The web app and the bot are separate
processes, separate systemd units, and neither imports the other — the bot runs
perfectly well with the web app stopped, and vice versa. They share one venv
and one `.env`, each reading only its own keys.

---

## Run either one

```bash
cp .env.example .env
make migrate
make dev        # web, on :8000
make dev-bot    # bot (needs DISCORD_BOT_TOKEN)
```

---

## Deploy

Fresh **Ubuntu 24.04** LXC container or VM, as root:

```bash
git clone https://github.com/Gamah/gamtube.git
cd gamtube
bash deploy.sh --domain your.domain.tld
```

Installs Python 3.12 and ffmpeg, creates a service user and a venv, writes
`.env`, runs migrations, and registers `gamtube` plus — if you give it a token —
`gamtube-bot`. Every prompt is pre-filled from the existing `.env`, so pressing
enter on a re-run keeps the current value and nothing is silently reset.

The base URL prompt wants a full URL **including scheme**: `MEDIA_BASE_URL` is
derived from it, and an `http://` value on an HTTPS site gets every video
blocked as mixed content. `--domain` only ever produces `http://`.

Updating an existing guest is `make up` (pull, re-deploy with `--keep`, print
versions). `make status`, `make logs`, `make bot-logs` and `make update-ytdlp`
cover the rest.

A daily timer upgrades yt-dlp and restarts both services only when the version
actually moves — they import it as a library, so nothing picks up a new
extractor set without a restart.

---

## Stack

Python 3.12 · FastAPI · discord.py · yt-dlp · ffmpeg · SQLite · SQLAlchemy ·
Alembic

---

## Support

If this is useful to you: [buymeacoffee.com/gamah](https://buymeacoffee.com/gamah)
