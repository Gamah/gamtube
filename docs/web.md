# The web app

Paste a URL, get back `your.domain/v/{short_id}`. The server downloads the
video with yt-dlp and re-hosts it. No accounts, no homepage, no browse - two
use cases only: submit a URL and get a link, or arrive at a link and be served
a video.

---

## Routes

| Method | Path | Auth | Behaviour |
|---|---|---|---|
| `GET` | `/`, `/submit` | public | the submit form |
| `POST` | `/submit` | public | derive `short_id`; return it if known, else create and enqueue |
| `GET` | `/v/{id}` | public | the video, or a live status page while it processes |
| `GET` | `/v/{id}/status.json` | public | `{"status": ..., "error": ...}` |
| `GET` | `/v/{id}/progress` | public | SSE stream of `{stage, pct}` |
| `GET` | `/scroll` | public | vertical feed of videos marked permanent |
| `GET` | `/manage` | admin | paginated table with per-row actions |
| `GET` | `/media/{path}` | public | dev only; nginx serves this in prod |

`POST /submit` returns `{"short_id": "...", "url": "https://domain.tld/v/..."}`.

---

## Short IDs

`core/ids.py` derives the id as `sha256(source_url)[:12]`. The same URL always
produces the same id, so a duplicate submission returns the existing link
immediately without re-downloading. `source_url` carries a unique constraint as
the secondary check.

---

## Processing

`web/worker.py` runs the pipeline via `BackgroundTasks`:

1. `status=downloading` → `core.downloader.download()` into a temp dir → save
   the thumbnail → write metadata to the row.
2. Move the file into storage under `{short_id}{ext}`.
3. `status=ready`, set `video_path`, `file_size_bytes` and `expires_at`.
4. When `TRANSCODE_ENABLED`, hand off to `reencode_video` in a thread.

Metadata (title, description, uploader, duration, tags) is stored for your own
records and never rendered to viewers. The one exception is `title`, which
becomes the `og:title` on the video page so chat apps have something to show.

`reencode_video` refuses to make things worse: it skips sources already in a
codec that beats H.264 on size (VP9, AV1, HEVC), and if the transcode comes out
larger than the original it keeps the original and marks the row `skipped`.

---

## Expiry

Videos expire `VIDEO_TTL_HOURS` after going ready. The **file** is deleted and
`status` becomes `expired`; the **row** is kept, so the video can be re-fetched
on demand. `web/cleanup.py` runs 30 s after startup and then every 10 minutes.

When `GET /v/{id}` hits an expired row it flips the status to `pending`,
enqueues a fresh download and shows the progress page. Set
`VIDEO_TTL_HOURS=0` to never expire.

Note what expiry means for a link shared in a chat app: the embed goes dead
when the file is deleted. Mark anything you have shared as permanent, or run
with a TTL of 0.

---

## Admin panel

Set `ADMIN_PASSWORD` to enable `/manage`; leave it empty to disable the panel
entirely. Auth is an HMAC-signed session cookie.

Per-row actions: renew expiry, delete the file, unlist (410 to viewers),
re-encode to H.264, mark permanent, or nuke the row. All of them are `fetch`
calls that update the cell in place.

---

## Storage

`web/storage/base.py` defines the backend interface:

```python
class StorageBackend(ABC):
    def save(self, source_path: Path, dest_key: str) -> str: ...
    def get_url(self, key: str) -> str: ...
    def delete(self, key: str) -> None: ...
    def get_local_path(self, key: str) -> Path | None: ...   # None when remote
```

Keys are flat: `{short_id}.{ext}`, no subdirectories. `LocalStorageBackend`
moves the file into `MEDIA_ROOT`. `S3StorageBackend` is a stub.

---

## Database

SQLite for now; the DSN is the only thing that changes for PostgreSQL. Engine
and session factory are built lazily behind `@lru_cache`, so importing
`web.database` does not require a valid `.env`. Migrations are Alembic -
`make migrate`.
