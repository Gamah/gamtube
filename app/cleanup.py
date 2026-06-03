import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_INTERVAL = 600  # seconds between cleanup runs


async def cleanup_loop() -> None:
    await asyncio.sleep(30)  # let app finish starting
    while True:
        try:
            await asyncio.to_thread(_expire_videos)
        except Exception:
            logger.exception("Video cleanup error")
        await asyncio.sleep(_INTERVAL)


def _expire_videos() -> None:
    from app.config import get_settings, get_storage
    from app.database import SessionLocal
    from app.models import Video

    settings = get_settings()
    if settings.video_ttl_hours == 0:
        return

    db = SessionLocal()
    storage = get_storage()
    now = datetime.now(timezone.utc)

    try:
        expired = (
            db.query(Video)
            .filter(
                Video.status == "ready",
                Video.expires_at.isnot(None),
                Video.expires_at <= now,
            )
            .all()
        )
        for video in expired:
            if video.video_path:
                try:
                    storage.delete(video.video_path)
                except Exception:
                    logger.warning("Could not delete file for %s", video.short_id)
            db.query(Video).filter(Video.id == video.id).update(
                {"status": "expired", "video_path": None, "file_size_bytes": None}
            )
        if expired:
            db.commit()
            logger.info("Expired %d video(s)", len(expired))
    finally:
        db.close()
