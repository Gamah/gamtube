import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import get_settings
from app.database import SessionLocal
from app.models import Video
from app.pipeline.downloader import download
from app.pipeline.transcoder import transcode
from app.progress import set_progress
from app.storage.base import StorageBackend


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

        info, src = download(
            url, tmp_dir,
            on_progress=lambda pct: set_progress(short_id, "downloading", pct),
        )
        upd(
            title=info.get("title"),
            description=info.get("description"),
            uploader=info.get("uploader"),
            duration_seconds=int(info["duration"]) if info.get("duration") else None,
            tags=info.get("tags"),
        )

        set_progress(short_id, "transcoding", 0.0)
        upd(status="transcoding")
        mp4 = tmp_dir / f"{short_id}.mp4"
        transcode(
            src, mp4,
            on_progress=lambda pct: set_progress(short_id, "transcoding", pct),
        )

        storage.save(mp4, f"{short_id}.mp4")

        local = storage.get_local_path(f"{short_id}.mp4")
        size = local.stat().st_size if local and local.exists() else 0

        expires_at = None
        if settings.video_ttl_hours > 0:
            expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.video_ttl_hours)

        set_progress(short_id, "ready", 100.0)
        upd(status="ready", video_path=f"{short_id}.mp4", file_size_bytes=size, expires_at=expires_at)

    except Exception as exc:
        db.query(Video).filter(Video.short_id == short_id).update(
            {"status": "error", "error_message": str(exc)}
        )
        db.commit()
        set_progress(short_id, "error", None)
    finally:
        db.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)
