import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import get_storage
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
        from fastapi.responses import HTMLResponse
        return HTMLResponse(
            "<html><body><p>This video is no longer available. You know what you did.</p></body></html>",
            status_code=410,
        )

    if video.status in ("ready", "reencoding"):
        video.view_count = (video.view_count or 0) + 1
        db.commit()
        video_url = storage.get_url(video.video_path)
        return render(
            "video.html",
            video_url=video_url,
            source_url=video.source_url,
            expires_at=_iso_utc(video.expires_at),
        )

    if video.status == "expired":
        # Re-trigger processing; guard against concurrent re-triggers
        updated = db.query(Video).filter(
            Video.short_id == short_id, Video.status == "expired"
        ).update({"status": "pending"})
        db.commit()
        if updated:
            from app.pipeline.worker import process_video
            background_tasks.add_task(process_video, video.short_id, video.source_url, storage)
        return render("status.html", status="pending", short_id=short_id)

    if video.status == "error":
        return render("error.html", error=video.error_message)

    return render("status.html", status=video.status, short_id=short_id)


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
