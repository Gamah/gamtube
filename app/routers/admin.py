import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings, get_storage
from app.database import get_db
from app.models import Video
from app.rendering import render
from app.storage.base import StorageBackend

router = APIRouter()

_COOKIE = "admin_token"


def _expected_token(password: str) -> str:
    return hmac.new(password.encode(), b"gamtube-admin-v1", hashlib.sha256).hexdigest()


def _is_authenticated(request: Request, password: str) -> bool:
    token = request.cookies.get(_COOKIE, "")
    if not token:
        return False
    return hmac.compare_digest(token, _expected_token(password))


def _check(request: Request):
    """For page routes: redirect to login if not authenticated."""
    settings = get_settings()
    if not settings.admin_password:
        raise HTTPException(status_code=403, detail="Admin panel not configured. Set ADMIN_PASSWORD in .env.")
    if not _is_authenticated(request, settings.admin_password):
        return RedirectResponse("/manage/login", status_code=302)
    return None


def _check_json(request: Request):
    """For fetch action routes: return JSON error if not authenticated."""
    settings = get_settings()
    if not settings.admin_password:
        return JSONResponse({"ok": False, "msg": "admin not configured"}, status_code=403)
    if not _is_authenticated(request, settings.admin_password):
        return JSONResponse({"ok": False, "msg": "not authenticated"}, status_code=401)
    return None


def _fmt_size(b: int | None) -> str:
    if b is None:
        return "—"
    if b >= 1_073_741_824:
        return f"{b / 1_073_741_824:.1f} GB"
    if b >= 1_048_576:
        return f"{b / 1_048_576:.1f} MB"
    if b >= 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b} B"


def _fmt_expires(dt: datetime | None) -> str:
    if dt is None:
        return "permanent"
    now = datetime.now(timezone.utc)
    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    diff = aware - now
    if diff.total_seconds() <= 0:
        return "expired"
    total = int(diff.total_seconds())
    years, total  = divmod(total, 365 * 24 * 3600)
    months, total = divmod(total, 30 * 24 * 3600)
    days, total   = divmod(total, 24 * 3600)
    hours, total  = divmod(total, 3600)
    minutes       = total // 60
    parts = []
    if years:   parts.append(f"{years}y")
    if months:  parts.append(f"{months}mo")
    if days:    parts.append(f"{days}d")
    if hours:   parts.append(f"{hours}h")
    if minutes or not parts: parts.append(f"{minutes}m")
    return " ".join(parts)


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M")


# ── Login ─────────────────────────────────────────────────────────────────────

@router.get("/manage/login")
async def login_form(request: Request):
    settings = get_settings()
    if not settings.admin_password:
        raise HTTPException(status_code=403, detail="Admin panel not configured.")
    if _is_authenticated(request, settings.admin_password):
        return RedirectResponse("/manage", status_code=302)
    return render("manage_login.html", error=None)


@router.post("/manage/login")
async def login(request: Request, password: str = Form(...)):
    settings = get_settings()
    if not settings.admin_password:
        raise HTTPException(status_code=403)
    if not hmac.compare_digest(password, settings.admin_password):
        return render("manage_login.html", error="Wrong password.")
    token = _expected_token(settings.admin_password)
    resp = RedirectResponse("/manage", status_code=302)
    resp.set_cookie(_COOKIE, token, httponly=True, samesite="lax", max_age=86400 * 30)
    return resp


@router.get("/manage/logout")
async def logout():
    resp = RedirectResponse("/manage/login", status_code=302)
    resp.delete_cookie(_COOKIE)
    return resp


# ── Main panel ────────────────────────────────────────────────────────────────

_PAGE_SIZE = 50


@router.get("/manage")
async def manage(
    request: Request,
    q: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
):
    if (redir := _check(request)):
        return redir

    query = db.query(Video).order_by(Video.created_at.desc())
    if q:
        like = f"%{q}%"
        query = query.filter(
            Video.title.ilike(like) | Video.source_url.ilike(like) | Video.short_id.ilike(like)
        )

    total = query.count()
    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(1, min(page, total_pages))
    videos = query.offset((page - 1) * _PAGE_SIZE).limit(_PAGE_SIZE).all()

    rows = [
        {
            "short_id": v.short_id,
            "source_url": v.source_url,
            "status": v.status,
            "transcoded": v.transcoded,
            "can_reencode": v.status == "ready" and v.transcoded == "no",
            "file_size": _fmt_size(v.file_size_bytes),
            "view_count": v.view_count,
            "created_at": _fmt_dt(v.created_at),
            "expires_at": _fmt_expires(v.expires_at),
            "title": v.title or "",
            "thumbnail_url": storage.get_url(v.thumbnail_url) if v.thumbnail_url else "",
        }
        for v in videos
    ]
    return render("manage.html", rows=rows, q=q, page=page, total_pages=total_pages, total=total)


# ── Actions (JSON, called via fetch) ─────────────────────────────────────────

@router.post("/manage/v/{short_id}/renew")
async def renew(
    short_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    hours: int = Form(...),
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
):
    if (err := _check_json(request)):
        return err
    video = db.query(Video).filter(Video.short_id == short_id).first()
    if not video:
        return JSONResponse({"ok": False, "msg": "not found"}, status_code=404)
    if hours == 0:
        video.expires_at = None
    else:
        video.expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)

    from app.pipeline.worker import process_video, refresh_metadata
    if video.status in ("expired", "error") or not video.video_path:
        # Re-download the full video; process_video will set its own expires_at
        video.status = "pending"
        db.commit()
        background_tasks.add_task(process_video, video.short_id, video.source_url, storage)
    else:
        db.commit()
        background_tasks.add_task(refresh_metadata, video.short_id, video.source_url, storage)

    return JSONResponse({"ok": True, "msg": "renewing…", "expires": _fmt_expires(video.expires_at), "status": video.status})


@router.post("/manage/v/{short_id}/delete")
async def delete_video(
    short_id: str,
    request: Request,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
):
    if (err := _check_json(request)):
        return err
    video = db.query(Video).filter(Video.short_id == short_id).first()
    if not video:
        return JSONResponse({"ok": False, "msg": "not found"}, status_code=404)
    if video.video_path:
        try:
            storage.delete(video.video_path)
        except Exception:
            pass
    video.status = "expired"
    video.video_path = None
    video.file_size_bytes = None
    db.commit()
    return JSONResponse({"ok": True, "msg": "file deleted"})


@router.post("/manage/v/{short_id}/unlist")
async def unlist_video(
    short_id: str,
    request: Request,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
):
    if (err := _check_json(request)):
        return err
    video = db.query(Video).filter(Video.short_id == short_id).first()
    if not video:
        return JSONResponse({"ok": False, "msg": "not found"}, status_code=404)
    if video.video_path:
        try:
            storage.delete(video.video_path)
        except Exception:
            pass
    video.status = "unlisted"
    video.video_path = None
    video.file_size_bytes = None
    video.expires_at = None
    db.commit()
    return JSONResponse({"ok": True, "msg": "unlisted"})


@router.post("/manage/v/{short_id}/reencode")
async def reencode(
    short_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
):
    if (err := _check_json(request)):
        return err
    video = db.query(Video).filter(Video.short_id == short_id).first()
    if not video:
        return JSONResponse({"ok": False, "msg": "not found"}, status_code=404)
    if not video.video_path:
        return JSONResponse({"ok": False, "msg": "no file to re-encode"}, status_code=400)
    video.status = "reencoding"
    db.commit()
    from app.pipeline.worker import reencode_video
    background_tasks.add_task(reencode_video, video.short_id, storage)
    return JSONResponse({"ok": True, "msg": "re-encoding…", "status": "reencoding"})


@router.post("/manage/v/{short_id}/nuke")
async def nuke_video(
    short_id: str,
    request: Request,
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
):
    if (err := _check_json(request)):
        return err
    video = db.query(Video).filter(Video.short_id == short_id).first()
    if not video:
        return JSONResponse({"ok": False, "msg": "not found"}, status_code=404)
    for key in (video.video_path, video.thumbnail_url):
        if key:
            try:
                storage.delete(key)
            except Exception:
                pass
    db.delete(video)
    db.commit()
    return JSONResponse({"ok": True, "msg": "nuked"})
