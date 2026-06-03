# gamtube

Personal video re-hosting tool. Paste a URL from any major platform (YouTube, Instagram, TikTok, Facebook, Twitter/X, etc.), yt-dlp downloads it and extracts metadata, ffmpeg normalizes to H.264 MP4, and it gets re-hosted at `domain.tld/v/{short_id}`. No social features, no homepage, no browse/discover. Two use cases only: submit a URL and get a link back, or arrive at a link and be served a video.

Submissions are public; anyone who can reach the server can submit a URL. Viewing is public.

---

## Stack

- **Python 3.12 + FastAPI** — yt-dlp runs as an imported library (not subprocess)
- **SQLAlchemy 2.0** — SQLite for MVP; swap DSN for PostgreSQL, no ORM changes needed
- **Alembic** — schema migrations
- **Jinja2** — server-rendered HTML, no JS framework
- **ffmpeg-python** — thin wrapper for transcode subprocess
- **pydantic-settings** — env var config

---

## Directory Structure

```
gamtube/
├── app/
│   ├── main.py              # app factory, lifespan (cleanup task), router registration, static mounts
│   ├── cleanup.py           # background expiry loop; deletes files, marks status=expired
│   ├── config.py            # Settings (pydantic-settings), get_storage() factory
│   ├── database.py          # lazy engine + SessionLocal(), Base, get_db()
│   ├── models.py            # Video ORM model
│   ├── schemas.py           # Pydantic request/response shapes
│   ├── ids.py               # SHA-256(source_url)[:12] short ID derivation
│   ├── rendering.py         # jinja2.Environment wrapper; render(name, **ctx) → HTMLResponse
│   ├── storage/
│   │   ├── base.py          # StorageBackend ABC
│   │   ├── local.py         # LocalStorageBackend
│   │   └── s3.py            # S3StorageBackend stub (NotImplementedError)
│   ├── pipeline/
│   │   ├── downloader.py    # yt-dlp wrapper → info dict + file path
│   │   ├── transcoder.py    # ffmpeg H.264 normalization, stream-copy if already H.264
│   │   └── worker.py        # orchestrates download → transcode → store → DB update
│   ├── routers/
│   │   ├── submit.py        # GET / GET /submit (form) + POST /submit (public)
│   │   └── videos.py        # GET /v/{id}, /v/{id}/status.json
│   └── templates/
│       ├── submit.html      # plain URL form, no API key
│       ├── video.html       # ONLY a <video> tag — no title, no metadata, no chrome
│       ├── status.html      # plain processing status + meta-refresh, no chrome
│       └── error.html       # plain error message, no chrome
├── static/
│   └── style.css
├── media/                   # default MEDIA_ROOT (gitignored)
├── logs/                    # app.log written by systemd service (gitignored)
├── migrations/
│   ├── env.py               # reads DATABASE_URL from app.config
│   ├── script.py.mako
│   └── versions/
│       ├── 0001_init.py     # initial schema migration
│       ├── 0002_submitter_id.py  # add submitter_id column
│       └── 0003_expires_at.py    # add expires_at column
├── alembic.ini
├── deploy.sh                # bootstrap script for Ubuntu 24.04 LXC + systemd
├── .env.example
├── requirements.txt
└── run.py                   # uvicorn app.main:app --reload
```

No shared base template — there is no nav, no branding chrome, no shared layout. Each page is self-contained and minimal.

---

## Data Model

Table: `videos`

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `short_id` | String(12) UNIQUE | SHA-256(source_url)[:12]; indexed |
| `source_url` | Text UNIQUE | integrity constraint |
| `status` | String | `pending` `downloading` `transcoding` `ready` `error` `expired` |
| `error_message` | Text nullable | |
| `title` | Text nullable | stored for record-keeping, never displayed |
| `description` | Text nullable | stored, never displayed |
| `uploader` | String(255) nullable | stored, never displayed |
| `duration_seconds` | Integer nullable | stored, never displayed |
| `tags` | JSON nullable | stored, never displayed |
| `submitter_id` | String(36) nullable | UUID4 from cookie at submit time |
| `video_path` | String(512) nullable | storage key (`{short_id}.mp4`); cleared on expiry |
| `file_size_bytes` | BigInteger nullable | cleared on expiry |
| `expires_at` | DateTime nullable | set to `ready_time + VIDEO_TTL_HOURS` on each `ready` transition; null if TTL=0 |
| `created_at` | DateTime | Python-side UTC default |
| `updated_at` | DateTime | Python-side `onupdate` UTC |

Metadata is captured from yt-dlp and stored in the DB for your own records. It is never rendered to viewers.

---

## Short ID (`app/ids.py`)

```python
import hashlib

def short_id_for(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:12]
```

Same URL always produces the same 12-char hex ID. The `source_url` unique constraint is the secondary integrity check; duplicate submissions return the existing `short_id` immediately without re-downloading.

---

## Database (`app/database.py`)

Engine and session factory are created lazily on first use via `@lru_cache`, so importing the module doesn't require a valid `.env`. Two entry points:

- `get_db()` — FastAPI dependency (yields, auto-closes)
- `SessionLocal()` — function returning a plain `Session`; used by `worker.py` which runs outside request context

---

## Storage Abstraction (`app/storage/base.py`)

```python
class StorageBackend(ABC):
    def save(self, source_path: Path, dest_key: str) -> str: ...
    def get_url(self, key: str) -> str: ...
    def delete(self, key: str) -> None: ...
    def get_local_path(self, key: str) -> Path | None: ...  # None for remote backends
```

Storage key is always `{short_id}.mp4` — flat, no subdirectory.

`LocalStorageBackend`: moves file into `MEDIA_ROOT/{key}`, returns `{MEDIA_BASE_URL}/{key}`.
`S3StorageBackend`: stub, all methods raise `NotImplementedError` with `# TODO: boto3`.

`get_storage()` in `config.py` is used as a FastAPI `Depends()` in both routers. The resolved instance is passed directly to `process_video` via `BackgroundTasks`.

---

## Download Pipeline (`app/pipeline/`)

**`downloader.py`**: uses `yt_dlp.YoutubeDL` as a library. `format="bestvideo+bestaudio/best"`. Output template is `video.%(ext)s`; the actual output file is found by globbing the temp dir and picking the largest non-partial file (handles yt-dlp's merged output filenames robustly).

**`transcoder.py`**: probes input with `ffprobe` (JSON output). If video codec is already `h264` and container is `mp4`, uses `-c copy`. Otherwise: `-c:v libx264 -crf 23 -preset fast -c:a aac -b:a 128k`.

**`worker.py`** (`process_video`, called via `BackgroundTasks`):
1. Set `status = downloading`
2. Download to `tempfile.mkdtemp(dir=TEMP_DIR)` → write metadata fields to DB (title, description, uploader, duration, tags)
3. Set `status = transcoding`
4. Transcode to `{tmp}/{short_id}.mp4`
5. `storage.save(mp4_path, f"{short_id}.mp4")`
6. Set `status = ready`, update `video_path`, `file_size_bytes`
7. On any exception: set `status = error`, store `error_message`
8. `finally`: `shutil.rmtree(tmp_dir)`

Swap to Celery/ARQ in future by replacing `background_tasks.add_task(process_video, ...)` — function signature stays the same.

---

## API Routes

| Method | Path | Auth | Behavior |
|---|---|---|---|
| `GET` | `/` | public | Render submit form |
| `GET` | `/submit` | public | Render submit form |
| `POST` | `/submit` | public | Derive `short_id` from URL hash → if exists return it; else create record, enqueue, return `short_id` immediately. Sets `submitter_id` cookie. |
| `GET` | `/v/{short_id}` | public | `ready` → render video.html; processing → render status.html; `error` → render error.html |
| `GET` | `/v/{short_id}/status.json` | public | `{"status": "...", "error": null}` — polled by status.html meta-refresh |
| `GET` | `/media/{path:path}` | public | `StaticFiles` mount at `MEDIA_ROOT` (dev); nginx in prod |

`POST /submit` returns `{"short_id": "...", "url": "https://domain.tld/v/..."}` and sets `Set-Cookie: submitter_id=<uuid4>; HttpOnly; SameSite=Lax; Max-Age=31536000`.

The submit form (`submit.html`) sends `POST /submit` via JS fetch with no authentication.

---

## Session Identity (`app/routers/submit.py`)

On every `POST /submit`, a `submitter_id` UUID4 is read from the request cookie or generated fresh, written to the `videos` row, and echoed back as a persistent cookie. This lets a future auth layer identify the original submitter without a full user system.

Promote a known UUID to owner/admin (e.g. via a one-time env var `OWNER_SESSION_ID`) to gate privileged actions without a full user table.

---

## Configuration (`.env` / env vars)

```
DATABASE_URL=sqlite:///./gamtube.db
MEDIA_ROOT=./media
MEDIA_BASE_URL=http://localhost:8000/media
STORAGE_BACKEND=local           # local | s3
BASE_URL=http://localhost:8000
TEMP_DIR=                       # optional; defaults to system temp
VIDEO_TTL_HOURS=24              # hours until hosted file is deleted; 0 = never expire
# S3 (ignored when STORAGE_BACKEND=local):
S3_BUCKET=
S3_REGION=us-east-1
S3_ACCESS_KEY=
S3_SECRET_KEY=
```

---

## Video Expiry (`app/cleanup.py`)

Videos expire after `VIDEO_TTL_HOURS` hours (default 24). On expiry the **file is deleted** and `status` is set to `expired`, but the **DB row is kept** so the video can be re-fetched on demand.

**Cleanup loop** — started via FastAPI `lifespan` in `app/main.py`. Runs 30 s after startup, then every 10 minutes. Finds all `ready` rows where `expires_at <= now`, calls `storage.delete()`, and marks them `expired`.

**On-demand re-fetch** — when `GET /v/{short_id}` hits an `expired` row, `video_page` atomically flips status to `pending` (`UPDATE … WHERE status='expired'`, so concurrent requests don't double-enqueue) and adds `process_video` to `BackgroundTasks`. The visitor sees the status/progress page while the video re-downloads and re-encodes. `expires_at` resets to `now + TTL` on the new `ready` transition.

**Info panel countdown** — `video.html` receives `expires_at` as a UTC ISO string and shows a live ticking countdown (seconds precision) in the info panel. Hidden when `VIDEO_TTL_HOURS=0`.

---

## Running Locally

```bash
cp .env.example .env
alembic upgrade head
python run.py
```

## Deploying (Ubuntu 24.04 LXC)

```bash
sudo bash deploy.sh --domain your.domain.tld
```

Installs `python3.12 + ffmpeg`, creates a `gamtube` system user, copies the app to `/opt/gamtube`, creates a venv, writes `.env`, runs migrations, and registers a systemd service. Re-running deploy.sh on an existing install is safe — it rsyncs code, upgrades packages, runs any new migrations, and restarts the service.

```bash
systemctl status gamtube
tail -f /opt/gamtube/logs/app.log
```

---

## Verification

```bash
# Submit a video
curl -X POST http://localhost:8000/submit \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=jNQXAC9IVRw"}'
# → {"short_id": "aabbccddeeff", "url": "http://localhost:8000/v/aabbccddeeff"}

# Same URL again → same short_id, no re-download
curl -X POST http://localhost:8000/submit \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=jNQXAC9IVRw"}'

# Poll status
curl http://localhost:8000/v/aabbccddeeff/status.json
# → {"status": "ready", "error": null}

# Verify H.264
ffprobe media/aabbccddeeff.mp4 2>&1 | grep "Video:"
# → Video: h264 ...
```
