import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from web.config import get_settings, get_storage
from web.database import get_db
from core.ids import short_id_for
from web.models import Video
from web.rendering import render
from web.schemas import SubmitRequest
from web.storage.base import StorageBackend

router = APIRouter()


def _get_or_create_submitter_id(request: Request) -> str:
    raw = request.cookies.get("submitter_id", "")
    if raw:
        try:
            val = uuid.UUID(raw)
            if val.version == 4:
                return raw
        except ValueError:
            pass
    return str(uuid.uuid4())


@router.get("/")
@router.get("/submit")
async def submit_form():
    return render("submit.html")


@router.post("/submit")
async def submit(
    body: SubmitRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
):
    from web.worker import process_video

    sid = short_id_for(body.url)
    settings = get_settings()
    submitter_id = _get_or_create_submitter_id(request)

    existing = db.query(Video).filter(Video.short_id == sid).first()
    if not existing:
        video = Video(short_id=sid, source_url=body.url, status="pending", submitter_id=submitter_id)
        db.add(video)
        db.commit()
        background_tasks.add_task(process_video, sid, body.url, storage)

    response = JSONResponse(content={"short_id": sid, "url": f"{settings.base_url}/v/{sid}"})
    response.set_cookie("submitter_id", submitter_id, max_age=31536000, httponly=True, samesite="lax")
    return response
