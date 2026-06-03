# TODO

## Expired media redirect

When a video's media file no longer exists (e.g. disk cleanup, manual deletion) and `GET /v/{short_id}` is hit, redirect the viewer to the original source URL instead of showing an error.

Changes:
- In `app/routers/videos.py`, for `status == "ready"` videos, check whether the media file actually exists on disk (or in the storage backend) before rendering `video.html`.
- If the file is missing, issue a `307 Temporary Redirect` to `video.source_url`.
- `LocalStorageBackend.get_local_path()` already returns the path — use that. For S3 (future), fall back to redirect unconditionally if `get_local_path()` returns `None`.
