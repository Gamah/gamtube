import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import get_settings, get_storage
from app.database import get_db
from app.models import Video
from app.progress import get_progress
from app.rendering import render
from app.schemas import StatusResponse
from app.storage.base import StorageBackend

router = APIRouter()


def _iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    s = dt.isoformat()
    return s if dt.tzinfo else s + "Z"


@router.get("/scroll")
async def scroll_feed(
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
):
    now = datetime.now(timezone.utc)
    videos = (
        db.query(Video)
        .filter(
            or_(Video.expires_at.is_(None), Video.expires_at > now),
            Video.status.in_(("ready", "reencoding")),
        )
        .order_by(func.random())
        .all()
    )
    settings = get_settings()
    base = settings.base_url.rstrip("/")
    items = []
    for v in videos:
        items.append({
            "short_id": v.short_id,
            "video_url": storage.get_url(v.video_path),
            "thumbnail_url": storage.get_url(v.thumbnail_url) if v.thumbnail_url else "",
            "source_url": v.source_url,
            "canonical_url": f"{base}/v/{v.short_id}",
        })
    return render("scroll.html", videos=items)


@router.get("/v/{short_id}")
async def video_page(
    short_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
):
    video = db.query(Video).filter(Video.short_id == short_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Not found")

    if video.status == "unlisted":
        thumbnail_url = storage.get_url(video.thumbnail_url) if video.thumbnail_url else None
        return render("unlisted.html", status_code=410, thumbnail_url=thumbnail_url or "")

    if video.status in ("ready", "reencoding"):
        video.view_count = (video.view_count or 0) + 1
        db.commit()
        video_url = storage.get_url(video.video_path)
        thumbnail_url = storage.get_url(video.thumbnail_url) if video.thumbnail_url else None
        settings = get_settings()
        canonical_url = f"{settings.base_url.rstrip('/')}/v/{video.short_id}"
        ext = (video.video_path or "").rsplit(".", 1)[-1].lower()
        video_mime = "video/mp4" if ext == "mp4" else "video/webm" if ext == "webm" else "video/mp4"
        return render(
            "video.html",
            video_url=video_url,
            source_url=video.source_url,
            short_id=video.short_id,
            expires_at=_iso_utc(video.expires_at),
            title=video.title or "",
            thumbnail_url=thumbnail_url or "",
            canonical_url=canonical_url,
            video_mime=video_mime,
        )

    if video.status == "expired":
        thumbnail_url = storage.get_url(video.thumbnail_url) if video.thumbnail_url else None
        return render("expired.html", short_id=short_id, thumbnail_url=thumbnail_url or "")

    if video.status == "error":
        return render("error.html", error=video.error_message)

    return render("status.html", status=video.status, short_id=short_id)


@router.post("/v/{short_id}/rerequest")
async def video_rerequest(
    short_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
):
    video = db.query(Video).filter(Video.short_id == short_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Not found")

    if video.status != "expired":
        return RedirectResponse(f"/v/{short_id}", status_code=303)

    updated = db.query(Video).filter(
        Video.short_id == short_id, Video.status == "expired"
    ).update({"status": "pending"})
    db.commit()
    if updated:
        from app.pipeline.worker import process_video
        background_tasks.add_task(process_video, short_id, video.source_url, storage)
    return RedirectResponse(f"/v/{short_id}", status_code=303)


@router.get("/v/{short_id}/status.json", response_model=StatusResponse)
async def video_status(short_id: str, db: Session = Depends(get_db)):
    video = db.query(Video).filter(Video.short_id == short_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Not found")
    return StatusResponse(status=video.status, error=video.error_message)


@router.get("/v/{short_id}/progress")
async def video_progress(
    short_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    video = db.query(Video).filter(Video.short_id == short_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Not found")

    initial_status = video.status

    async def event_stream():
        if initial_status in ("ready", "reencoding", "error"):
            payload = {"stage": "ready" if initial_status == "reencoding" else initial_status,
                       "pct": 100.0 if initial_status in ("ready", "reencoding") else None}
            yield f"data: {json.dumps(payload)}\n\n"
            return

        while True:
            if await request.is_disconnected():
                return

            p = get_progress(short_id)
            if p is not None:
                yield f"data: {json.dumps(p)}\n\n"
                if p.get("stage") in ("ready", "error"):
                    return
            else:
                yield f"data: {json.dumps({'stage': initial_status, 'pct': None})}\n\n"

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
