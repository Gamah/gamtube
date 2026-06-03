from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import get_storage
from app.database import get_db
from app.models import Video
from app.rendering import render
from app.schemas import StatusResponse
from app.storage.base import StorageBackend

router = APIRouter()


@router.get("/v/{short_id}")
async def video_page(
    short_id: str,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
):
    video = db.query(Video).filter(Video.short_id == short_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Not found")

    if video.status == "ready":
        video_url = storage.get_url(video.video_path)
        return render("video.html", video_url=video_url)
    if video.status == "error":
        return render("error.html", error=video.error_message)
    return render("status.html", status=video.status, short_id=short_id)


@router.get("/v/{short_id}/status.json", response_model=StatusResponse)
async def video_status(short_id: str, db: Session = Depends(get_db)):
    video = db.query(Video).filter(Video.short_id == short_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Not found")
    return StatusResponse(status=video.status, error=video.error_message)
