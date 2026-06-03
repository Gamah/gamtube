# gamtube

Personal video re-hosting tool. Paste a URL from any major platform (YouTube, Instagram, TikTok, Facebook, Twitter/X, etc.), yt-dlp downloads it and extracts metadata, ffmpeg normalizes to H.264 MP4, and it gets re-hosted at `domain.tld/v/{short_id}`. No social features, no homepage, no browse/discover. Two use cases only: submit a URL and get a link back, or arrive at a link and be served a video.

Submissions require an API key. Viewing is public.

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
│   ├── main.py              # app factory, router registration, startup
│   ├── config.py            # Settings (pydantic-settings), get_storage() factory
│   ├── database.py          # engine, SessionLocal, Base
│   ├── models.py            # Video ORM model + StatusEnum
│   ├── schemas.py           # Pydantic request/response shapes
│   ├── auth.py              # X-API-Key dependency
│   ├── ids.py               # SHA-256(source_url)[:12] short ID derivation
│   ├── storage/
│   │   ├── base.py          # StorageBackend ABC
│   │   ├── local.py         # LocalStorageBackend
│   │   └── s3.py            # S3StorageBackend stub (NotImplementedError)
│   ├── pipeline/
│   │   ├── downloader.py    # yt-dlp wrapper → info dict + file path
│   │   ├── transcoder.py    # ffmpeg H.264 normalization, stream-copy if already H.264
│   │   └── worker.py        # orchestrates download → transcode → store → DB update
│   ├── routers/
│   │   ├── submit.py        # POST /submit (auth required)
│   │   └── videos.py        # GET /v/{id}, /v/{id}/status.json
│   └── templates/
│       ├── submit.html      # paste URL form, shows output link to copy on success
│       ├── video.html       # ONLY a <video> tag — no title, no metadata, no chrome
│       ├── status.html      # plain processing status + auto-refresh, no chrome
│       └── error.html       # plain error message, no chrome
├── static/
│   └── style.css
├── media/                   # default MEDIA_ROOT (gitignored)
├── migrations/              # alembic
├── alembic.ini
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
| `status` | String | `pending` `downloading` `transcoding` `ready` `error` |
| `error_message` | Text nullable | |
| `title` | Text nullable | stored for record-keeping, never displayed |
| `description` | Text nullable | stored, never displayed |
| `uploader` | String(255) nullable | stored, never displayed |
| `duration_seconds` | Integer nullable | stored, never displayed |
| `tags` | JSON nullable | stored, never displayed |
| `video_path` | String(512) nullable | storage key (`{short_id}.mp4`) |
| `file_size_bytes` | BigInteger nullable | |
| `created_at` | DateTime | server default UTC |
| `updated_at` | DateTime | onupdate UTC |

Metadata is captured from yt-dlp and stored in the DB for your own records. It is never rendered to viewers.

---

## Short ID (`app/ids.py`)

```python
import hashlib

def short_id_for(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:12]
```

Same URL always produces the same 12-char hex ID. At 48 bits, collisions between different URLs are negligible for a personal tool (~1 in 281 trillion). The `source_url` unique constraint provides a secondary integrity check.

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

Each backend implementation owns how it maps the logical key to a URL. A future Akamai backend returns whatever CDN URL format Akamai requires — the app never sees it.

Active backend injected via `get_storage()` FastAPI dependency based on `STORAGE_BACKEND` env var.

---

## Download Pipeline (`app/pipeline/`)

**`downloader.py`**: uses `yt_dlp.YoutubeDL` as a library. `format="bestvideo+bestaudio/best"`. No thumbnail download. Returns yt-dlp info dict + downloaded file path.

**`transcoder.py`**: probes input with `ffprobe` (JSON output). If video codec is already `h264` and container is `mp4`, uses `-c copy`. Otherwise: `-c:v libx264 -crf 23 -preset fast -c:a aac -b:a 128k`.

**`worker.py`** (called via `BackgroundTasks`):
1. Set `status = downloading`
2. Download to `tempfile.mkdtemp(dir=TEMP_DIR)` → update metadata fields in DB
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
| `GET` | `/` | public | Render submit form (homepage) |
| `GET` | `/submit` | public | Render submit form |
| `POST` | `/submit` | API key | Derive `short_id` from URL hash → if exists return it; else create record, enqueue, return `short_id` immediately |
| `GET` | `/v/{short_id}` | public | `ready` → render video.html (just `<video>`); processing → render status.html; `error` → render error.html |
| `GET` | `/v/{short_id}/status.json` | public | `{"status": "...", "error": null}` — polled by status.html meta-refresh or JS |
| `GET` | `/media/{path:path}` | public | `StaticFiles` mount at `MEDIA_ROOT` (dev); nginx in prod |

No index of videos. No browse/discover endpoint.

`POST /submit` returns `{"short_id": "...", "url": "https://domain.tld/v/..."}` — the submit form displays this as a copyable link.

---

## Auth (`app/auth.py`)

`APIKeyHeader(name="X-API-Key")` dependency on `POST /submit` only. Key compared against `API_KEY` env var. Returns 401 on mismatch. Future multi-user: replace env var check with hashed `api_keys` table lookup — route decorators unchanged.

---

## Configuration (`.env` / env vars)

```
API_KEY=                        # required
DATABASE_URL=sqlite:///./gamtube.db
MEDIA_ROOT=./media
MEDIA_BASE_URL=http://localhost:8000/media
STORAGE_BACKEND=local           # local | s3
BASE_URL=http://localhost:8000
TEMP_DIR=                       # optional; defaults to system temp (tempfile.gettempdir())
# S3 (ignored when STORAGE_BACKEND=local):
S3_BUCKET=
S3_REGION=us-east-1
S3_ACCESS_KEY=
S3_SECRET_KEY=
```

---

## Implementation Order

1. `config.py` + `database.py` + `models.py`
2. `ids.py` + `auth.py`
3. `storage/base.py` + `storage/local.py` + `storage/s3.py`
4. `schemas.py`
5. `routers/submit.py` + `routers/videos.py` (stubs — verify routing + auth work)
6. `pipeline/downloader.py` + `pipeline/transcoder.py`
7. `pipeline/worker.py` — wires 6 into submit route
8. Templates
9. Alembic migration + `alembic upgrade head`
10. `requirements.txt` + `.env.example` + `run.py`

---

## Verification

```bash
cp .env.example .env  # set API_KEY=testkey
alembic upgrade head
python run.py

# Submit a video
curl -X POST http://localhost:8000/submit \
  -H "X-API-Key: testkey" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=jNQXAC9IVRw"}'
# → {"short_id": "aabbccddeeff", "url": "http://localhost:8000/v/aabbccddeeff"}

# Same URL again → same short_id, no re-download
curl -X POST http://localhost:8000/submit \
  -H "X-API-Key: testkey" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=jNQXAC9IVRw"}'

# Poll status
curl http://localhost:8000/v/aabbccddeeff/status.json
# → {"status": "ready", "error": null}

# Video page — just the video
open http://localhost:8000/v/aabbccddeeff

# Auth rejection
curl -X POST http://localhost:8000/submit \
  -H "Content-Type: application/json" \
  -d '{"url":"..."}' # → 401

# Verify H.264
ffprobe media/aabbccddeeff.mp4 2>&1 | grep "Video:"
# → Video: h264 ...
```
