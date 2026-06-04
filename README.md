# gamtube

Paste a URL from YouTube, Instagram, TikTok, Facebook, Twitter/X, or anywhere yt-dlp supports. Get back a clean, permanent link at `your.domain/v/{id}`.

No accounts. No algorithm. No ads. Just the video.

---

## How it works

1. Submit a URL — the server downloads it and stores it.
2. You get a short link. Share it anywhere.
3. Anyone with the link can watch it — no login, no tracking, no bullshit.

Same URL always produces the same short ID, so submitting a duplicate just returns the existing link instantly.

By default the file is served as downloaded (VP9/AV1 webm from YouTube, etc. — modern browsers handle these fine). Set `TRANSCODE_ENABLED=true` to normalize everything to H.264 MP4 on ingest, or re-encode individual videos on demand from the admin panel.

---

## Admin panel

Set `ADMIN_PASSWORD` in `.env` to enable `/manage` — a paginated table of all videos with per-row actions: renew expiry, delete file, unlist, re-encode to H.264, or nuke the DB row entirely. Leave it empty to disable the panel.

---

## Self-hosting

Requires a fresh **Ubuntu 24.04** LXC container (or VM). Run as root:

```bash
git clone https://github.com/Gamah/gamtube.git
cd gamtube
bash deploy.sh --domain your.domain.tld
```

That's it. The script installs Python 3.12, ffmpeg, sets up a systemd service, and runs migrations.

**Logs:** `tail -f /opt/gamtube/logs/app.log`

**Re-deploy after updates:**
```bash
git pull && bash deploy.sh --domain your.domain.tld
```

---

## Stack

Python 3.12 · FastAPI · yt-dlp · ffmpeg · SQLite · SQLAlchemy · Alembic

---

## Support

If this is useful to you: [buymeacoffee.com/gamah](https://buymeacoffee.com/gamah)
