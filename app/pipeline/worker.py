import re
import shutil
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import get_settings
from app.database import SessionLocal
from app.models import Video
from app.pipeline.downloader import download
from app.pipeline.transcoder import _probe, transcode
from app.progress import set_progress
from app.storage.base import StorageBackend

# Strip leading social-count noise Facebook puts before the actual title,
# e.g. "1,234 views · 567 reactions · Real Title" → "Real Title"
_SOCIAL_PREFIX = re.compile(
    r'^(?:[\d,]+(?:\.\d+)?[KMB]?\s+(?:views?|reactions?|likes?|comments?|shares?)\s*[·•|,]\s*)+',
    re.IGNORECASE,
)


def _clean_title(title: str | None) -> str | None:
    if not title:
        return title
    cleaned = _SOCIAL_PREFIX.sub('', title).strip(' ·|,•')
    return cleaned if cleaned else title


def refresh_metadata(short_id: str, url: str, storage: StorageBackend) -> None:
    """Re-fetch title and thumbnail without touching the video file.
    Silently no-ops if the source is gone or unreachable."""
    import yt_dlp
    from app.pipeline.downloader import _IMAGE_EXTS

    settings = get_settings()
    tmp_dir = Path(tempfile.mkdtemp(dir=settings.temp_dir or None))
    db = SessionLocal()
    try:
        ydl_opts = {
            "skip_download": True,
            "writethumbnail": True,
            "outtmpl": str(tmp_dir / "video.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        thumb_files = [
            f for f in tmp_dir.iterdir()
            if f.is_file() and f.suffix.lower() in _IMAGE_EXTS
        ]

        update = {}
        if title := _clean_title(info.get("title")):
            update["title"] = title
        for field in ("description", "uploader", "tags"):
            if info.get(field):
                update[field] = info[field]
        if info.get("duration"):
            update["duration_seconds"] = int(info["duration"])

        if thumb_files:
            video = db.query(Video).filter(Video.short_id == short_id).first()
            if video and video.thumbnail_url:
                try:
                    storage.delete(video.thumbnail_url)
                except Exception:
                    pass
            thumb = thumb_files[0]
            key = f"{short_id}{thumb.suffix}"
            storage.save(thumb, key)
            update["thumbnail_url"] = key

        if update:
            db.query(Video).filter(Video.short_id == short_id).update(update)
            db.commit()
    except Exception:
        pass  # source gone or unavailable — keep existing data
    finally:
        db.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def process_video(short_id: str, url: str, storage: StorageBackend) -> None:
    settings = get_settings()
    tmp_dir = Path(tempfile.mkdtemp(dir=settings.temp_dir or None))
    db = SessionLocal()

    def upd(**kw):
        db.query(Video).filter(Video.short_id == short_id).update(kw)
        db.commit()

    try:
        set_progress(short_id, "downloading", 0.0)
        upd(status="downloading")

        info, src, thumb_path = download(
            url, tmp_dir,
            on_progress=lambda pct: set_progress(short_id, "downloading", pct),
        )

        thumb_key = None
        if thumb_path and thumb_path.exists():
            thumb_key = f"{short_id}{thumb_path.suffix}"
            storage.save(thumb_path, thumb_key)

        upd(
            title=_clean_title(info.get("title")),
            description=info.get("description"),
            uploader=info.get("uploader"),
            duration_seconds=int(info["duration"]) if info.get("duration") else None,
            tags=info.get("tags"),
            thumbnail_url=thumb_key,
        )

        video_key = f"{short_id}{src.suffix}"
        storage.save(src, video_key)

        local = storage.get_local_path(video_key)
        size = local.stat().st_size if local and local.exists() else 0

        expires_at = None
        if settings.video_ttl_hours > 0:
            expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.video_ttl_hours)

        set_progress(short_id, "ready", 100.0)
        upd(status="ready", video_path=video_key, file_size_bytes=size,
            expires_at=expires_at, transcoded="no")

        if settings.transcode_enabled:
            threading.Thread(
                target=reencode_video, args=(short_id, storage), daemon=True
            ).start()

    except Exception as exc:
        db.query(Video).filter(Video.short_id == short_id).update(
            {"status": "error", "error_message": str(exc)}
        )
        db.commit()
        set_progress(short_id, "error", None)
    finally:
        db.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def reencode_video(short_id: str, storage: StorageBackend) -> None:
    settings = get_settings()
    db = SessionLocal()

    def upd(**kw):
        db.query(Video).filter(Video.short_id == short_id).update(kw)
        db.commit()

    try:
        video = db.query(Video).filter(Video.short_id == short_id).first()
        if not video or not video.video_path:
            return

        if video.transcoded == "skipped":
            return  # already established that raw is smaller; don't re-attempt

        local_path = storage.get_local_path(video.video_path)
        if not local_path or not local_path.exists():
            upd(status="error", error_message="source file missing; cannot re-encode")
            set_progress(short_id, "error", None)
            return

        # Codecs that consistently beat H.264 in file size — transcoding will bloat the file
        probe = _probe(local_path)
        src_codec = next(
            (s.get("codec_name") for s in probe.get("streams", []) if s.get("codec_type") == "video"),
            None,
        )
        if src_codec in ("vp9", "av1", "hevc"):
            upd(transcoded="skipped")
            return

        old_key = video.video_path
        original_size = video.file_size_bytes or 0
        set_progress(short_id, "reencoding", 0.0)
        upd(status="reencoding")

        tmp_dir = Path(tempfile.mkdtemp(dir=settings.temp_dir or None))
        try:
            mp4 = tmp_dir / f"{short_id}.mp4"
            transcode(
                local_path, mp4,
                on_progress=lambda pct: set_progress(short_id, "transcoding", pct),
            )

            new_key = f"{short_id}.mp4"
            mp4_size = mp4.stat().st_size

            if original_size and mp4_size >= original_size:
                # Transcoded result is larger — keep the original file, mark skipped
                set_progress(short_id, "ready", 100.0)
                upd(status="ready", transcoded="skipped")
                return

            storage.save(mp4, new_key)

            if old_key != new_key:
                try:
                    storage.delete(old_key)
                except Exception:
                    pass

            set_progress(short_id, "ready", 100.0)
            upd(status="ready", video_path=new_key, file_size_bytes=mp4_size, transcoded="yes")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    except Exception as exc:
        try:
            upd(status="error", error_message=str(exc))
            set_progress(short_id, "error", None)
        except Exception:
            pass
    finally:
        db.close()
