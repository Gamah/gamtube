import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from web.cleanup import cleanup_loop
from web.config import get_settings
from web.routers import admin, ephemeral, feedback, submit, videos

log = logging.getLogger("gamtube")


def _start_bot(app: FastAPI) -> asyncio.Task | None:
    """Start the Discord client in this process, if a token is configured.

    It runs here rather than as its own service because the videos it posts
    live in this process's memory and are served by `web/routers/ephemeral.py`
    — two processes could not share that. `bot.client.run_client` swallows its
    own failures, so a bot problem never takes the web app down.
    """
    from bot.config import get_bot_settings
    from bot.store import MemoryStore

    settings = get_bot_settings()
    if not settings.discord_bot_token:
        return None

    from bot.client import run_client

    app.state.bot_store = MemoryStore(
        ttl_seconds=settings.link_ttl_seconds,
        max_bytes=settings.max_memory_mb * 1024 * 1024,
    )
    log.info(
        "starting Discord bot; links held %ds, up to %dMB in memory",
        settings.link_ttl_seconds, settings.max_memory_mb,
    )
    return asyncio.create_task(
        run_client(settings, app.state.bot_store, get_settings().base_url)
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(cleanup_loop())]
    bot_task = _start_bot(app)
    if bot_task is not None:
        tasks.append(bot_task)
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.state.bot_store = None
    app.include_router(submit.router)
    app.include_router(videos.router)
    app.include_router(admin.router)
    app.include_router(feedback.router)
    app.include_router(ephemeral.router)

    settings = get_settings()
    media_path = Path(settings.media_root)
    media_path.mkdir(parents=True, exist_ok=True)

    app.mount("/media", StaticFiles(directory=str(media_path)), name="media")
    app.mount("/static", StaticFiles(directory="static"), name="static")

    return app


app = create_app()
