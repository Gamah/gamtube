import shutil
import tempfile
from pathlib import Path

from app.config import get_settings
from app.database import SessionLocal
from app.models import Video
from app.pipeline.downloader import download
from app.pipeline.transcoder import transcode
from app.storage.base import StorageBackend


def process_video(short_id: str, url: str, storage: StorageBackend) -> None:
    settings = get_settings()
    tmp_dir = Path(tempfile.mkdtemp(dir=settings.temp_dir or None))
    db = SessionLocal()

    def upd(**kw):
        db.query(Video).filter(Video.short_id == short_id).update(kw)
        db.commit()

    try:
        upd(status="downloading")

        info, src = download(url, tmp_dir)
        upd(
            title=info.get("title"),
            description=info.get("description"),
            uploader=info.get("uploader"),
            duration_seconds=int(info["duration"]) if info.get("duration") else None,
            tags=info.get("tags"),
        )

        upd(status="transcoding")
        mp4 = tmp_dir / f"{short_id}.mp4"
        transcode(src, mp4)

        storage.save(mp4, f"{short_id}.mp4")

        local = storage.get_local_path(f"{short_id}.mp4")
        size = local.stat().st_size if local and local.exists() else 0

        upd(status="ready", video_path=f"{short_id}.mp4", file_size_bytes=size)

    except Exception as exc:
        db.query(Video).filter(Video.short_id == short_id).update(
            {"status": "error", "error_message": str(exc)}
        )
        db.commit()
    finally:
        db.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)
