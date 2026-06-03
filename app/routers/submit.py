from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_api_key
from app.config import get_settings, get_storage
from app.database import get_db
from app.ids import short_id_for
from app.models import Video
from app.schemas import SubmitRequest, SubmitResponse
from app.storage.base import StorageBackend

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
@router.get("/submit", response_class=HTMLResponse)
async def submit_form(request: Request):
    return templates.TemplateResponse("submit.html", {"request": request})


@router.post("/submit", response_model=SubmitResponse)
async def submit(
    body: SubmitRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: str = Depends(require_api_key),
    storage: StorageBackend = Depends(get_storage),
):
    from app.pipeline.worker import process_video

    sid = short_id_for(body.url)
    settings = get_settings()

    existing = db.query(Video).filter(Video.short_id == sid).first()
    if existing:
        return SubmitResponse(short_id=sid, url=f"{settings.base_url}/v/{sid}")

    video = Video(short_id=sid, source_url=body.url, status="pending")
    db.add(video)
    db.commit()

    background_tasks.add_task(process_video, sid, body.url, storage)

    return SubmitResponse(short_id=sid, url=f"{settings.base_url}/v/{sid}")
