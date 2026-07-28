# gamtube

Personal video re-hosting tool. Paste a URL from any major platform (YouTube, Instagram, TikTok, Facebook, Twitter/X, etc.), yt-dlp downloads it and extracts metadata, and it gets re-hosted at `domain.tld/v/{short_id}`. Transcoding to H.264 MP4 is optional (`TRANSCODE_ENABLED`); by default the file is served as downloaded. No social features, no homepage, no browse/discover. Two use cases only: submit a URL and get a link back, or arrive at a link and be served a video.

Submissions are public; anyone who can reach the server can submit a URL. Viewing is public.

---

## Stack

- **Python 3.12 + FastAPI** — yt-dlp runs as an imported library (not subprocess)
- **SQLAlchemy 2.0** — SQLite for MVP; swap DSN for PostgreSQL, no ORM changes needed
- **Alembic** — schema migrations
- **Jinja2** — server-rendered HTML, no JS framework
- **ffmpeg/ffprobe** — optional H.264 transcoding via subprocess
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
│   ├── progress.py          # in-process SSE progress store: set_progress / get_progress
│   ├── storage/
│   │   ├── base.py          # StorageBackend ABC
│   │   ├── local.py         # LocalStorageBackend
│   │   └── s3.py            # S3StorageBackend stub (NotImplementedError)
│   ├── pipeline/
│   │   ├── downloader.py    # yt-dlp wrapper → info dict + video path + thumbnail path
│   │   ├── transcoder.py    # ffmpeg H.264 normalization, stream-copy if already H.264
│   │   └── worker.py        # process_video, refresh_metadata, reencode_video
│   ├── routers/
│   │   ├── submit.py        # GET / GET /submit (form) + POST /submit (public)
│   │   ├── videos.py        # GET /v/{id}, /v/{id}/status.json, /v/{id}/progress (SSE)
│   │   └── admin.py         # /manage panel: auth, list, renew, delete, unlist, nuke, reencode
│   └── templates/
│       ├── submit.html      # URL form + inline SSE progress bar after submission
│       ├── video.html       # ONLY a <video> tag — no title, no metadata, no chrome
│       ├── status.html      # processing status with live SSE progress bar, no chrome
│       ├── error.html       # plain error message, no chrome
│       ├── manage.html      # admin panel table
│       └── manage_login.html  # admin login form
├── static/
│   └── style.css
├── media/                   # default MEDIA_ROOT (gitignored)
├── logs/                    # app.log written by systemd service (gitignored)
├── migrations/
│   ├── env.py               # reads DATABASE_URL from app.config
│   ├── script.py.mako
│   └── versions/
│       ├── 0001_init.py     # initial schema
│       ├── 0002_submitter_id.py
│       ├── 0003_expires_at.py
│       ├── 0004_admin_fields.py  # view_count
│       ├── 0005_thumbnail_url.py
│       └── 0006_transcoded.py    # transcoded bool; backfills ready rows to true
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
| `status` | String | `pending` `downloading` `transcoding` `ready` `reencoding` `error` `expired` `unlisted` |
| `error_message` | Text nullable | |
| `title` | Text nullable | stored for record-keeping, never displayed |
| `description` | Text nullable | stored, never displayed |
| `uploader` | String(255) nullable | stored, never displayed |
| `duration_seconds` | Integer nullable | stored, never displayed |
| `tags` | JSON nullable | stored, never displayed |
| `thumbnail_url` | Text nullable | storage key for thumbnail (e.g. `{short_id}.jpg`); served only on admin panel |
| `submitter_id` | String(36) nullable | UUID4 from cookie at submit time |
| `view_count` | Integer | page-view counter; incremented on `GET /v/{id}` when ready/reencoding |
| `video_path` | String(512) nullable | storage key (e.g. `{short_id}.webm`, `{short_id}.mp4`); cleared on expiry |
| `file_size_bytes` | BigInteger nullable | cleared on expiry |
| `transcoded` | Boolean nullable | `true` = H.264 MP4; `false` = raw as downloaded; `null` = unknown (legacy) |
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
- `SessionLocal()` — function returning a plain `Session`; used by worker functions which run outside request context

---

## Storage Abstraction (`app/storage/base.py`)

```python
class StorageBackend(ABC):
    def save(self, source_path: Path, dest_key: str) -> str: ...
    def get_url(self, key: str) -> str: ...
    def delete(self, key: str) -> None: ...
    def get_local_path(self, key: str) -> Path | None: ...  # None for remote backends
```

Storage key is `{short_id}.{ext}` — flat, no subdirectory. Extension is `.mp4` when transcoded, or the original yt-dlp output extension (e.g. `.webm`) when `TRANSCODE_ENABLED=false`.

`LocalStorageBackend`: moves file into `MEDIA_ROOT/{key}`, returns `{MEDIA_BASE_URL}/{key}`.
`S3StorageBackend`: stub, all methods raise `NotImplementedError` with `# TODO: boto3`.

`get_storage()` in `config.py` is used as a FastAPI `Depends()` in all routers.

---

## Download Pipeline (`app/pipeline/`)

**`downloader.py`**: uses `yt_dlp.YoutubeDL` as a library. `format="bestvideo+bestaudio/best"`, `writethumbnail=True`. Output template is `video.%(ext)s`; the actual output file is found by globbing the temp dir and picking the largest non-image file. Returns `(info_dict, video_path, thumbnail_path | None)`. Uses `postprocessor_hooks` to emit indeterminate progress during the yt-dlp stream-merge phase (VP9+AAC → webm).

**`transcoder.py`**: probes input with `ffprobe -show_streams -show_format` (JSON). If video codec is already `h264` and container is `mp4`, uses `-c copy`. Otherwise: `-c:v libx264 -crf 23 -preset superfast -c:a aac -b:a 128k`. `_duration_us()` checks both stream-level and format-level duration so mkv containers report progress correctly.

**`worker.py`** — three public functions, all called via `BackgroundTasks`:

`process_video(short_id, url, storage)`:
1. `status = downloading` → download to `tempfile.mkdtemp(dir=TEMP_DIR)` → save thumbnail → write metadata to DB
2. If `TRANSCODE_ENABLED`: `status = transcoding` → transcode to `{tmp}/{short_id}.mp4` → `storage.save(mp4, "{short_id}.mp4")`, `transcoded=True`
3. Else: `storage.save(src, "{short_id}{src.suffix}")`, `transcoded=False` — no ffmpeg involved
4. `status = ready`, update `video_path`, `file_size_bytes`, `expires_at`
5. On exception: `status = error`, store `error_message`; `finally`: `shutil.rmtree(tmp_dir)`

`refresh_metadata(short_id, url, storage)`: re-fetches title and thumbnail via yt-dlp (`skip_download=True`, `writethumbnail=True`). Silently no-ops if the source is gone. Used by the admin renew action when the video file already exists.

`reencode_video(short_id, storage)`: transcodes the already-stored raw file to H.264 MP4 in the background. Sets `status = reencoding` (video remains live for viewers during this), then `status = ready` + `transcoded=True` when done. Deletes the original raw file after the new MP4 is saved.

Title cleanup: `_clean_title()` strips leading social-count noise that Facebook/Meta embeds in `og:title` (e.g. "1.2K views · 34 reactions · Actual Title" → "Actual Title").

---

## API Routes

| Method | Path | Auth | Behavior |
|---|---|---|---|
| `GET` | `/` | public | Render submit form |
| `GET` | `/submit` | public | Render submit form |
| `POST` | `/submit` | public | Derive `short_id` → if exists return it; else create, enqueue, return immediately. Sets `submitter_id` cookie. |
| `GET` | `/v/{short_id}` | public | `ready`/`reencoding` → render video.html; processing → render status.html; `error` → render error.html; `expired` → re-enqueue and show status |
| `GET` | `/v/{short_id}/status.json` | public | `{"status": "...", "error": null}` |
| `GET` | `/v/{short_id}/progress` | public | SSE stream; emits `{"stage": "...", "pct": 0–100 or null}` |
| `GET` | `/manage` | admin | Video table with search, pagination (50/page), actions |
| `POST` | `/manage/v/{id}/renew` | admin | Extend expiry; re-downloads if expired/error, refreshes metadata otherwise |
| `POST` | `/manage/v/{id}/delete` | admin | Delete file, set `status=expired` |
| `POST` | `/manage/v/{id}/unlist` | admin | Delete file, set `status=unlisted` (410 to viewers) |
| `POST` | `/manage/v/{id}/reencode` | admin | Transcode raw file to H.264 MP4 in background; video stays live during conversion |
| `POST` | `/manage/v/{id}/nuke` | admin | Delete file, thumbnail, and DB row entirely |
| `GET` | `/media/{path:path}` | public | `StaticFiles` mount at `MEDIA_ROOT` (dev); nginx in prod |

`POST /submit` returns `{"short_id": "...", "url": "https://domain.tld/v/..."}`.

The submit form connects to the SSE progress endpoint after submission and shows an inline progress bar that auto-hides when ready.

---

## Admin Panel (`/manage`)

Protected by HMAC-signed session cookie (`hmac(password, b"gamtube-admin-v1", sha256)`). Set `ADMIN_PASSWORD` in `.env`; leave empty to disable the panel entirely.

Features: paginated video table (50/page), search by title/URL/short_id, thumbnails, per-row actions (renew with expiry selector, delete file, unlist, re-encode, nuke). All actions use JS fetch — no page reloads. Status and expiry cells update in-place; a toast fades in with the result.

The "reencoded" column shows `yes`/`no`/`—` (dash for non-ready rows). The "re-encode" button only appears on `ready` rows where `transcoded=False`. Clicking it sets status to `reencoding` immediately in the UI and removes the button.

---

## SSE Progress (`app/progress.py`)

In-process dict mapping `short_id → {stage, pct}`. Workers call `set_progress(short_id, stage, pct)`. The `/v/{short_id}/progress` SSE endpoint polls this at 0.5 s intervals and streams events to the client. `pct=None` means indeterminate — the UI shows a CSS shimmer animation.

Stages in order: `pending → downloading → transcoding → ready`. During yt-dlp stream merge the stage stays `downloading` but `pct=None` (shimmer + "Merging…" label). During transcoding with unknown duration (some containers), `pct=None` (shimmer + "Transcoding…").

---

## Session Identity (`app/routers/submit.py`)

On every `POST /submit`, a `submitter_id` UUID4 is read from the request cookie or generated fresh, written to the `videos` row, and echoed back as a persistent cookie. This lets a future auth layer identify the original submitter without a full user system.

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
TRANSCODE_ENABLED=false         # re-encode to H.264 MP4 on ingest; false = serve as downloaded
ADMIN_PASSWORD=                 # password for /manage panel; leave empty to disable
# S3 (ignored when STORAGE_BACKEND=local):
S3_BUCKET=
S3_REGION=us-east-1
S3_ACCESS_KEY=
S3_SECRET_KEY=
```

`TRANSCODE_ENABLED=false` (default): yt-dlp output is moved directly to storage. YouTube typically delivers VP9/AV1 in webm; modern browsers play these natively. Individual videos can be re-encoded on demand from the admin panel.

`TRANSCODE_ENABLED=true`: every download is transcoded to H.264 MP4 via ffmpeg (`-preset superfast`) before being stored. Guarantees universal browser compatibility but adds significant CPU time.

---

## Video Expiry (`app/cleanup.py`)

Videos expire after `VIDEO_TTL_HOURS` hours (default 24). On expiry the **file is deleted** and `status` is set to `expired`, but the **DB row is kept** so the video can be re-fetched on demand.

**Cleanup loop** — started via FastAPI `lifespan` in `app/main.py`. Runs 30 s after startup, then every 10 minutes. Finds all `ready` rows where `expires_at <= now`, calls `storage.delete()`, and marks them `expired`.

**On-demand re-fetch** — when `GET /v/{short_id}` hits an `expired` row, `video_page` atomically flips status to `pending` and adds `process_video` to `BackgroundTasks`. The visitor sees the status/progress page while the video re-downloads.

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

Installs `python3.12 + ffmpeg`, creates a `gamtube` system user, copies the app to `/opt/gamtube`, creates a venv, writes `.env`, runs migrations, and registers a systemd service. Re-running is safe — it rsyncs code, upgrades packages, runs any new migrations, and restarts the service.

Prompts for public base URL, data directory, admin password, transcoding, and video TTL. **Every prompt is pre-filled from the existing `/opt/gamtube/.env`, so pressing enter keeps the current value** — a re-run never silently resets config. The listen port is read back from the installed systemd unit for the same reason. `--keep` skips the prompts entirely; `--domain`/`--port`/`--data-dir` override non-interactively.

The base URL prompt takes a full URL including scheme, because `MEDIA_BASE_URL` is derived from it (`{BASE_URL}/media`) and an `http://` value on an HTTPS site gets every video blocked as mixed content. `--domain` only ever produces `http://`; type the URL at the prompt for HTTPS.

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

# Poll status
curl http://localhost:8000/v/aabbccddeeff/status.json
# → {"status": "ready", "error": null}
```
