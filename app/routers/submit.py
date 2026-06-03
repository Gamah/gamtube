import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings, get_storage
from app.database import get_db
from app.ids import short_id_for
from app.models import Video
from app.schemas import SubmitRequest
from app.storage.base import StorageBackend

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


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


@router.get("/", response_class=HTMLResponse)
@router.get("/submit", response_class=HTMLResponse)
async def submit_form(request: Request):
    return templates.TemplateResponse(request, "submit.html")


@router.post("/submit")
async def submit(
    body: SubmitRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
):
    from app.pipeline.worker import process_video

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
