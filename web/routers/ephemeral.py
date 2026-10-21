"""`/d/{key}` — the short-lived links the Discord bot posts.

Backed by `bot.store.MemoryStore` on `app.state.bot_store`, so these routes
read from this process's memory. Nothing here touches the database, the
storage backend or `videos`; when the item expires the link 404s and that is
the whole lifecycle.

Three audiences, by user agent:

- a **link crawler** fetching the page gets the `og:` tags, which is the only
  thing that has to work for an embed to appear;
- a **person** who opens the link in a browser gets a short notice instead,
  because the metadata is no use to them and the video isn't meant for them;
- the **media route** serves bytes with `206` range support to whoever asks,
  unless `MEDIA_USER_AGENTS` lists who may.

That list is empty by default and is **not** the crawler list. Discord fetches
the page with its unfurler (`Discordbot`) and the video with its media proxy,
which is a different service; assuming they look alike would block Discord's
own fetcher and kill every embed. Every media fetch is logged with its agent
and address so the list can be filled from evidence.
"""

import logging

from fastapi import APIRouter, Header, Request, Response

from bot.ranges import parse_range
from bot.store import Item, MemoryStore
from web.config import get_settings
from web.rendering import render

log = logging.getLogger("gamtube.bot")

router = APIRouter(prefix="/d")


def get_settings_bot():
    # Imported lazily so `web` keeps working with the bot's deps absent.
    from bot.config import get_bot_settings

    return get_bot_settings()


def _lookup(request: Request, key: str) -> Item | None:
    store: MemoryStore | None = getattr(request.app.state, "bot_store", None)
    return store.get(key) if store is not None else None


def _is_crawler(request: Request) -> bool:
    from bot.agents import is_crawler

    return is_crawler(request.headers.get("user-agent"), get_settings_bot().crawlers())


@router.get("/{key}")
def discord_page(key: str, request: Request) -> Response:
    item = _lookup(request, key)
    if item is None:
        return render("link_gone.html", status_code=404)

    settings = get_settings_bot()
    if settings.human_notice and not _is_crawler(request):
        # No og: tags here — a person doesn't need them, and the video element
        # is left out so landing on the link doesn't start a download.
        return render("discord_human.html", source_url=item.source_url)

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
    agent = request.headers.get("user-agent") or "-"
    client = request.client.host if request.client else "-"
    # The only record of who actually fetches the video. Until this has been
    # read on a live instance, MEDIA_USER_AGENTS cannot be set responsibly.
    log.info("media %s %s range=%s ua=%r from %s",
             key, request.method, range_header or "-", agent, client)

    allowed = get_settings_bot().media_agents()
    if allowed and not any(a in agent.lower() for a in allowed):
        log.info("media %s blocked: ua=%r not in MEDIA_USER_AGENTS", key, agent)
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
