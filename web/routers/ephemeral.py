"""`/d/{key}` — the short-lived links the Discord bot posts.

Backed by `bot.store.MemoryStore` on `app.state.bot_store`, so these routes
read from this process's memory. Nothing here touches the database, the
storage backend or `videos`; when the item expires the link 404s and that is
the whole lifecycle.

The two routes are split on purpose: Discord unfurls the **page** to read its
`og:` tags, then fetches the **media** URL with byte-range requests to build
the player.
"""

from fastapi import APIRouter, Header, Request, Response

from bot.ranges import parse_range
from bot.store import Item, MemoryStore
from web.config import get_settings
from web.rendering import render

router = APIRouter(prefix="/d")


def _lookup(request: Request, key: str) -> Item | None:
    store: MemoryStore | None = getattr(request.app.state, "bot_store", None)
    return store.get(key) if store is not None else None


def _blocked(request: Request) -> bool:
    """Optional user-agent gate.

    `SERVE_USER_AGENTS` is a comma-separated list of substrings; when set, only
    matching clients get the bytes. It is a heuristic — Discord's proxy
    identifies itself, but a human clicking the link does not, so leaving this
    empty (the default) is the safe choice.
    """
    allowed = [s.strip().lower() for s in get_settings_bot().serve_user_agents.split(",")]
    allowed = [s for s in allowed if s]
    if not allowed:
        return False
    ua = (request.headers.get("user-agent") or "").lower()
    return not any(s in ua for s in allowed)


def get_settings_bot():
    # Imported lazily so `web` keeps working with the bot's deps absent.
    from bot.config import get_bot_settings

    return get_bot_settings()


@router.get("/{key}")
def discord_page(key: str, request: Request) -> Response:
    item = _lookup(request, key)
    if item is None:
        return render("link_gone.html", status_code=404)

    base = get_settings().base_url.rstrip("/")
    return render(
        "discord.html",
        canonical_url=f"{base}/d/{key}",
        video_url=f"{base}/d/{key}/f",
        video_mime=item.content_type,
        title=item.title,
        width=item.width,
        height=item.height,
        duration=item.duration,
    )


@router.api_route("/{key}/f", methods=["GET", "HEAD"])
def discord_media(
    key: str,
    request: Request,
    range_header: str | None = Header(None, alias="range"),
) -> Response:
    item = _lookup(request, key)
    if item is None:
        return Response(status_code=404)
    if _blocked(request):
        return Response(status_code=403)

    size = item.size
    common = {"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=300"}

    try:
        rng = parse_range(range_header, size)
    except ValueError:
        return Response(
            status_code=416, headers={**common, "Content-Range": f"bytes */{size}"}
        )

    if request.method == "HEAD":
        return Response(
            status_code=200,
            media_type=item.content_type,
            headers={**common, "Content-Length": str(size)},
        )

    if rng is None:
        return Response(content=item.data, media_type=item.content_type, headers=common)

    start, end = rng
    return Response(
        content=item.data[start : end + 1],
        status_code=206,
        media_type=item.content_type,
        headers={**common, "Content-Range": f"bytes {start}-{end}/{size}"},
    )
