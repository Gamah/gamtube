from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Feedback
from app.rendering import render

router = APIRouter()

_MAX_CHARS = 2000


@router.get("/feedback")
async def feedback_form(request: Request, vid: str = "", ref: str = ""):
    return render("feedback.html", vid=vid, ref=ref)


@router.post("/feedback")
async def submit_feedback(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    email = (body.get("email") or "").strip() or None
    message = (body.get("message") or "").strip()
    vid = (body.get("vid") or "").strip() or None
    ref = (body.get("ref") or "").strip() or None

    if not message:
        return JSONResponse({"ok": False, "error": "Message is required."}, status_code=400)
    if len(message) > _MAX_CHARS:
        return JSONResponse({"ok": False, "error": f"Message exceeds {_MAX_CHARS} characters."}, status_code=400)

    fb = Feedback(
        email=email,
        message=message,
        video_short_id=vid or None,
        page_url=ref or None,
    )
    db.add(fb)
    db.commit()
    return JSONResponse({"ok": True})
