# gamtube

Personal video re-hosting tool with two front ends over one download pipeline: a **web app** (`web/`) and a **Discord bot** (`bot/`). Paste a URL from any major platform (YouTube, Instagram, TikTok, Facebook, Twitter/X, etc.), yt-dlp downloads it and extracts metadata, and it gets re-hosted at `domain.tld/v/{short_id}`. Transcoding to H.264 MP4 is optional (`TRANSCODE_ENABLED`); by default the file is served as downloaded. No social features, no homepage, no browse/discover. Two use cases only: submit a URL and get a link back, or arrive at a link and be served a video.

Submissions are public; anyone who can reach the server can submit a URL. Viewing is public.

---

## Stack

- **Python 3.12 + FastAPI** - yt-dlp runs as an imported library (not subprocess)
- **SQLAlchemy 2.0** - SQLite for MVP; swap DSN for PostgreSQL, no ORM changes needed
- **Alembic** - schema migrations
- **Jinja2** - server-rendered HTML, no JS framework
- **ffmpeg/ffprobe** - optional H.264 transcoding via subprocess
- **discord.py** - the bot's gateway client (`bot/`)
- **pydantic-settings** - env var config; both processes read one `.env`

---

## Directory Structure

```
gamtube/
├── core/                    # SHARED. no web, no Discord, no DB - stdlib + yt-dlp + ffmpeg
│   ├── ids.py               # SHA-256(source_url)[:12] short ID derivation
│   ├── titles.py            # clean_title(): strips social-count noise from og:title
│   ├── downloader.py        # yt-dlp wrapper → info dict + video path + thumbnail path
│   └── transcoder.py        # ffmpeg H.264 normalization, stream-copy if already H.264
├── web/
│   ├── main.py              # app factory, lifespan (cleanup task), router registration, static mounts
│   ├── cleanup.py           # background expiry loop; deletes files, marks status=expired
│   ├── config.py            # Settings (pydantic-settings), get_storage() factory
│   ├── database.py          # lazy engine + SessionLocal(), Base, get_db()
│   ├── models.py            # Video + Feedback ORM models
│   ├── schemas.py           # Pydantic request/response shapes
│   ├── rendering.py         # jinja2.Environment wrapper; render(name, **ctx) → HTMLResponse
│   ├── progress.py          # in-process SSE progress store: set_progress / get_progress
│   ├── worker.py            # process_video, refresh_metadata, reencode_video (DB + storage)
│   ├── storage/
│   │   ├── base.py          # StorageBackend ABC
│   │   ├── local.py         # LocalStorageBackend
│   │   └── s3.py            # S3StorageBackend stub (NotImplementedError)
│   ├── routers/
│   │   ├── submit.py        # GET / GET /submit (form) + POST /submit (public)
│   │   ├── videos.py        # GET /v/{id}, /status.json, /progress (SSE), /scroll
│   │   ├── feedback.py      # GET/POST /feedback
│   │   ├── ephemeral.py     # /d/{key} og:video page + /d/{key}/f ranged bytes from RAM
│   │   └── admin.py         # /manage panel: auth, list, renew, delete, unlist, nuke, reencode
│   └── templates/           # self-contained pages; no shared base template
├── bot/                     # runs INSIDE the web process; see "Discord Bot" below
│   ├── client.py            # build_client / run_client: on_message, embed re-check, dispatch
│   ├── config.py            # BotSettings (pydantic-settings), guild/channel/domain parsing
│   ├── detect.py            # PURE: extract_candidates, is_candidate, norm_url, unembedded
│   ├── scope.py             # PURE: allowed_by_ids, opted_in - channel opt-in via permissions
│   ├── store.py             # PURE: MemoryStore - TTL + byte-budget, oldest evicted first
│   ├── ranges.py            # PURE: parse_range - HTTP Range, because Discord sends them
│   └── handler.py           # rehost(): download → normalise → store → reply with the link
├── docs/
│   ├── web.md               # routes, processing, expiry, storage
│   └── bot.md               # why attachments, Discord app setup, intents, tuning
├── tests/
│   ├── test_pure.py         # detection, opt-in, store/eviction, ranges, titles
│   ├── test_routes.py       # /d/ routes via TestClient; skips if fastapi is absent
│   └── test_deploy.sh       # deploy.sh end to end against stubbed system commands
├── static/                  # style.css, awesome_face.svg
├── media/                   # default MEDIA_ROOT (gitignored)
├── logs/                    # app.log + bot.log written by the systemd services (gitignored)
├── migrations/              # alembic; env.py reads DATABASE_URL from web.config
├── alembic.ini
├── Makefile                 # up / status / logs / bot-logs / update-ytdlp / dev / migrate / test
├── deploy.sh                # bootstrap script for Ubuntu 24.04 LXC + one systemd unit
├── scripts/update-ytdlp.sh  # timer-driven yt-dlp upgrade; restarts gamtube on a version change
├── .env.example
├── requirements.txt         # one venv for both processes; grouped shared / web / bot
├── README.md
└── run.py                   # uvicorn web.main:app --reload
```

**Dependencies run exactly one way: `web/` → `bot/` → `core/`.** `core/`
imports neither of the others, and `bot/` must never import `web/` - that is
what keeps the bot's logic testable without FastAPI, a database or a token.
`web/routers/ephemeral.py` importing `bot.store` is the one edge in the other
direction, and it is deliberate: the route serves what the bot put in memory.

Anything that needs `get_settings()`, a DB session or a storage backend belongs
in `web/`, not `core/`. A `from web.config import ...` inside `core/` or `bot/`
breaks that, and `bot/config.py` exists precisely so the bot never needs it.

`core/downloader.download()` and `core/transcoder.transcode()` take keyword-only
options (`fmt`, `writethumbnail`, `crf`, `max_height`) whose defaults reproduce
the web app's behaviour exactly. The bot passes non-defaults to aim under
Discord's upload cap; don't change a default to suit one caller.

No shared base template - there is no nav, no branding chrome, no shared layout. Each page is self-contained and minimal.

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

## Short ID (`core/ids.py`)

```python
import hashlib

def short_id_for(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:12]
```

Same URL always produces the same 12-char hex ID. The `source_url` unique constraint is the secondary integrity check; duplicate submissions return the existing `short_id` immediately without re-downloading.

---

## Database (`web/database.py`)

Engine and session factory are created lazily on first use via `@lru_cache`, so importing the module doesn't require a valid `.env`. Two entry points:

- `get_db()` - FastAPI dependency (yields, auto-closes)
- `SessionLocal()` - function returning a plain `Session`; used by worker functions which run outside request context

---

## Storage Abstraction (`web/storage/base.py`)

```python
class StorageBackend(ABC):
    def save(self, source_path: Path, dest_key: str) -> str: ...
    def get_url(self, key: str) -> str: ...
    def delete(self, key: str) -> None: ...
    def get_local_path(self, key: str) -> Path | None: ...  # None for remote backends
```

Storage key is `{short_id}.{ext}` - flat, no subdirectory. Extension is `.mp4` when transcoded, or the original yt-dlp output extension (e.g. `.webm`) when `TRANSCODE_ENABLED=false`.

`LocalStorageBackend`: moves file into `MEDIA_ROOT/{key}`, returns `{MEDIA_BASE_URL}/{key}`.
`S3StorageBackend`: stub, all methods raise `NotImplementedError` with `# TODO: boto3`.

`get_storage()` in `web/config.py` is used as a FastAPI `Depends()` in all routers.

---

## Download Pipeline (`core/` + `web/worker.py`)

**`downloader.py`**: uses `yt_dlp.YoutubeDL` as a library. `format="bestvideo+bestaudio/best"`, `writethumbnail=True`. Output template is `video.%(ext)s`; the actual output file is found by globbing the temp dir and picking the largest non-image file. Returns `(info_dict, video_path, thumbnail_path | None)`. Uses `postprocessor_hooks` to emit indeterminate progress during the yt-dlp stream-merge phase (VP9+AAC → webm).

**`transcoder.py`**: probes input with `ffprobe -show_streams -show_format` (JSON). If video codec is already `h264` and container is `mp4`, uses `-c copy`. Otherwise: `-c:v libx264 -crf 23 -preset superfast -c:a aac -b:a 128k`. `_duration_us()` checks both stream-level and format-level duration so mkv containers report progress correctly.

**`web/worker.py`** - three public functions, all called via `BackgroundTasks`:

`process_video(short_id, url, storage)`:
1. `status = downloading` → download to `tempfile.mkdtemp(dir=TEMP_DIR)` → save thumbnail → write metadata to DB
2. If `TRANSCODE_ENABLED`: `status = transcoding` → transcode to `{tmp}/{short_id}.mp4` → `storage.save(mp4, "{short_id}.mp4")`, `transcoded=True`
3. Else: `storage.save(src, "{short_id}{src.suffix}")`, `transcoded=False` - no ffmpeg involved
4. `status = ready`, update `video_path`, `file_size_bytes`, `expires_at`
5. On exception: `status = error`, store `error_message`; `finally`: `shutil.rmtree(tmp_dir)`

`refresh_metadata(short_id, url, storage)`: re-fetches title and thumbnail via yt-dlp (`skip_download=True`, `writethumbnail=True`). Silently no-ops if the source is gone. Used by the admin renew action when the video file already exists.

`reencode_video(short_id, storage)`: transcodes the already-stored raw file to H.264 MP4 in the background. Sets `status = reencoding` (video remains live for viewers during this), then `status = ready` + `transcoded=True` when done. Deletes the original raw file after the new MP4 is saved.

Title cleanup: `core/titles.py` - `clean_title()` strips leading social-count noise that Facebook/Meta embeds in `og:title` (e.g. "1.2K views · 34 reactions · Actual Title" → "Actual Title").

---

## API Routes

| Method | Path | Auth | Behavior |
|---|---|---|---|
| `GET` | `/` | public | Render submit form |
| `GET` | `/submit` | public | Render submit form |
| `POST` | `/submit` | public | Derive `short_id` → if exists return it; else create, enqueue, return immediately. Sets `submitter_id` cookie. |
| `GET` | `/v/{short_id}` | public | `ready`/`reencoding` → render video.html; processing → render status.html; `error` → render error.html; `expired` → re-enqueue and show status |
| `GET` | `/v/{short_id}/status.json` | public | `{"status": "...", "error": null}` |
| `GET` | `/v/{short_id}/progress` | public | SSE stream; emits `{"stage": "...", "pct": 0-100 or null}` |
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

Features: paginated video table (50/page), search by title/URL/short_id, thumbnails, per-row actions (renew with expiry selector, delete file, unlist, re-encode, nuke). All actions use JS fetch - no page reloads. Status and expiry cells update in-place; a toast fades in with the result.

The "reencoded" column shows `yes`/`no`/`-` (dash for non-ready rows). The "re-encode" button only appears on `ready` rows where `transcoded=False`. Clicking it sets status to `reencoding` immediately in the UI and removes the button.

---

## SSE Progress (`web/progress.py`)

In-process dict mapping `short_id → {stage, pct}`. Workers call `set_progress(short_id, stage, pct)`. The `/v/{short_id}/progress` SSE endpoint polls this at 0.5 s intervals and streams events to the client. `pct=None` means indeterminate - the UI shows a CSS shimmer animation.

Stages in order: `pending → downloading → transcoding → ready`. During yt-dlp stream merge the stage stays `downloading` but `pct=None` (shimmer + "Merging…" label). During transcoding with unknown duration (some containers), `pct=None` (shimmer + "Transcoding…").

---

## Session Identity (`web/routers/submit.py`)

On every `POST /submit`, a `submitter_id` UUID4 is read from the request cookie or generated fresh, written to the `videos` row, and echoed back as a persistent cookie. This lets a future auth layer identify the original submitter without a full user system.

---

## Discord Bot (`bot/`)

Replies with a link Discord can unfurl when someone posts a URL it won't embed
playably. `docs/bot.md` covers the Discord-side setup.

**It runs inside the web app's process, not as its own service.** That is
forced, not stylistic: the video is held in RAM by `bot/store.py` and served by
`web/routers/ephemeral.py`, and two processes cannot share that memory. The
client is started from `web/main.py`'s lifespan when `DISCORD_BOT_TOKEN` is
set, and `bot.client.run_client` swallows everything but cancellation, so a bad
token or gateway failure degrades to "no bot" instead of taking the web app
down. Don't reintroduce a `gamtube-bot` unit; `deploy.sh` actively removes a
leftover one.

It still touches **nothing** of the web app's data: no `videos` row, no storage
backend, no expiry loop, no call to `/submit`. The only shared surface is the
route and the store.

**`/d/{key}`** renders the `og:video` page; **`/d/{key}/f`** serves the bytes
with `206` range support, because Discord's media proxy fetches by range.
`og:video:width`/`height` come from an ffprobe of the final file and are not
decorative - without them Discord sizes the player wrongly or falls back to a
plain card, which is the failure the bot exists to fix.

**Discord fetches the page and the video with two different services**, and
conflating them is the trap here. The unfurler reads `/d/{key}` and identifies
as `Discordbot`, so `bot/agents.py` can safely serve people a notice page
instead of `og:` tags (`HUMAN_NOTICE`, on by default - only a crawler needs the
metadata). The media proxy that fetches `/d/{key}/f` is something else, and its
user agent is **not known here**: gating the bytes on the crawler list would
block Discord's own fetcher and kill every embed. `MEDIA_USER_AGENTS` is a
separate list, empty by default, and every media fetch is logged with its agent
and address so it can be filled from evidence rather than guessed.

**The TTL is a bet.** `LINK_TTL_SECONDS` (300) assumes Discord keeps its own
copy once it has unfurled a link, so the link may then die. If Discord instead
proxies every view from the origin, expiry leaves a dead embed in channel
history. This was never measured - settle it by posting a link, waiting out the
TTL and trying to play the embed from another account, and raise the TTL if it
fails. Attachment upload would sidestep the question entirely but is capped by
the guild's `filesize_limit`, which is why it isn't the default.

**If the upload path is revisited**, the pieces are: read `guild.filesize_limit`
(it already accounts for boosts) and feed it to `_format_selector` in place of
`MAX_ITEM_MB`; send `discord.File(io.BytesIO(item.data), filename=...)` instead
of the link; and restore `core.titles.safe_filename`, removed in abe8a76, since
Discord shows the attachment filename above the player and a raw yt-dlp title
contains slashes, emoji and occasionally hundreds of characters. An upload needs
no store entry, no `/d/` route, no TTL and no warmup, so it is a shorter path
than the link one, not a longer one - the cap is its whole cost. Note
`filesize<?` in the format selector is yt-dlp's own field and unrelated to
Discord's limit; it keeps us from downloading something we would then reject.

**The reply is two messages, not one edit.** Download plus any re-encode can
run the better part of a minute, so `ACK_MODE` acknowledges immediately and the
work happens inside `channel.typing()`. When the link is ready it goes out as a
**new** message and the acknowledgement is withdrawn - editing a note to
contain the link would be tidier but relies on Discord unfurling an edited
message, which is not something to bet the whole feature on. A failure says so
rather than going quiet.

`ACK_MODE=reaction` acknowledges with an emoji on the original message instead
(default white check mark, red cross on failure). It needs Add Reactions on the
channel and falls back to a message without it. `ACK_KEEP_REACTION` leaves the
check in place afterwards, so it reads as "handled" rather than flashing on and
off - the link message is the real completion signal.

**Posting the link is what makes Discord fetch it**, so the player can only
ever arrive after the message: there is no way to pre-attach it, because **a
bot cannot set an embed's `video` field** (Discord's Create Message docs
exclude `provider`, `video` and image dimensions). Clients also sometimes don't
re-render until you revisit the channel, which looks like the bot being slow.

`WARMUP_ENABLED` (on by default) works around it by posting the link somewhere
throwaway first, waiting until Discord has pulled the whole file, pausing
`WARMUP_SETTLE_SECONDS`, deleting it, and only then posting publicly against
Discord's now-warm per-URL cache. With no `WARMUP_CHANNEL_ID` it warms against
**the application owner's DM**, which needs no configuration and is invisible
to the server - a bot may DM a user it shares a guild with, and the owner of a
bot in your own server qualifies. **The wait is on our own byte accounting, not
a timer** - `Item.record_served` / `store.is_complete`, fed by the media route
- because we serve the file and therefore know first-hand when Discord
finished. Every failure path (no channel, failed post, timeout) falls back to
posting unwarmed.

Two things this assumes and nobody has verified: that Discord's unfurl cache is
shared across channels, and that a settle pause is enough. The log shows
whether it worked - a warmed link should produce no second `page` fetch when it
goes out publicly.

`ACK_TEXT`/`ACK_FAILED_TEXT` defaults deliberately contain no `'`, `"` or `#`:
`.env` is read by systemd as an `EnvironmentFile`, which treats those as
quoting and comments.

**The trigger is the subtle part.** `on_message` fires before Discord has
unfurled anything, so the message delivered to the handler never has embeds
yet. The bot sleeps `EMBED_WAIT_SECONDS`, re-fetches the message, and only then
judges it. The predicate is **"no embed with a playable video"**, not "no
embeds" - TikTok and Instagram normally do produce an embed, just a dead
link-card. It is evaluated per link (`bot/detect.unembedded`), because a
message holding both a YouTube link and a TikTok link must still get the TikTok
re-hosted.

Links wrapped as `<https://...>` are skipped on purpose: that syntax is how a
Discord user suppresses an embed, so overriding it would be rude.

**Per-channel opt-in is Discord's permission system, not bot state**
(`bot/scope.py`). A channel is enabled when it carries an *explicit* permission
overwrite granting the bot `OPT_IN_PERMISSION` (default `embed_links`, which is
also the permission it needs for its reply to unfurl at all, so marker and
capability can't disagree). Discord stores it, so it survives restarts with no
file, DB or command to sync. Three things deliberately don't count: base role
permissions from the invite (not overwrites), an `@everyone` overwrite (it
would enable every channel at once), and category inheritance.

The documented setup gives the bot **no permissions of its own** - the install
link carries none. That is not the same as blind: a bot is a server member and
inherits `@everyone`, which usually allows view/send/history/embed, so it can
still see most channels. What gates it is that `opted_in` counts neither role
permissions nor `@everyone` overwrites, so the explicit per-channel grant is
the only thing that ever enables it. Genuine invisibility needs View Channels
*denied* to the bot's role at the category level; Discord has no per-bot
visibility switch. `scope.missing_capabilities()` reports a
half-ticked channel at startup, since Embed Links alone looks enabled but
cannot reply. Moving the first three into the install settings is a supported
fallback and needs no code change. Threads have no
overwrites of their own - discord.py doesn't even give `Thread` an
`overwrites_for` - so a thread follows its parent. `CHANNEL_OPT_IN=false` drops
the requirement.

**`gamtube` logging needs `web/logsetup.configure_logging()`.** Uvicorn
configures its own loggers and never touches the root, so without it an INFO
record from `gamtube.*` is dropped by `logging.lastResort` at WARNING -
`isEnabledFor(INFO)` is False and the record isn't even created. It is called
from `web/main.py` on startup; every bot log line depends on it, and nothing
else will tell you they're missing.

`BOT_DEBUG=true` is the tuning mode: full request headers on `/d/` (in case
Discord's media proxy sends something more reliable to gate on than a user
agent), per-stage timings, and polling the message during the wait so the real
embed-arrival delay is measured rather than assumed. It costs extra Discord API
calls, so it is off by default.

`bot/detect.py`, `bot/scope.py`, `bot/store.py`, `bot/ranges.py` and
`core/titles.py` are **pure and must stay that way** - they are the only parts
of this feature that can be exercised without a Discord token, and `tests/`
does exactly that. New link, filename, eviction or range logic goes there
rather than inside the discord.py handlers or the FastAPI route.

`EMBED_WAIT_SECONDS=3.0` is a guess, not a measurement. It needs tuning against
a real guild; too low and links that would have embedded get re-hosted anyway.

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
# bot (ignored by the web app; the web app's keys are ignored by the bot):
DISCORD_BOT_TOKEN=              # empty = bot service stays disabled
CHANNEL_OPT_IN=true             # channel must explicitly grant OPT_IN_PERMISSION
OPT_IN_PERMISSION=embed_links   # validated against discord.Permissions at startup
DISCORD_GUILDS=                 # comma-separated guild ids; empty = all
DISCORD_CHANNELS=               # comma-separated channel ids; empty = all
EMBED_WAIT_SECONDS=3.0          # how long to let Discord unfurl before judging
LINK_TTL_SECONDS=300            # how long a posted link works; see the TTL bet above
MAX_ITEM_MB=100                 # skip a single video bigger than this (held in RAM)
MAX_MEMORY_MB=512               # total store budget; oldest evicted first
FORCE_MP4=true                  # normalise to H.264 mp4 if the download isn't
WARMUP_ENABLED=true             # warm Discord's unfurl cache before posting publicly
WARMUP_CHANNEL_ID=0             # 0 = the app owner's DM; or a channel id in the guild
WARMUP_TIMEOUT_SECONDS=20.0
WARMUP_SETTLE_SECONDS=3.0
LINK_NOTE=(clicking this link...)  # appended under the link; empty for none
ACK_MODE=message                # message | reaction | none while downloading
BOT_DEBUG=false                 # header dumps, stage timings, embed-arrival polling
HUMAN_NOTICE=true               # non-crawlers get a notice page instead of og: tags
CRAWLER_USER_AGENTS=            # page gate; empty = bot/agents.py DEFAULT_CRAWLERS
MEDIA_USER_AGENTS=              # video gate; empty = anyone. NOT the crawler list
MAX_LINKS_PER_MESSAGE=3
SHRINK_ENABLED=true             # one re-encode attempt when a download is over the cap
SHRINK_CRF=30
SHRINK_MAX_HEIGHT=720
WATCH_DOMAINS=                  # comma-separated; empty = bot/detect.py DEFAULT_DOMAINS
BOT_TEMP_DIR=                   # a tmpfs path keeps downloads off disk
# S3 (ignored when STORAGE_BACKEND=local):
S3_BUCKET=
S3_REGION=us-east-1
S3_ACCESS_KEY=
S3_SECRET_KEY=
```

Both processes load the same `.env`, so **both `Settings` classes set
`"extra": "ignore"`**. Without it pydantic-settings rejects the file for
holding the other process's keys - adding a bot key would break the web app at
startup. Don't remove it.

`TRANSCODE_ENABLED=false` (default): yt-dlp output is moved directly to storage. YouTube typically delivers VP9/AV1 in webm; modern browsers play these natively. Individual videos can be re-encoded on demand from the admin panel.

`TRANSCODE_ENABLED=true`: every download is transcoded to H.264 MP4 via ffmpeg (`-preset superfast`) before being stored. Guarantees universal browser compatibility but adds significant CPU time.

---

## Video Expiry (`web/cleanup.py`)

Videos expire after `VIDEO_TTL_HOURS` hours (default 24). On expiry the **file is deleted** and `status` is set to `expired`, but the **DB row is kept** so the video can be re-fetched on demand.

**Cleanup loop** - started via FastAPI `lifespan` in `web/main.py`. Runs 30 s after startup, then every 10 minutes. Finds all `ready` rows where `expires_at <= now`, calls `storage.delete()`, and marks them `expired`.

**On-demand re-fetch** - when `GET /v/{short_id}` hits an `expired` row, `video_page` atomically flips status to `pending` and adds `process_video` to `BackgroundTasks`. The visitor sees the status/progress page while the video re-downloads.

---

## Running Locally

```bash
cp .env.example .env
make migrate
make dev
```

## Deploying (Ubuntu 24.04 LXC)

```bash
sudo bash deploy.sh --domain your.domain.tld
```

Installs `python3.12 + ffmpeg`, creates a `gamtube` system user, copies the app to `/opt/gamtube`, creates a venv, writes `.env`, runs migrations, and registers one systemd unit: `gamtube` (uvicorn `web.main:app`), which carries the bot inside it.

The bot is opt-in on the token alone - an empty `DISCORD_BOT_TOKEN` means
`web/main.py` never starts the client, and every bot code path is skipped. At
the prompt, `-` clears an existing token. A leftover `gamtube-bot.service` from
the earlier two-service layout is disabled and deleted on deploy.

**`make up` is the whole update, including a first-time bot setup.** It pulls,
runs `deploy.sh --keep`, and prints versions. Adding a setting in a future
version means adding it to the `EX_*` block, the defaults block and the `.env`
heredoc - and, if it is a decision rather than a tunable, an `_env_has` prompt
in the `--keep` branch. Tunables should just take their default silently;
nobody wants a questionnaire on every deploy. Re-running is safe - it rsyncs code, upgrades packages, runs any new migrations, and restarts the service.

Prompts for public base URL, data directory, admin password, transcoding, video TTL, and the Discord bot token. The bot's other settings are carried through from the existing `.env` rather than prompted, and defaulted on a first install. **Every prompt is pre-filled from the existing `/opt/gamtube/.env`, so pressing enter keeps the current value** - a re-run never silently resets config. The listen port is read back from the installed systemd unit for the same reason. `--keep` reuses every value already in `.env` without asking, but **asks once for a setting the existing `.env` has never seen** - and only when stdin is a terminal, so `make up` is safe unattended and over a pipe. An answer of blank is still an answer: it is written to `.env`, which is why `_env_has` exists (an empty value and a missing key must stay distinguishable) and why the question never comes back. `--domain`/`--port`/`--data-dir` override non-interactively, and `--bot-token`/`--no-bot` answer the bot question in scripts.

The base URL prompt takes a full URL including scheme, because `MEDIA_BASE_URL` is derived from it (`{BASE_URL}/media`) and an `http://` value on an HTTPS site gets every video blocked as mixed content. `--domain` only ever produces `http://`; type the URL at the prompt for HTTPS.

On an existing guest the whole update is `make up` (git pull --ff-only +
`deploy.sh --keep` + a version printout); `make status`, `make logs`,
`make bot-logs` and `make update-ytdlp` cover the rest. Set `DEPLOY_DIR=` if the app is not in `/opt/gamtube`.

`deploy.sh` also installs `gamtube-update-ytdlp.timer`: daily (randomised up to 2 h),
it runs `scripts/update-ytdlp.sh`, which pip-upgrades yt-dlp in the venv and restarts
`gamtube` only when the version actually moved. yt-dlp is imported as a library, so
nothing - web app or bot - picks up a new version without that restart. `requirements.txt` carries floors,
not pins, and the deploy installs with `--upgrade` so a re-run moves every dep forward.

```bash
systemctl status gamtube
systemctl list-timers gamtube-update-ytdlp.timer
tail -f /opt/gamtube/logs/app.log
tail -f /opt/gamtube/logs/app.log | grep gamtube.bot   # or: make bot-logs
```

---

## Verification

Nothing engine-bound here, so most of this repo runs locally. What is worth
knowing:

- `make test` runs everything that can run here: `tests/test_pure.py` (pure
  stdlib, no deps at all), `tests/test_routes.py` (the `/d/` routes through
  FastAPI's `TestClient`, skipping cleanly when fastapi isn't installed) and
  `tests/test_deploy.sh`.
- The pure modules listed under **Discord Bot** are testable with no token, no
  network and no venv. Keep new logic there rather than in the discord.py
  handlers, which cannot be exercised without a gateway connection.
- `pip install --target` into a scratch dir plus `PYTHONPATH` is enough to run
  the route tests without creating a venv (`python3 -m venv` has no ensurepip
  on this host).
- `deploy.sh` can be run end to end in a sandbox by pointing `DEPLOY_DIR` and
  the systemd path at temp directories and stubbing `apt-get`, `systemctl`,
  `useradd`, `rsync`, `chown`, `sudo` and `python3.12` on `PATH`. That catches
  the config-carry-through and unit-file regressions that are otherwise only
  visible on a live guest.
- discord.py is pip-installable locally, so API surface (`Embed.video`,
  `Intents.message_content`, `Thread.overwrites_for`, `Client.run` kwargs,
  `Permissions.VALID_FLAGS`) can be checked by introspection instead of from
  memory. Permission integers come from `Permissions(...).value` rather than
  being written out by hand.

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
