# TODO

## Show download/transcode progress to clients

While a video is processing, show the client real-time progress rather than a static status page with meta-refresh.

Ideas:
- SSE (Server-Sent Events) endpoint the status page connects to — zero dependencies, works in all browsers
- Worker emits progress events (yt-dlp download %, ffmpeg transcode %) into a shared store (in-memory dict for SQLite MVP, Redis for multi-worker)
- Status page upgrades from meta-refresh polling to a live progress bar
